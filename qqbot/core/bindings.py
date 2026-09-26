"""All write operations on bindings and codes (docs/SPEC.md 3.9).

Every function runs inside ``transaction.atomic()`` and takes its locks in
the order documented in ``core/locks.py`` (user, then QQs, then rows), so
concurrent operations serialize instead of deadlocking or racing.

绑定和验证码的所有写操作（见 docs/SPEC.md 3.9）。

每个函数都在 ``transaction.atomic()`` 内运行，并按 ``core/locks.py`` 里写明
的顺序加锁（先用户，再 QQ，最后数据行），这样并发操作会排队执行，而不会
死锁或互相抢着改数据。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext, gettext_lazy, ngettext

from allianceauth.services.hooks import get_extension_logger

from ..models import AuditLog, Binding, BindCode, Config, Event, normalize_qq
from . import audit, codes, events, locks
from .cards import CARD_MAX_BYTES, byte_length, clean_card
from .roster import in_fresh_roster
from .util import mask_qq, validate_nickname

logger = get_extension_logger(__name__)

Action = AuditLog.Action

CODE_RATE_LIMIT = 5  # codes per user ... / 每个用户最多生成这么多个验证码……
CODE_RATE_WINDOW = 3600  # ... per this many seconds / ……在这么多秒之内
# 每个用户在每个时间窗口内，免验证码（老成员免验证）绑定或换 QQ 的次数上限
TRUSTED_RATE_LIMIT = 5  # code-free (trusted) binds / QQ changes per user per window

# Lazy: translated into the language of the request that shows them.
# 惰性翻译：显示时才按当前请求的语言翻译。
MSG_TAKEN = gettext_lazy(
    "This QQ is already bound. If you have questions, contact a QQ admin."
)
MSG_QQ_CHANGED = gettext_lazy(
    "This member's QQ has just changed, so this page is out of date. "
    "Reopen the page and check again before you continue."
)


def _qq_changed(binding, expected_qq) -> bool:
    """True when a manager acted on a page that showed another QQ.

    ``expected_qq=None`` skips the check; anything else (also ``""``) must
    equal the binding's current QQ. Member rebinds keep the binding's pk, so
    the pk alone does not say which QQ the manager reviewed.

    管理员操作所在的页面上显示的是另一个 QQ 时，返回 True。

    ``expected_qq=None`` 表示跳过检查；其他任何值（包括 ``""``）都必须等于
    该绑定当前的 QQ。成员换绑时绑定的 pk 不变，所以光看 pk 无法知道管理员
    核对的是哪个 QQ。
    """
    return expected_qq is not None and normalize_qq(expected_qq) != binding.qq


@dataclass
class Result:
    ok: bool
    outcome: str
    message: str = ""


@dataclass
class SubmitResult(Result):
    binding: Binding | None = None
    code: str | None = None  # plain-text code; only returned once / 明文验证码，只返回这一次
    expires_at: datetime | None = None
    retry_after: timedelta | None = None  # remaining cooldown / 剩余冷却时间


@dataclass
class ClaimResult(Result):
    qq: str = ""
    binding: Binding | None = None


def _live_codes(user, now):
    return BindCode.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True, expires_at__gt=now
    )


def _invalidate_codes(user, now) -> int:
    """Invalidate every unused code of the user (live or not yet expired).

    作废该用户所有未使用的验证码（仍有效的或尚未过期的）。
    """
    return BindCode.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=now)


def format_remaining(delta: timedelta) -> str:
    """``delta`` for members, rounded up: "7 小时 54 分钟", "3 小时", "5 分钟".

    把 ``delta`` 转成给成员看的文字，向上取整："7 小时 54 分钟"、"3 小时"、"5 分钟"。
    """
    minutes = max(1, int((delta.total_seconds() + 59) // 60))
    hours, minutes = divmod(minutes, 60)
    hours_text = ngettext("%(count)d hour", "%(count)d hours", hours) % {"count": hours}
    minutes_text = ngettext("%(count)d minute", "%(count)d minutes", minutes) % {"count": minutes}
    if hours and minutes:
        return f"{hours_text} {minutes_text}"
    if hours:
        return hours_text
    return minutes_text


def _count(key: str, limit: int) -> bool:
    """Count one attempt in a fixed window; True when over ``limit``.

    在固定时间窗口内记一次尝试；超过 ``limit`` 时返回 True。
    """
    cache.add(key, 0, CODE_RATE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr / 键在 add 和 incr 之间过期了
        cache.set(key, 1, CODE_RATE_WINDOW)
        count = 1
    return count > limit


def _rate_limited(user) -> bool:
    """Count one code generation; True when the user is over the limit.

    记一次验证码生成；用户超出次数上限时返回 True。
    """
    return _count(f"qqbot:codes:{user.pk}", CODE_RATE_LIMIT)


def _trusted_rate_limited(user) -> bool:
    """Count one code-free (trusted) bind; True when the user is over the
    limit. Separate from the code counter so failed code attempts never
    block an old member.

    记一次免验证码（老成员免验证）绑定；用户超出次数上限时返回 True。
    它和验证码的计数器分开，这样验证码相关的失败尝试永远不会挡住老成员。
    """
    return _count(f"qqbot:trusted:{user.pk}", TRUSTED_RATE_LIMIT)


def _last_qq_change(user, existing, qq_n, cooldown: timedelta, now):
    """When the user last changed their QQ, for the rebind cooldown.

    With a binding that is ``qq_changed_at``. Without one, the cooldown of a
    binding the member removed themselves (``UNBIND``) still runs, so
    "unbind, then submit another QQ" cannot skip it; binding the *same* QQ
    again is not a change (an unbind by mistake can be undone at once). The
    time is kept in that audit row's ``detail``. A manager's forced unbind
    does not carry it over.

    用户上一次更换 QQ 的时间，用于计算换绑冷却。

    有绑定时就是它的 ``qq_changed_at``。没有绑定时，如果之前的绑定是成员
    自己解除的（``UNBIND``），那个绑定的冷却仍然继续计时，所以“先解绑再提交
    另一个 QQ”绕不过冷却；重新绑定*同一个* QQ 不算更换（误解绑可以马上恢复）。
    这个时间保存在那条审计日志的 ``detail`` 里。管理员强制解绑不会把冷却
    带过来。
    """
    if existing is not None:
        return existing.qq_changed_at
    # qq_changed_at <= unbind time, so older unbinds cannot matter.
    # qq_changed_at 一定不晚于解绑时间，所以冷却期之前的解绑记录不用管。
    row = (
        AuditLog.objects.filter(
            target_user_id=user.pk, action=Action.UNBIND, created_at__gte=now - cooldown
        )
        .order_by("-id")
        .values_list("qq", "detail")
        .first()
    )
    if row is None or row[0] == qq_n:
        return None
    detail = row[1]
    if not isinstance(detail, dict) or not isinstance(detail.get("qq_changed_at"), str):
        return None
    return parse_datetime(detail["qq_changed_at"])


def cooldown_ends(qq_changed_at, now=None, config=None):
    """When the rebind cooldown that started at ``qq_changed_at`` ends, or
    ``None`` when it is already over (for the services card).

    从 ``qq_changed_at`` 开始的换绑冷却何时结束；已经结束时返回 ``None``
    （用于服务页卡片）。
    """
    now = now or timezone.now()
    config = config or Config.get_solo()
    if not qq_changed_at or not config.rebind_cooldown_hours:
        return None
    ends = qq_changed_at + timedelta(hours=config.rebind_cooldown_hours)
    return ends if ends > now else None


def _user_binding_for_update(user):
    # No select_related together with select_for_update: PostgreSQL refuses
    # FOR UPDATE on the nullable side of an outer join.
    # 不要把 select_related 和 select_for_update 一起用：PostgreSQL 不允许
    # 对外连接里可以为空的那一侧使用 FOR UPDATE。
    user_id = getattr(user, "pk", user)
    return Binding.objects.select_for_update().filter(user_id=user_id).first()


def _current_qq(user_id) -> str:
    """The user's bound QQ (plain read). Only the user's own operations change
    it, and they hold the user lock, so it is stable once that lock is held.

    用户当前绑定的 QQ（普通读取，不加锁）。只有该用户自己的操作会改变它，
    而这些操作都持有用户锁，所以拿到用户锁之后这个值就不会再变。
    """
    return Binding.objects.filter(user_id=user_id).values_list("qq", flat=True).first() or ""


def _lock_user_and_qqs(user_id, *qqs) -> None:
    """Lock the user, then the given QQs plus the user's current QQ.

    先锁用户，再锁给定的 QQ 以及该用户当前绑定的 QQ。
    """
    locks.lock_user(user_id)
    locks.lock_qqs(*qqs, _current_qq(user_id))


def _card_event(binding, kinds):
    if Event.Kind.CARD not in kinds:
        events.emit(Event.Kind.CARD, binding.qq)


# --------------------------------------------------------------------------
# submit / codes
# 提交 / 验证码
# --------------------------------------------------------------------------


def submit(user, qq, nickname, now=None) -> SubmitResult:
    """A member submits (or changes) their QQ and nickname.

    成员提交（或修改）自己的 QQ 和昵称。
    """
    now = now or timezone.now()
    qq_n = normalize_qq(qq)
    if not qq_n:
        return SubmitResult(
            False, "invalid",
            gettext("Enter a valid QQ number (5–11 digits, not starting with 0)."),
        )
    try:
        nickname = validate_nickname(nickname)
    except ValidationError as exc:
        return SubmitResult(False, "invalid", " ".join(exc.messages))

    config = Config.get_solo()
    with transaction.atomic():
        _lock_user_and_qqs(user.pk, qq_n)
        existing = _user_binding_for_update(user)

        # Same QQ as the current binding: only the nickname can change.
        # 和当前绑定的 QQ 相同：只有昵称可能变化。
        if existing is not None and existing.qq == qq_n:
            if existing.nickname == nickname:
                return SubmitResult(True, "unchanged", gettext("Nothing to change."), binding=existing)
            old_nickname = existing.nickname
            existing.nickname = nickname
            existing.save(update_fields=["nickname", "updated_at"])
            audit.log(Action.NICKNAME, actor=user, qq=qq_n, target_user=user,
                      old=old_nickname, new=nickname)
            _card_event(existing, events.refresh_kinds(existing))
            return SubmitResult(
                True, "nickname_updated", gettext("Nickname updated."), binding=existing
            )

        if Binding.objects.filter(qq=qq_n, status=Binding.Status.VERIFIED).exclude(
            user_id=user.pk
        ).exists():
            return SubmitResult(False, "taken", str(MSG_TAKEN))

        cooldown = timedelta(hours=config.rebind_cooldown_hours)
        changed_at = _last_qq_change(user, existing, qq_n, cooldown, now) if cooldown else None
        if changed_at:
            ends = changed_at + cooldown
            if now < ends:
                remaining = ends - now
                return SubmitResult(
                    False,
                    "cooldown",
                    gettext("You changed your QQ too recently. Try again in %(time)s.")
                    % {"time": format_remaining(remaining)},
                    retry_after=remaining,
                )

        if in_fresh_roster(qq_n, now):
            if _trusted_rate_limited(user):
                return SubmitResult(
                    False, "rate_limited",
                    gettext(
                        "Too many binds or QQ changes (at most %(limit)d per hour). "
                        "Please try again later."
                    ) % {"limit": TRUSTED_RATE_LIMIT},
                )
            return _submit_trusted(user, existing, qq_n, nickname, now)

        # Pending: the current binding stays untouched until the code is used.
        # 待验证：在验证码被使用之前，当前绑定保持不变。
        if _rate_limited(user):
            return SubmitResult(
                False, "rate_limited",
                gettext(
                    "Too many verification codes requested (at most %(limit)d per hour). "
                    "Please try again later."
                ) % {"limit": CODE_RATE_LIMIT},
            )
        _invalidate_codes(user, now)
        expires_at = now + timedelta(minutes=config.code_ttl_minutes)
        for _attempt in range(5):
            code = codes.generate_code()
            try:
                with transaction.atomic():
                    BindCode.objects.create(
                        user=user,
                        qq=qq_n,
                        nickname=nickname,
                        code_hash=codes.hash_code(code),
                        expires_at=expires_at,
                    )
                break
            except IntegrityError:  # hash collision with an old code / 和旧验证码的哈希撞了
                continue
        else:  # pragma: no cover - practically impossible / 实际上不可能发生
            raise RuntimeError("could not generate a unique code")
        audit.log(Action.CODE, actor=user, qq=qq_n, target_user=user, expires_at=expires_at)
        logger.info("qqbot: code generated for %s / %s", user, mask_qq(qq_n))
        return SubmitResult(
            True,
            "pending",
            gettext(
                "Before the code expires, request to join a QQ group and enter the "
                "verification code as the verification message."
            ),
            binding=existing,
            code=code,
            expires_at=expires_at,
        )


def _submit_trusted(user, existing, qq_n, nickname, now) -> SubmitResult:
    """Old member: the QQ is in a fresh roster, bind without a code.

    老成员：该 QQ 在最近更新过的群成员名单里，不需要验证码，直接绑定。
    """
    old_qq = existing.qq if existing is not None else ""
    if existing is None:
        binding = Binding(user=user)
    else:
        binding = existing
    binding.qq = qq_n
    binding.nickname = nickname
    binding.status = Binding.Status.TRUSTED
    binding.verified_via = Binding.VerifiedVia.NONE
    binding.verified_at = None
    binding.card_override = ""
    binding.qq_changed_at = now
    binding.save()
    _invalidate_codes(user, now)

    if old_qq:
        events.emit(Event.Kind.RECHECK, old_qq)
        audit.log(Action.REBIND, actor=user, qq=qq_n, target_user=user,
                  old_qq=old_qq, status=Binding.Status.TRUSTED)
    else:
        audit.log(Action.BIND, actor=user, qq=qq_n, target_user=user,
                  status=Binding.Status.TRUSTED)

    others = list(
        Binding.objects.filter(qq=qq_n, status=Binding.Status.TRUSTED)
        .exclude(pk=binding.pk)
        .select_related("user")
    )
    if others:
        audit.log(Action.CONFLICT, actor=user, qq=qq_n, target_user=user,
                  others=[b.user.username for b in others])
    events.refresh_binding(binding)
    events.refresh_qq(qq_n, exclude_pk=binding.pk)
    if old_qq:
        events.refresh_qq(old_qq)
    logger.info("qqbot: %s bound %s as trusted", user, mask_qq(qq_n))
    if others:
        return SubmitResult(
            True,
            "conflict",
            gettext("Another account has also claimed this QQ. Please contact a QQ admin."),
            binding=binding,
        )
    return SubmitResult(
        True, "trusted", gettext("Bound (trusted: already in group)."), binding=binding
    )


def live_code(user, now=None) -> BindCode | None:
    now = now or timezone.now()
    return _live_codes(user, now).order_by("-pk").first()


def cancel_code(user, now=None) -> int:
    """Invalidate the user's live code(s). Returns how many were invalidated.

    作废该用户仍有效的验证码，返回作废的数量。
    """
    now = now or timezone.now()
    with transaction.atomic():
        locks.lock_user(user.pk)
        return _invalidate_codes(user, now)


# --------------------------------------------------------------------------
# claim (bot API)
# claim：机器人上报验证码（机器人 API）
# --------------------------------------------------------------------------
# The messages here go to the bot API (read by ops in Chinese), so they are
# not translated.
# 这里的提示文字返回给机器人接口（运维看中文），所以不做翻译。


def claim(qq, text, now=None) -> ClaimResult:
    """The bot reports that ``qq`` sent ``text`` (e.g. a join-request comment).

    机器人上报：``qq`` 发送了 ``text``（例如入群申请里填写的验证信息）。
    """
    now = now or timezone.now()
    qq_n = normalize_qq(qq)
    code = codes.extract_code(text)
    if not code:
        return ClaimResult(False, "no_code", "没有找到验证码。", qq=qq_n)
    code_hash = codes.hash_code(code)
    try:
        return _claim(qq_n, code_hash, now)
    except IntegrityError:
        # Safety net: the locks should make this impossible. The transaction
        # was rolled back (the code is unused again); one retry sees the
        # committed state and gives a normal outcome.
        # 兜底：有锁保护，按理说不会走到这里。事务已经回滚（验证码又变回
        # 未使用），重试一次就能看到已提交的数据，并给出正常结果。
        logger.warning("qqbot: claim for %s hit an integrity error, retrying", mask_qq(qq_n))
        return _claim(qq_n, code_hash, now)


def _claim(qq_n, code_hash, now) -> ClaimResult:
    with transaction.atomic():
        # Find the code's owner without locking the code row, so the user
        # lock can be taken first (same order as submit / unbind).
        # 先不锁验证码行，只查出验证码属于哪个用户，这样可以先拿用户锁
        # （和 submit / unbind 的加锁顺序相同）。
        owner = list(
            BindCode.objects.filter(code_hash=code_hash).values_list("user_id", flat=True)[:1]
        )
        if not owner:
            return ClaimResult(False, "code_invalid", "验证码不存在。", qq=qq_n)
        locks.lock_user(owner[0])
        bc = BindCode.objects.select_for_update().filter(code_hash=code_hash).first()
        if bc is None:
            return ClaimResult(False, "code_invalid", "验证码不存在。", qq=qq_n)
        if bc.used_at is not None or bc.invalidated_at is not None:
            return ClaimResult(False, "code_used", "验证码已使用或已作废。", qq=qq_n)
        if bc.expires_at <= now:
            return ClaimResult(False, "code_expired", "验证码已过期，请在 AA 上重新生成。", qq=qq_n)
        if bc.qq != qq_n:
            BindCode.objects.filter(
                pk=bc.pk, used_at__isnull=True, invalidated_at__isnull=True
            ).update(invalidated_at=now)
            audit.log(Action.CLAIM_FAILED, qq=bc.qq, target_user=bc.user,
                      reason="qq_mismatch", applicant_qq=qq_n)
            logger.info("qqbot: code for %s used by %s, invalidated",
                        mask_qq(bc.qq), mask_qq(qq_n))
            return ClaimResult(
                False, "qq_mismatch", "申请人的 QQ 与验证码登记的 QQ 不一致，验证码已作废。",
                qq=qq_n,
            )
        locks.lock_qqs(qq_n, _current_qq(bc.user_id))
        updated = BindCode.objects.filter(
            pk=bc.pk, used_at__isnull=True, invalidated_at__isnull=True
        ).update(used_at=now)
        if updated != 1:
            return ClaimResult(False, "code_used", "验证码已使用或已作废。", qq=qq_n)

        user = bc.user
        # The code wins: every other claim on this QQ goes away.
        # 以验证码为准：这个 QQ 上其他账号的绑定全部删除。
        for other in (
            Binding.objects.select_for_update().filter(qq=qq_n).exclude(user_id=user.pk)
        ):
            audit.log(
                Action.CONFLICT_RESOLVED,
                qq=qq_n,
                target_user=other.user,
                reason="takeover" if other.status == Binding.Status.VERIFIED else "conflict",
                previous_status=other.status,
                winner=user.username,
                via="code",
            )
            other.delete()

        binding = _user_binding_for_update(user)
        old_qq = ""
        if binding is None:
            binding = Binding(user=user, qq_changed_at=now)
        elif binding.qq != qq_n:
            old_qq = binding.qq
            binding.qq_changed_at = now
            binding.card_override = ""
        binding.qq = qq_n
        binding.nickname = bc.nickname
        binding.status = Binding.Status.VERIFIED
        binding.verified_via = Binding.VerifiedVia.CODE
        binding.verified_at = now
        binding.save()

        if old_qq:
            events.emit(Event.Kind.RECHECK, old_qq)
        audit.log(Action.VERIFY, qq=qq_n, target_user=user, via="code", old_qq=old_qq)
        events.refresh_binding(binding)
        if old_qq:
            events.refresh_qq(old_qq)
        logger.info("qqbot: %s verified %s by code", user, mask_qq(qq_n))
        return ClaimResult(True, "claimed", "验证成功。", qq=qq_n, binding=binding)


# --------------------------------------------------------------------------
# manager / member operations
# 管理员 / 成员操作
# --------------------------------------------------------------------------


def confirm(binding, actor, expected_qq=None) -> Result:
    """A QQ manager confirms a binding (makes it verified).

    ``expected_qq`` is the QQ the manager was looking at; the confirmation is
    refused (``qq_changed``) when the binding holds another QQ by now.

    QQ 管理员确认一个绑定（把它设为已验证）。

    ``expected_qq`` 是管理员当时在页面上看到的 QQ；如果这时绑定已经换成了
    别的 QQ，就拒绝确认（返回 ``qq_changed``）。
    """
    with transaction.atomic():
        user_id = Binding.objects.filter(pk=binding.pk).values_list("user_id", flat=True).first()
        if user_id is None:
            return Result(False, "not_found", gettext("Binding not found."))
        _lock_user_and_qqs(user_id)
        binding = Binding.objects.select_for_update().filter(pk=binding.pk).first()
        if binding is None:
            return Result(False, "not_found", gettext("Binding not found."))
        if _qq_changed(binding, expected_qq):
            return Result(False, "qq_changed", str(MSG_QQ_CHANGED))
        if binding.status == Binding.Status.VERIFIED:
            return Result(True, "unchanged", gettext("This binding is already verified."))
        if Binding.objects.filter(qq=binding.qq, status=Binding.Status.VERIFIED).exclude(
            pk=binding.pk
        ).exists():
            return Result(
                False, "taken",
                gettext("This QQ is already verified by another account and cannot be confirmed."),
            )
        for other in (
            Binding.objects.select_for_update()
            .filter(qq=binding.qq, status=Binding.Status.TRUSTED)
            .exclude(pk=binding.pk)
        ):
            audit.log(
                Action.CONFLICT_RESOLVED,
                actor=actor,
                qq=binding.qq,
                target_user=other.user,
                reason="conflict",
                previous_status=other.status,
                winner=binding.user.username,
                via="manager",
            )
            other.delete()
        binding.status = Binding.Status.VERIFIED
        binding.verified_via = Binding.VerifiedVia.MANAGER
        binding.verified_at = timezone.now()
        binding.save(update_fields=["status", "verified_via", "verified_at", "updated_at"])
        audit.log(Action.CONFIRM, actor=actor, qq=binding.qq, target_user=binding.user)
        events.refresh_binding(binding)
        return Result(True, "confirmed", gettext("Binding confirmed."))


def unbind(user, actor=None, forced=False, expected_qq=None) -> Result:
    """Remove the user's binding (member or, with ``forced``, a manager).

    ``expected_qq`` (managers): refuse with ``qq_changed`` when the binding
    holds another QQ than the one shown on the manager's page.

    解除用户的绑定（成员自己操作；带 ``forced`` 时是管理员强制解绑）。

    ``expected_qq``（管理员用）：如果绑定当前的 QQ 和管理员页面上显示的
    不一样，就拒绝操作并返回 ``qq_changed``。
    """
    now = timezone.now()
    with transaction.atomic():
        _lock_user_and_qqs(user.pk)
        binding = _user_binding_for_update(user)
        if binding is not None and _qq_changed(binding, expected_qq):
            return Result(False, "qq_changed", str(MSG_QQ_CHANGED))
        _invalidate_codes(user, now)
        if binding is None:
            return Result(False, "not_bound", gettext("No QQ is bound."))
        qq = binding.qq
        status = binding.status
        changed_at = binding.qq_changed_at
        binding.delete()
        events.emit(Event.Kind.RECHECK, qq)
        audit.log(
            Action.FORCE_UNBIND if forced else Action.UNBIND,
            actor=actor,
            qq=qq,
            target_user=user,
            status=status,
            # Keeps the rebind cooldown running after a member's own unbind
            # (see _last_qq_change).
            # 成员自己解绑后，换绑冷却仍继续计时（见 _last_qq_change）。
            qq_changed_at=changed_at.isoformat() if changed_at else None,
        )
        events.refresh_qq(qq)
        logger.info("qqbot: %s unbound %s", user, mask_qq(qq))
        return Result(True, "unbound", gettext("Unbound."))


def set_nickname(user, nickname) -> Result:
    try:
        nickname = validate_nickname(nickname)
    except ValidationError as exc:
        return Result(False, "invalid", " ".join(exc.messages))
    with transaction.atomic():
        locks.lock_user(user.pk)
        binding = _user_binding_for_update(user)
        if binding is None:
            return Result(False, "not_bound", gettext("No QQ is bound."))
        if binding.nickname == nickname:
            return Result(True, "unchanged", gettext("Nothing to change."))
        old = binding.nickname
        binding.nickname = nickname
        binding.save(update_fields=["nickname", "updated_at"])
        audit.log(Action.NICKNAME, actor=user, qq=binding.qq, target_user=user,
                  old=old, new=nickname)
        events.refresh_binding(binding)
        return Result(True, "nickname_updated", gettext("Nickname updated."))


def set_card_override(binding, card, actor) -> Result:
    """Set (or, with an empty string, clear) a manager-chosen group card.

    设置管理员指定的群名片（传空字符串则清除）。
    """
    card = clean_card(card or "")
    if byte_length(card) > CARD_MAX_BYTES:
        return Result(
            False, "invalid",
            gettext(
                "Group nickname is too long: at most %(max)d bytes "
                "(a Chinese character takes 3 bytes)."
            ) % {"max": CARD_MAX_BYTES},
        )
    with transaction.atomic():
        user_id = Binding.objects.filter(pk=binding.pk).values_list("user_id", flat=True).first()
        if user_id is None:
            return Result(False, "not_found", gettext("Binding not found."))
        locks.lock_user(user_id)
        binding = Binding.objects.select_for_update().filter(pk=binding.pk).first()
        if binding is None:
            return Result(False, "not_found", gettext("Binding not found."))
        if binding.card_override == card:
            return Result(True, "unchanged", gettext("Nothing to change."))
        old = binding.card_override
        binding.card_override = card
        binding.save(update_fields=["card_override", "updated_at"])
        audit.log(Action.CARD, actor=actor, qq=binding.qq, target_user=binding.user,
                  old=old, new=card)
        events.refresh_binding(binding)
        if not card:
            return Result(True, "card_cleared", gettext("Automatic group nickname restored."))
        return Result(True, "card_set", gettext("Group nickname set."))


def conflicts() -> list[tuple[str, list[Binding]]]:
    """QQs with no verified binding and at least two trusted bindings.

    找出没有已验证绑定、且至少有两个老成员免验证绑定的 QQ。
    """
    qqs = list(
        Binding.objects.values("qq")
        .annotate(
            trusted=Count("pk", filter=Q(status=Binding.Status.TRUSTED)),
            verified=Count("pk", filter=Q(status=Binding.Status.VERIFIED)),
        )
        .filter(trusted__gte=2, verified=0)
        .order_by("qq")
        .values_list("qq", flat=True)
    )
    if not qqs:
        return []
    grouped: dict[str, list[Binding]] = {qq: [] for qq in qqs}
    for b in (
        Binding.objects.filter(qq__in=qqs, status=Binding.Status.TRUSTED)
        .select_related("user__profile__main_character")
        .order_by("qq", "created_at", "pk")
    ):
        grouped[b.qq].append(b)
    return [(qq, grouped[qq]) for qq in qqs]


# Display states of one binding (services card, manage pages).
# 单个绑定的显示状态（用于服务页卡片和管理页面）。
STATE_VERIFIED = "verified"
STATE_TRUSTED = "trusted"
STATE_CONFLICT = "conflict"  # other accounts claim the same QQ, none verified / 其他账号也认领了这个 QQ，都未验证
STATE_TAKEN = "taken"  # another account verified the same QQ (it wins) / 另一个账号已验证这个 QQ（以它为准）


def binding_state(binding: Binding | None) -> str:
    """Display state of ``binding``; ``""`` for no binding. Read only.

    Mirrors the eligibility rules: a verified binding always wins, and two
    or more trusted bindings of one QQ without a verified one are a conflict.

    ``binding`` 的显示状态；没有绑定时返回 ``""``。只读，不修改数据。

    和资格规则保持一致：已验证的绑定总是优先；同一个 QQ 上有两个或更多
    老成员免验证绑定、却没有已验证绑定时，算作冲突。
    """
    if binding is None:
        return ""
    if binding.status == Binding.Status.VERIFIED:
        return STATE_VERIFIED
    others = set(
        Binding.objects.filter(qq=binding.qq).exclude(pk=binding.pk).values_list("status", flat=True)
    )
    if Binding.Status.VERIFIED in others:
        return STATE_TAKEN
    if others:
        return STATE_CONFLICT
    return STATE_TRUSTED


def on_user_deleted(user) -> None:
    """Called from ``pre_delete`` of ``User``: snapshot the QQ and tell the bot.

    在 ``User`` 的 ``pre_delete`` 信号里调用：记下该用户的 QQ 并通知机器人。
    """
    with transaction.atomic():
        _lock_user_and_qqs(user.pk)
        _invalidate_codes(user, timezone.now())
        binding = Binding.objects.filter(user_id=user.pk).first()
        if binding is None:
            return
        # Written after the deletion commits: a long bulk delete must not leave
        # an older event id invisible to the bot's cursor until after it has
        # moved past it.
        # 等删除提交之后再写事件：批量删除耗时较长时，事件编号不会被机器人的游标跳过。
        # robust: the user is already deleted by then; a failure is only
        # logged (the daily reconciliation catches it), never a 500.
        # robust：这时用户已经删掉了；写事件失败只记日志（每天的对账会兜底），
        # 不会让删除用户的页面报 500。
        qq = binding.qq
        transaction.on_commit(lambda: events.emit(Event.Kind.RECHECK, qq), robust=True)
        audit.log(Action.USER_DELETED, qq=binding.qq, target_user=user, status=binding.status)
