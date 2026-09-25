"""Member side: the "QQ 绑定" card on AA's services page (DECISIONS.md #18,
docs/SPEC.md section 5, DESIGN.md 4.1 / 5).

Members do everything inside that card; there is no separate member page.
:func:`card_context` collects what the card shows, the POST views below
change data (always through ``qqbot.core``), store the outcome in the session
and go back to the card (``/services/#qqbot``). The card shows the outcome
*inside itself* (not as a Django message: AA prints those above the whole row
of service cards, which is off-screen once the page jumps to a card in a
later row, e.g. on phones), with the member's input kept on failure.
"""

import hmac
import math
from dataclasses import dataclass

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
RESULT_KEY = "qqbot_result"  # outcome of the last card action, shown once
RESULT_MAX_AGE = 300  # seconds; an outcome nobody saw in time is dropped
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

# Outcome levels and how the card shows them.
SUCCESS, INFO, WARNING, ERROR = "success", "info", "warning", "error"
LEVEL_STYLES = {  # level -> (alert class, Font Awesome icon)
    SUCCESS: ("alert-success", "fa-circle-check"),
    INFO: ("alert-info", "fa-circle-info"),
    WARNING: ("alert-warning", "fa-triangle-exclamation"),
    ERROR: ("alert-danger", "fa-circle-xmark"),
}

# Level per core outcome (submit / nickname / unbind / cancel).
OUTCOME_LEVELS = {
    "trusted": SUCCESS,
    "conflict": WARNING,
    "pending": SUCCESS,
    "nickname_updated": SUCCESS,
    "unchanged": INFO,
    "taken": ERROR,
    "cooldown": WARNING,
    "rate_limited": WARNING,
    "invalid": ERROR,
    "not_bound": WARNING,
    "unbound": SUCCESS,
}

# Which form the member used, so a failed action re-opens it pre-filled.
PANEL_BIND = "bind"  # the bind form of an unbound member
PANEL_REBIND = "rebind"
PANEL_NICKNAME = "nickname"
PANEL_UNBIND = "unbind"
BOUND_PANELS = {PANEL_REBIND, PANEL_NICKNAME, PANEL_UNBIND}

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


def _flash(request, level: str, text: str, *, ok: bool, panel: str = "", qq: str = "", nickname: str = "") -> None:
    """Remember an outcome for the card (shown once, by :func:`_pop_result`).

    ``panel``, ``qq`` and ``nickname`` say which form was used and what was
    typed, so a failed action comes back with that form open and filled in.
    """
    request.session[RESULT_KEY] = {
        "level": level,
        "text": text,
        "ok": ok,
        "panel": panel,
        # Only ever shown (escaped) in the member's own card; cut to the
        # form's max_length so a crafted POST cannot bloat the session.
        "qq": (qq or "")[:32],
        "nickname": (nickname or "")[:64],
        "at": timezone.now().timestamp(),
    }


def _flash_result(request, result, *, panel: str = "", qq: str = "", nickname: str = "") -> None:
    level = OUTCOME_LEVELS.get(result.outcome) or (INFO if result.ok else ERROR)
    text = result.message or ("操作完成。" if result.ok else "操作失败。")
    _flash(request, level, text, ok=result.ok, panel=panel, qq=qq, nickname=nickname)


def _pop_result(request, now) -> dict | None:
    """The outcome of the member's last card action, once; ``None`` when there
    is none (or it is malformed or too old to still be meant for this page)."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    data = session.pop(RESULT_KEY, None)
    if not isinstance(data, dict):
        return None
    level, text, at = data.get("level"), data.get("text"), data.get("at")
    if level not in LEVEL_STYLES or not isinstance(text, str) or not text:
        return None
    if not isinstance(at, (int, float)) or not 0 <= now.timestamp() - at <= RESULT_MAX_AGE:
        return None
    alert_class, icon = LEVEL_STYLES[level]
    return {
        "text": text,
        "ok": bool(data.get("ok")),
        "alert_class": alert_class,
        "icon": icon,
        "panel": data.get("panel") if isinstance(data.get("panel"), str) else "",
        "qq": data.get("qq") if isinstance(data.get("qq"), str) else "",
        "nickname": data.get("nickname") if isinstance(data.get("nickname"), str) else "",
    }


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
    """``(label, bootstrap class)`` of the status badge in the card header.

    The badge shows the *current binding* when there is one, also while a
    re-bind code is pending (the current QQ keeps working until the new one
    is verified; DESIGN.md 4.2 ③). A problem badge always comes with the
    red explanation in the card (``#qqbot-problem``, bound and pending view).
    """
    if status.state == STATE_CONFLICT:
        return "冲突", "text-bg-danger"
    if status.state == STATE_TAKEN:
        return "已被占用", "text-bg-danger"
    if status.binding is not None:
        return "已启用", "text-bg-success"
    if view == VIEW_PENDING:
        return "待验证", "text-bg-primary"
    return "未启用", "text-bg-warning"  # like AA's own "Disabled" badge


def card_context(request, now=None) -> dict:
    """Everything ``qqbot/service_ctrl.html`` needs for ``request.user``.

    Runs on every services page load, so it only does the queries the
    current state needs. It shows (and forgets) the outcome of the member's
    last card action; a failed action re-opens the form that was used,
    filled in with what was typed. It may drop a stale verification code
    from the session (and then says so once, pre-filling the form: the bind
    form, or the opened 换绑 panel for a re-bind).
    """
    user = request.user
    now = now or timezone.now()
    result = _pop_result(request, now)
    context = {
        "result": result,
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
        # binding.user comes with its main character (select_related).
        owner = binding.user if binding is not None else user
        before, after, nick_in_card = _card_parts(owner, config)
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
        # Shown as time left, not a clock time: AA renders times in
        # settings.TIME_ZONE (usually UTC), members read Beijing time.
        ends = bindings.cooldown_ends(binding.qq_changed_at, now, config)
        context["cooldown_ends"] = ends
        context["cooldown_left"] = bindings.format_remaining(ends - now) if ends else ""

    # Pre-fill the forms and pick the open panel (bound view): a failed
    # action's input wins over a stale code's, which wins over the binding.
    initial_qq = ""
    initial_nickname = binding.nickname if binding else ""
    open_panel = ""
    if stale_code:
        initial_qq = session.get("qq") or ""
        if binding is None:
            initial_nickname = session.get("nickname") or initial_nickname
        else:
            open_panel = PANEL_REBIND
    if result is not None and not result["ok"]:
        panel = result["panel"]
        if view == VIEW_UNBOUND and panel in (PANEL_BIND, PANEL_REBIND):
            initial_qq, initial_nickname = result["qq"], result["nickname"]
        elif view == VIEW_BOUND and panel in BOUND_PANELS:
            open_panel = panel
            if panel == PANEL_REBIND:
                initial_qq = result["qq"]
            elif panel == PANEL_NICKNAME:
                initial_nickname = result["nickname"]
    context["initial_qq"] = initial_qq
    context["initial_nickname"] = initial_nickname
    context["open_panel"] = open_panel if view == VIEW_BOUND else ""
    return context


def _no_main_redirect(request):
    # The card itself explains this (view "no_main").
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
    typed_qq = request.POST.get("qq", "")
    typed_nickname = request.POST.get("nickname", "")
    # Which form this came from: the bind form, or 换绑 of a bound member
    # ("重新生成" in the pending view needs no panel).
    panel = PANEL_REBIND if Binding.objects.filter(user_id=user.pk).exists() else PANEL_BIND
    form = SubmitForm(request.POST)
    if not form.is_valid():
        _flash(request, ERROR, first_error(form), ok=False, panel=panel, qq=typed_qq, nickname=typed_nickname)
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
            _flash(request, WARNING, "验证码已经失效，请重新填写 QQ 号和昵称后提交。", ok=False)
            return _back()
        panel = ""

    result = bindings.submit(user, qq, nickname)
    _flash_result(request, result, panel=panel, qq=typed_qq, nickname=typed_nickname)
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
        _flash(request, SUCCESS, "验证码已取消。", ok=True)
    else:
        _flash(request, INFO, "没有需要取消的验证码。", ok=True)
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
def nickname(request):
    if not has_main_character(request.user):
        return _no_main_redirect(request)
    typed = request.POST.get("nickname", "")
    form = NicknameForm(request.POST)
    if not form.is_valid():
        _flash(request, ERROR, first_error(form), ok=False, panel=PANEL_NICKNAME, nickname=typed)
        return _back()
    result = bindings.set_nickname(request.user, form.cleaned_data["nickname"])
    _flash_result(request, result, panel=PANEL_NICKNAME, nickname=typed)
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
        _flash(request, WARNING, first_error(form), ok=False, panel=PANEL_UNBIND)
        return _back()
    result = bindings.unbind(request.user, actor=request.user)
    request.session.pop(SESSION_KEY, None)
    _flash_result(request, result, panel=PANEL_UNBIND)
    return _back()
