"""Member side: the "QQ 绑定" card on AA's services page (DECISIONS.md #18,
docs/SPEC.md section 5, DESIGN.md 4.1 / 5).

Members do everything inside that card; there is no separate member page.
:func:`card_context` collects what the card shows, the POST views below
change data (always through ``qqbot.core``), store the outcome in the session
and go back to the card (``/services/#qqbot``). The card shows the outcome
*inside itself* (not as a Django message: AA prints those above the whole row
of service cards, which is off-screen once the page jumps to a card in a
later row, e.g. on phones), with the member's input kept on failure.

成员端：AA 服务页上的「QQ 绑定」卡片（DECISIONS.md #18、docs/SPEC.md 第 5 节、
DESIGN.md 4.1 / 5）。

成员的所有操作都在这张卡片里完成，没有单独的成员页面。
:func:`card_context` 收集卡片要显示的内容；下面的 POST 视图负责修改数据
（一律通过 ``qqbot.core``），把结果存进 session，再跳回卡片
（``/services/#qqbot``）。结果显示在卡片*内部*（不用 Django message：AA 会把
message 显示在整排服务卡片的上方，页面跳到后面几排的卡片时就看不到了，
手机上尤其如此）；操作失败时会保留成员填写的内容。
"""

import hmac
import math
from dataclasses import dataclass

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy, pgettext
from django.views.decorators.http import require_http_methods, require_POST

from allianceauth.services.hooks import get_extension_logger

from ..core import bindings, cards, codes, eligibility
from ..core.access import has_main_character
from ..core.util import NICKNAME_MAX_LENGTH, mask_qq
from ..i18n import ui_language_view
from ..models import Binding, BindCode, Config, QQGroup, normalize_qq
from .member_forms import NicknameForm, SubmitForm, UnbindForm, first_error, nickname_help

logger = get_extension_logger(__name__)

BASIC_ACCESS = "qqbot.basic_access"
MANAGE = "qqbot.manage"
SESSION_KEY = "qqbot_code"
RESULT_KEY = "qqbot_result"  # outcome of the last card action, shown once / 上次卡片操作的结果，只显示一次
RESULT_MAX_AGE = 300  # seconds; an outcome nobody saw in time is dropped / 秒；没被及时看到的结果会被丢弃
CARD_ANCHOR = "qqbot"  # id of the card on the services page / 服务页上卡片的 id

# Binding states shown to the member.
# 展示给成员看的绑定状态。
STATE_VERIFIED = bindings.STATE_VERIFIED
STATE_TRUSTED = bindings.STATE_TRUSTED
STATE_CONFLICT = bindings.STATE_CONFLICT
STATE_TAKEN = bindings.STATE_TAKEN

STATE_LABELS = {
    STATE_VERIFIED: gettext_lazy("Verified"),
    STATE_TRUSTED: gettext_lazy("Trusted (already in group)"),
    STATE_CONFLICT: gettext_lazy("Conflict - contact a QQ admin"),
    STATE_TAKEN: gettext_lazy("Verified by another account - contact a QQ admin"),
}
PROBLEM_STATES = {STATE_CONFLICT, STATE_TAKEN}

# What the card shows (``card_context()["view"]``).
# 卡片显示哪种视图（``card_context()["view"]``）。
VIEW_NO_MAIN = "no_main"
VIEW_UNBOUND = "unbound"
VIEW_PENDING = "pending"
VIEW_BOUND = "bound"

# Outcome levels and how the card shows them.
# 结果的级别，以及卡片怎样显示它们。
SUCCESS, INFO, WARNING, ERROR = "success", "info", "warning", "error"
LEVEL_STYLES = {  # level -> (alert class, Font Awesome icon) / 级别 -> (提示框样式类, Font Awesome 图标)
    SUCCESS: ("alert-success", "fa-circle-check"),
    INFO: ("alert-info", "fa-circle-info"),
    WARNING: ("alert-warning", "fa-triangle-exclamation"),
    ERROR: ("alert-danger", "fa-circle-xmark"),
}

# Level per core outcome (submit / nickname / unbind / cancel).
# 每种 core 结果对应的级别（提交 / 改昵称 / 解绑 / 取消）。
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
# 记下成员用的是哪个表单，操作失败时重新打开它并填好内容。
PANEL_BIND = "bind"  # the bind form of an unbound member / 未绑定成员的绑定表单
PANEL_REBIND = "rebind"
PANEL_NICKNAME = "nickname"
PANEL_UNBIND = "unbind"
BOUND_PANELS = {PANEL_REBIND, PANEL_NICKNAME, PANEL_UNBIND}

# Placeholder nickname used to split the card preview around the input box.
# 占位用的昵称，用来在输入框两侧切开群名片预览。
_NICK_MARK = ""


@dataclass
class MemberStatus:
    """What the services card shows about a user's binding.

    服务卡片上显示的某个用户的绑定情况。
    """

    binding: Binding | None = None
    state: str = ""
    live_code: BindCode | None = None

    @property
    def state_label(self) -> str:
        label = STATE_LABELS.get(self.state)
        return str(label) if label else ""

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
# 辅助函数
# --------------------------------------------------------------------------


def services_url() -> str:
    """The services page, scrolled to our card.

    服务页的地址，并定位到我们的卡片。
    """
    return reverse("services:services") + "#" + CARD_ANCHOR


def _back():
    return redirect(services_url())


def _flash(request, level: str, text: str, *, ok: bool, panel: str = "", qq: str = "", nickname: str = "") -> None:
    """Remember an outcome for the card (shown once, by :func:`_pop_result`).

    ``panel``, ``qq`` and ``nickname`` say which form was used and what was
    typed, so a failed action comes back with that form open and filled in.

    为卡片记下一个操作结果（由 :func:`_pop_result` 取出，只显示一次）。

    ``panel``、``qq`` 和 ``nickname`` 记录用的是哪个表单、填了什么，这样
    操作失败回到卡片时，这个表单是打开的，并且已经填好内容。
    """
    request.session[RESULT_KEY] = {
        "level": level,
        "text": text,
        "ok": ok,
        "panel": panel,
        # Only ever shown (escaped) in the member's own card; cut to the
        # form's max_length so a crafted POST cannot bloat the session.
        # 只会（转义后）显示在成员自己的卡片里；按表单的 max_length 截断，
        # 防止伪造的 POST 把 session 撑大。
        "qq": (qq or "")[:32],
        "nickname": (nickname or "")[:64],
        "at": timezone.now().timestamp(),
    }


def _flash_result(request, result, *, panel: str = "", qq: str = "", nickname: str = "") -> None:
    level = OUTCOME_LEVELS.get(result.outcome) or (INFO if result.ok else ERROR)
    text = result.message or (gettext("Done.") if result.ok else gettext("Something went wrong."))
    _flash(request, level, text, ok=result.ok, panel=panel, qq=qq, nickname=nickname)


def _pop_result(request, now) -> dict | None:
    """The outcome of the member's last card action, once; ``None`` when there
    is none (or it is malformed or too old to still be meant for this page).

    成员上一次卡片操作的结果，只返回一次；没有结果时返回 ``None``（结果格式
    不对，或者太旧、已经不是给这次页面看的，也返回 ``None``）。
    """
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

    以昵称为界，把群名片预览切成两段：``(before, after, found)``。

    如果群名片格式里没有昵称，``found`` 为 False，这时 ``before`` 就是整张
    群名片。这两段*不会*被截短；昵称实际还剩多少空间，见 :func:`_nickname_room`。
    """
    card = cards.full_card(user, _NICK_MARK, config)
    if card.count(_NICK_MARK) == 1:
        before, after = card.split(_NICK_MARK)
        return before, after, True
    return card.replace(_NICK_MARK, ""), "", False


def _nickname_room(before: str, after: str, found: bool) -> int | None:
    """Bytes left for the nickname before the character name gets shortened;
    ``None`` when every allowed nickname fits (no hint needed).

    在角色名被截短之前，还能留给昵称的字节数；如果任何合法的昵称都放得下
    （不需要提示），返回 ``None``。
    """
    if not found:
        return None
    room = cards.CARD_MAX_BYTES - cards.byte_length(before) - cards.byte_length(after)
    # The longest allowed nickname: NICKNAME_MAX_LENGTH CJK characters.
    # 最长的合法昵称：NICKNAME_MAX_LENGTH 个中文字符。
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

    卡片标题栏里状态徽章的 ``(文字, bootstrap 样式类)``。

    有绑定时，徽章显示的是*当前绑定*的状态，换绑验证码还在等待验证时也一样
    （新 QQ 验证通过之前，当前 QQ 仍然有效；DESIGN.md 4.2 ③）。出问题的徽章
    总会配上卡片里的红色说明（``#qqbot-problem``，在已绑定和待验证视图里）。
    """
    if status.state == STATE_CONFLICT:
        return pgettext("status badge", "Conflict"), "text-bg-danger"
    if status.state == STATE_TAKEN:
        return pgettext("status badge", "Taken"), "text-bg-danger"
    if status.binding is not None:
        return pgettext("status badge", "Enabled"), "text-bg-success"
    if view == VIEW_PENDING:
        return pgettext("status badge", "Pending"), "text-bg-primary"
    return pgettext("status badge", "Disabled"), "text-bg-warning"  # like AA's own "Disabled" badge / 和 AA 自带的「Disabled」徽章一样


def card_context(request, now=None) -> dict:
    """Everything ``qqbot/service_ctrl.html`` needs for ``request.user``.

    Runs on every services page load, so it only does the queries the
    current state needs. It shows (and forgets) the outcome of the member's
    last card action; a failed action re-opens the form that was used,
    filled in with what was typed. It may drop a stale verification code
    from the session (and then says so once, pre-filling the form: the bind
    form, or the opened 换绑 panel for a re-bind).

    为 ``request.user`` 准备 ``qqbot/service_ctrl.html`` 需要的全部数据。

    每次打开服务页都会运行，所以只做当前状态需要的查询。它会显示（然后忘掉）
    成员上一次卡片操作的结果；操作失败时，会重新打开当时用的表单，并填上
    成员输入的内容。它可能会把 session 里过期的验证码删掉（这时会提示一次，
    并预填表单：绑定表单，或者换绑时打开的「换绑」面板）。
    """
    user = request.user
    now = now or timezone.now()
    result = _pop_result(request, now)
    context = {
        "result": result,
        "is_manager": user.has_perm(MANAGE),
        "has_main": has_main_character(user),
        "nickname_help": nickname_help(),
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
    # session 里的验证码已经对不上有效的验证码：忘掉它。如果只是过期了
    # （没有用在当前绑定上），提示一次，并用成员之前填的内容预填表单。
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
    # 群列表：只在要显示时才查（有问题的 QQ 不显示任何群）。
    groups = []
    if view == VIEW_PENDING or (view == VIEW_BOUND and not status.has_problem):
        groups = eligibility.groups_for_user(user, now)
    context["groups"] = _split_groups(groups)
    context["has_groups"] = bool(groups)

    # The nickname input with the card prefix: bind form and "改昵称".
    # 带群名片前缀的昵称输入框：用于绑定表单和「改昵称」。
    if view in (VIEW_UNBOUND, VIEW_BOUND):
        # binding.user comes with its main character (select_related).
        # binding.user 已经连同主角色一起查出来了（select_related）。
        owner = binding.user if binding is not None else user
        before, after, nick_in_card = _card_parts(owner, config)
        room = _nickname_room(before, after, nick_in_card)
        context.update(
            {
                "card_before": before,
                "card_after": after,
                "nick_in_card": nick_in_card,
                # Hint under the nickname input (DESIGN.md 6).
                # 昵称输入框下方的提示（DESIGN.md 6）。
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
        # 解绑不会重置换绑冷却（DESIGN.md 5.5）。
        # 显示剩余时长而不是具体时刻：AA 按 settings.TIME_ZONE（通常是 UTC）
        # 显示时间，而成员看的是北京时间。
        ends = bindings.cooldown_ends(binding.qq_changed_at, now, config)
        context["cooldown_ends"] = ends
        context["cooldown_left"] = bindings.format_remaining(ends - now) if ends else ""

    # Pre-fill the forms and pick the open panel (bound view): a failed
    # action's input wins over a stale code's, which wins over the binding.
    # 预填表单，并选出要打开的面板（已绑定视图）：优先用失败操作的输入，
    # 其次是过期验证码里的内容，最后才是当前绑定。
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
    # 卡片自己会说明这种情况（视图 "no_main"）。
    return _back()


# --------------------------------------------------------------------------
# views
# 视图
# --------------------------------------------------------------------------


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@ui_language_view
def my_qq(request):
    """The old "我的 QQ" page: everything is in the services card now.

    原来的「我的 QQ」页面：现在所有功能都在服务卡片里。
    """
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
@ui_language_view
def submit(request):
    user = request.user
    if not has_main_character(user):
        return _no_main_redirect(request)
    typed_qq = request.POST.get("qq", "")
    typed_nickname = request.POST.get("nickname", "")
    # Which form this came from: the bind form, or 换绑 of a bound member
    # ("重新生成" in the pending view needs no panel).
    # 判断请求来自哪个表单：绑定表单，或者已绑定成员的「换绑」
    # （待验证视图里的「重新生成」不需要面板）。
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
            _flash(
                request,
                WARNING,
                gettext("The verification code has expired. Enter your QQ number and nickname again and submit."),
                ok=False,
            )
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
@ui_language_view
def code_cancel(request):
    n = bindings.cancel_code(request.user)
    request.session.pop(SESSION_KEY, None)
    if n:
        _flash(request, SUCCESS, gettext("Verification code cancelled."), ok=True)
    else:
        _flash(request, INFO, gettext("There is no verification code to cancel."), ok=True)
    return _back()


@login_required
@permission_required(BASIC_ACCESS, raise_exception=True)
@require_POST
@ui_language_view
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
@ui_language_view
def unbind(request):
    """POST with the ticked confirm box unbinds; GET (old links) goes back
    to the card, where the unbind form is.

    勾选了确认框的 POST 请求会解绑；GET 请求（旧链接）会跳回卡片，
    解绑表单就在卡片里。
    """
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
