"""Member side: the "QQ 绑定" card on AA's services page (DECISIONS.md #18,
docs/SPEC.md section 5, DESIGN.md 4.1 / 5).

Members do everything inside that card; there is no separate member page.
:func:`card_context` collects what the card shows, the POST views below
change data (always through ``qqbot.core``), turn the core result into a
Django message and go back to the card (``/services/#qqbot``), where AA's
base template shows the message.
"""

import hmac
import math
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from allianceauth.services.hooks import get_extension_logger

from ..core import bindings, cards, codes, eligibility
from ..core.access import has_main_character
from ..core.util import NICKNAME_MAX_LENGTH, mask_qq
from ..models import Binding, BindCode, Config, QQGroup, normalize_qq
from .member_forms import NICKNAME_HELP, NicknameForm, SubmitForm, UnbindForm, first_error

logger = get_extension_logger(__name__)

BASIC_ACCESS = "qqbot.basic_access"
MANAGE = "qqbot.manage"
SESSION_KEY = "qqbot_code"
CARD_ANCHOR = "qqbot"  # id of the card on the services page

# Binding states shown to the member.
STATE_VERIFIED = bindings.STATE_VERIFIED
STATE_TRUSTED = bindings.STATE_TRUSTED
STATE_CONFLICT = bindings.STATE_CONFLICT
STATE_TAKEN = bindings.STATE_TAKEN

STATE_LABELS = {
    STATE_VERIFIED: "已验证",
    STATE_TRUSTED: "老成员免验证",
    STATE_CONFLICT: "冲突 - 请联系 QQ 管理员",
    STATE_TAKEN: "已被其他账号验证 - 请联系 QQ 管理员",
}
PROBLEM_STATES = {STATE_CONFLICT, STATE_TAKEN}

# What the card shows (``card_context()["view"]``).
VIEW_NO_MAIN = "no_main"
VIEW_UNBOUND = "unbound"
VIEW_PENDING = "pending"
VIEW_BOUND = "bound"

# Message level per core outcome (submit / nickname / unbind / cancel).
OUTCOME_LEVELS = {
    "trusted": messages.SUCCESS,
    "conflict": messages.WARNING,
    "pending": messages.SUCCESS,
    "nickname_updated": messages.SUCCESS,
    "unchanged": messages.INFO,
    "taken": messages.ERROR,
    "cooldown": messages.WARNING,
    "rate_limited": messages.WARNING,
    "invalid": messages.ERROR,
    "not_bound": messages.WARNING,
    "unbound": messages.SUCCESS,
}

# Placeholder nickname used to split the card preview around the input box.
_NICK_MARK = ""


@dataclass
class MemberStatus:
    """What the services card shows about a user's binding."""

    binding: Binding | None = None
    state: str = ""
    live_code: BindCode | None = None

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, "")

    @property
    def masked_qq(self) -> str:
        return mask_qq(self.binding.qq) if self.binding else ""

    @property
    def has_problem(self) -> bool:
        return self.state in PROBLEM_STATES


def member_status(user, now=None) -> MemberStatus:
    binding = (
        Binding.objects.filter(user_id=user.pk)
        .select_related("user__profile__main_character")
        .first()
    )
    return MemberStatus(
        binding=binding,
        state=bindings.binding_state(binding),
        live_code=bindings.live_code(user, now),
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def services_url() -> str:
    """The services page, scrolled to our card."""
    return reverse("services:services") + "#" + CARD_ANCHOR


def _back():
    return redirect(services_url())


def _message(request, result) -> None:
    level = OUTCOME_LEVELS.get(result.outcome)
    if level is None:
        level = messages.INFO if result.ok else messages.ERROR
    messages.add_message(request, level, result.message or ("操作完成。" if result.ok else "操作失败。"))


def _session_code(request) -> dict:
    data = request.session.get(SESSION_KEY)
    return data if isinstance(data, dict) else {}


def _session_code_matches(data: dict, live: BindCode | None) -> bool:
    code = data.get("code")
    if not live or not isinstance(code, str) or not code:
        return False
    return hmac.compare_digest(codes.hash_code(code), live.code_hash)


def _card_parts(user, config) -> tuple[str, str, bool]:
    """Split the card preview around the nickname: ``(before, after, found)``.

    ``found`` is False when the card format does not contain the nickname;
    ``before`` is then the whole card. The parts are *not* shortened; see
    :func:`_nickname_room` for how much room the nickname really has.
    """
    card = cards.full_card(user, _NICK_MARK, config)
    if card.count(_NICK_MARK) == 1:
        before, after = card.split(_NICK_MARK)
        return before, after, True
    return card.replace(_NICK_MARK, ""), "", False


def _nickname_room(before: str, after: str, found: bool) -> int | None:
    """Bytes left for the nickname before the character name gets shortened;
    ``None`` when every allowed nickname fits (no hint needed)."""
    if not found:
        return None
    room = cards.CARD_MAX_BYTES - cards.byte_length(before) - cards.byte_length(after)
    # The longest allowed nickname: NICKNAME_MAX_LENGTH CJK characters.
    if room >= 3 * NICKNAME_MAX_LENGTH:
        return None
    return max(room, 0)


def _split_groups(groups: list[QQGroup]) -> dict:
    return {
        "fixed": [g for g in groups if g.kind == QQGroup.Kind.FIXED],
        "role": [g for g in groups if g.kind == QQGroup.Kind.ROLE],
    }


def _minutes_left(expires_at, now) -> int:
    return max(1, math.ceil((expires_at - now).total_seconds() / 60))


def _badge(view: str, status: MemberStatus) -> tuple[str, str]:
    """``(label, bootstrap class)`` of the status badge in the card header."""
    if status.state == STATE_CONFLICT:
        return "冲突", "text-bg-danger"
    if status.state == STATE_TAKEN:
        return "已被占用", "text-bg-danger"
    if status.binding is not None:
        return "已启用", "text-bg-success"
    if view == VIEW_PENDING:
        return "待验证", "text-bg-primary"
    return "未启用", "text-bg-secondary"


def card_context(request, now=None) -> dict:
    """Everything ``qqbot/service_ctrl.html`` needs for ``request.user``.

    Runs on every services page load, so it only does the queries the
    current state needs. It may drop a stale verification code from the
    session (and then says so once, pre-filling the form).
    """
    user = request.user
    now = now or timezone.now()
    context = {
        "is_manager": user.has_perm(MANAGE),
        "has_main": has_main_character(user),
        "nickname_help": NICKNAME_HELP,
        "nickname_max_length": NICKNAME_MAX_LENGTH,
        "card_max_bytes": cards.CARD_MAX_BYTES,
    }
    if not context["has_main"]:
        status = MemberStatus()
        context.update({"view": VIEW_NO_MAIN, "status": status})
        context["badge_label"], context["badge_class"] = _badge(VIEW_NO_MAIN, status)
        return context

    config = Config.get_solo()
    status = member_status(user, now)
    binding, live = status.binding, status.live_code
    session = _session_code(request)

    # The session code no longer matches a live code: forget it. When it
    # simply ran out (not used for the current binding), say so once and
    # pre-fill the form with what the member typed.
    stale_code = False
    if session and not _session_code_matches(session, live):
        request.session.pop(SESSION_KEY, None)
        used = binding is not None and binding.qq == session.get("qq")
        if live is None and not used:
            stale_code = True
        else:
            session = {}

    if live is not None:
        view = VIEW_PENDING
    elif binding is not None:
        view = VIEW_BOUND
    else:
        view = VIEW_UNBOUND

    context.update(
        {
            "view": view,
            "status": status,
            "binding": binding,
            "live": live,
            "rules_text": config.rules_text,
            "code_ttl_minutes": config.code_ttl_minutes,
            "cooldown_hours": config.rebind_cooldown_hours,
            "stale_code": stale_code,
        }
    )
    context["badge_label"], context["badge_class"] = _badge(view, status)

    # Groups: only where they are shown (a QQ with a problem shows none).
    groups = []
    if view == VIEW_PENDING or (view == VIEW_BOUND and not status.has_problem):
        groups = eligibility.groups_for_user(user, now)
    context["groups"] = _split_groups(groups)
    context["has_groups"] = bool(groups)

    # The nickname input with the card prefix: bind form and "改昵称".
    if view in (VIEW_UNBOUND, VIEW_BOUND):
        before, after, nick_in_card = _card_parts(user, config)
        room = _nickname_room(before, after, nick_in_card)
        context.update(
            {
                "card_before": before,
                "card_after": after,
                "nick_in_card": nick_in_card,
                # Hint under the nickname input (DESIGN.md 6).
                "nick_room_cjk": None if room is None else room // 3,
                "nick_room_ascii": room,
            }
        )

    if live is not None:
        context.update(
            {
                "code": session.get("code") if _session_code_matches(session, live) else "",
                "code_qq": mask_qq(live.qq),
                "code_nickname": live.nickname,
                "minutes_left": _minutes_left(live.expires_at, now),
            }
        )
    if binding is not None:
        context["card"] = cards.render_card(binding, config)
        context["card_shortened"] = cards.is_shortened(binding, config)
        # Unbinding does not reset the rebind cooldown (DESIGN.md 5.5).
        context["cooldown_ends"] = bindings.cooldown_ends(binding.qq_changed_at, now, config)

    # Pre-fill the bind form: the last typed values, else the binding.
    initial_qq = ""
    initial_nickname = binding.nickname if binding else ""
    if stale_code:
        initial_qq = session.get("qq") or ""
        initial_nickname = session.get("nickname") or initial_nickname
    context["initial_qq"] = initial_qq
    context["initial_nickname"] = initial_nickname
    return context


def _no_main_redirect(request):
    messages.warning(request, "请先在 AA 首页设置主角色，然后再来绑定 QQ。")
    return _back()


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
def my_qq(request):
    """The old "我的 QQ" page: everything is in the services card now."""
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
def submit(request):
    user = request.user
    if not has_main_character(user):
        return _no_main_redirect(request)
    form = SubmitForm(request.POST)
    if not form.is_valid():
        messages.error(request, first_error(form))
        return _back()

    qq = form.cleaned_data["qq"]
    nickname = form.cleaned_data["nickname"]
    if form.cleaned_data["regenerate"]:
        live = bindings.live_code(user)
        session = _session_code(request)
        if live is not None:
            qq, nickname = live.qq, live.nickname
        elif session.get("qq") and session.get("nickname"):
            qq, nickname = session["qq"], session["nickname"]
        else:
            messages.warning(request, "验证码已经失效，请重新填写 QQ 号和昵称后提交。")
            return _back()

    result = bindings.submit(user, qq, nickname)
    _message(request, result)
    if result.outcome == "pending" and result.code:
        request.session[SESSION_KEY] = {
            "code": result.code,
            "expires_at": result.expires_at.isoformat() if result.expires_at else "",
            "qq": normalize_qq(qq),
            "nickname": nickname,
        }
    elif result.outcome in ("trusted", "conflict"):
        request.session.pop(SESSION_KEY, None)
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
def code_cancel(request):
    n = bindings.cancel_code(request.user)
    request.session.pop(SESSION_KEY, None)
    if n:
        messages.success(request, "验证码已取消。")
    else:
        messages.info(request, "没有需要取消的验证码。")
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
def nickname(request):
    if not has_main_character(request.user):
        return _no_main_redirect(request)
    form = NicknameForm(request.POST)
    if not form.is_valid():
        messages.error(request, first_error(form))
        return _back()
    result = bindings.set_nickname(request.user, form.cleaned_data["nickname"])
    _message(request, result)
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_http_methods(["GET", "HEAD", "POST"])
def unbind(request):
    """POST with the ticked confirm box unbinds; GET (old links) goes back
    to the card, where the unbind form is."""
    if request.method != "POST":
        return _back()
    form = UnbindForm(request.POST)
    if not form.is_valid():
        messages.warning(request, first_error(form))
        return _back()
    result = bindings.unbind(request.user, actor=request.user)
    request.session.pop(SESSION_KEY, None)
    _message(request, result)
    return _back()
