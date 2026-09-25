"""Bot API endpoints (docs/SPEC.md section 4; contract in API.md).

Every endpoint is POST-only, HMAC-signed and always answers JSON -- also for
auth failures and for unexpected exceptions (500 ``internal_error``). The
functions' names must match ``auth_hooks.PUBLIC_VIEWS``; :func:`api_endpoint`
keeps them via ``functools.wraps`` so ``URLPattern.lookup_str`` stays
``qqbot.api.views.<name>``.

All data changes go through :mod:`qqbot.core`.
"""

import json
from functools import wraps

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from allianceauth.services.hooks import get_extension_logger

from .. import __version__, checks
from ..core import bindings, eligibility, roster
from ..core import events as core_events
from ..core.util import mask_qq
from ..models import QQGroup, normalize_qq
from . import signing
from .signing import ApiError, error

logger = get_extension_logger(__name__)

MAX_CHECK_QQS = 3000
EVENTS_DEFAULT_LIMIT = 200
EVENTS_MAX_LIMIT = 500
MAX_CLAIM_TEXT = 2000

# Extra reason used only by the API for numbers that are not valid QQs.
BAD_QQ = "BAD_QQ"


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------


def _json(payload: dict, status: int = 200, headers=None) -> JsonResponse:
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    for name, value in (headers or {}).items():
        response[name] = value
    return response


def _ok(**data) -> JsonResponse:
    return _json({"ok": True, "server_time": timezone.now().isoformat(), **data})


def _error_response(exc: ApiError) -> JsonResponse:
    return _json(
        {"ok": False, "error": exc.code, "message": exc.message}, exc.status, exc.headers
    )


def bad_request(message: str) -> ApiError:
    return error(400, "bad_request", message)


def _parse_json(body: bytes) -> dict:
    if not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise bad_request("请求体不是合法的 JSON（需要 UTF-8 编码）。") from None
    if not isinstance(data, dict):
        raise bad_request("请求体必须是一个 JSON 对象（{...}）。")
    return data


def api_endpoint(view):
    """Method check, signature auth, JSON parsing and error handling.

    The wrapped view is called as ``view(request, data)`` with the parsed
    JSON object. Whatever goes wrong, the answer is JSON.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            if request.method != "POST":
                raise error(405, "method_not_allowed", headers={"Allow": "POST"})
            try:
                body = request.body
            except RequestDataTooBig:
                # Django's own DATA_UPLOAD_MAX_MEMORY_SIZE cap (2.5 MB by
                # default) is hit before the body can even be read.
                raise error(413, "too_large") from None
            # The authenticated key id, for views that want to log it.
            request.qqbot_api_key = signing.authenticate(request, body)
            data = _parse_json(body)
            return view(request, data, *args, **kwargs)
        except ApiError as exc:
            if exc.status in (401, 413, 429, 503):
                logger.warning(
                    "qqbot: API %s refused (%s) from %s, key %r",
                    view.__name__,
                    exc.code,
                    request.META.get("REMOTE_ADDR", "?"),
                    request.headers.get(signing.HEADER_KEY, "")[:64],
                )
            else:
                logger.info("qqbot: API %s answered %s: %s", view.__name__, exc.code, exc.message)
            return _error_response(exc)
        except Exception:
            logger.exception("qqbot: API %s failed", view.__name__)
            return _error_response(error(500, "internal_error"))

    return csrf_exempt(wrapper)


# --------------------------------------------------------------------------
# field validation
# --------------------------------------------------------------------------


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _group_id(value) -> str:
    gid = normalize_qq(value) if isinstance(value, str) or _is_int(value) else ""
    if not gid:
        raise bad_request("group_id 必须是群号（5–11 位数字，字符串或整数）。")
    return gid


def _active_group(value) -> QQGroup:
    group = QQGroup.objects.filter(group_id=_group_id(value), is_active=True).first()
    if group is None:
        raise error(404, "unknown_group")
    return group


def _decision(d: eligibility.Decision) -> dict:
    return {"qq": d.qq, "decision": d.decision, "reason": d.reason, "card": d.card}


# --------------------------------------------------------------------------
# endpoints (names must match auth_hooks.PUBLIC_VIEWS)
# --------------------------------------------------------------------------


@api_endpoint
def health(request, data):
    problems = list(checks.problems())
    return _ok(
        version=__version__,
        config_ok=not any(".E" in p for p in problems),
        problems=problems,
    )


@api_endpoint
def groups(request, data):
    return _ok(
        groups=[
            {"group_id": g.group_id, "name": g.name, "kind": g.kind}
            for g in eligibility.active_groups()
        ]
    )


@api_endpoint
def check(request, data):
    if "group_id" not in data:
        raise bad_request("缺少 group_id。")
    qqs = data.get("qqs")
    if not isinstance(qqs, list):
        raise bad_request("qqs 必须是 QQ 号的列表。")
    if len(qqs) > MAX_CHECK_QQS:
        raise bad_request(f"qqs 一次最多 {MAX_CHECK_QQS} 个。")
    if any(not (isinstance(q, str) or _is_int(q)) for q in qqs):
        raise bad_request("qqs 里的每一项必须是字符串或整数。")
    full_roster = data.get("full_roster", False)
    if not isinstance(full_roster, bool):
        raise bad_request("full_roster 必须是 true 或 false。")
    group = _active_group(data["group_id"])

    normalized = [normalize_qq(q) for q in qqs]
    valid = [n for n in normalized if n]
    if full_roster and not valid:
        # An empty "complete" list would wipe the roster; almost surely a bug.
        raise bad_request("full_roster=true 时 qqs 必须是该群完整的成员名单，不能为空。")

    roster_result = None
    if full_roster:
        roster_result = roster.update_roster(group, valid)

    decisions = eligibility.evaluate(group, valid)
    results = []
    for original, n in zip(qqs, normalized):
        if n and n in decisions:
            results.append(_decision(decisions[n]))
        else:
            results.append(
                {"qq": original, "decision": eligibility.REVIEW, "reason": BAD_QQ, "card": None}
            )
    logger.info(
        "qqbot: check group %s: %d QQs (%d invalid)%s",
        group.group_id,
        len(qqs),
        len(qqs) - len(valid),
        " with full roster" if full_roster else "",
    )
    return _ok(group_id=group.group_id, results=results, roster=roster_result)


@api_endpoint
def claim(request, data):
    qq = data.get("qq")
    qq_n = normalize_qq(qq) if isinstance(qq, str) or _is_int(qq) else ""
    if not qq_n:
        raise bad_request("qq 必须是 5–11 位数字的 QQ 号。")
    text = data.get("text")
    if not isinstance(text, str):
        raise bad_request("text 必须是字符串（入群申请的验证信息原文）。")
    if len(text) > MAX_CLAIM_TEXT:
        raise bad_request(f"text 最长 {MAX_CLAIM_TEXT} 个字符。")
    group = None
    if data.get("group_id") is not None:
        group = _active_group(data["group_id"])

    result = bindings.claim(qq_n, text)
    decision = None
    if group is not None:
        decision = _decision(eligibility.evaluate(group, [qq_n])[qq_n])
    logger.info("qqbot: claim by %s: %s", mask_qq(qq_n), result.outcome)
    return _ok(
        claimed=bool(result.ok and result.outcome == "claimed"),
        outcome=result.outcome,
        message=result.message,
        result=decision,
    )


@api_endpoint
def events(request, data):
    after = data.get("after")
    if not _is_int(after) or after < 0:
        raise bad_request("after 必须是 ≥ 0 的整数（上次返回的 last_id，第一次用 0）。")
    limit = data.get("limit", EVENTS_DEFAULT_LIMIT)
    if not _is_int(limit) or not 1 <= limit <= EVENTS_MAX_LIMIT:
        raise bad_request(f"limit 必须是 1–{EVENTS_MAX_LIMIT} 的整数。")
    page = core_events.poll(after, limit)
    return _ok(
        events=[
            {"id": e.id, "kind": e.kind, "qq": e.qq, "created_at": e.created_at.isoformat()}
            for e in page.events
        ],
        last_id=page.last_id,
        has_more=page.has_more,
    )
