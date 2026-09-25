"""All write operations on bindings and codes (docs/SPEC.md 3.9).

Every function runs inside ``transaction.atomic()`` and takes its locks in
the order documented in ``core/locks.py`` (user, then QQs, then rows), so
concurrent operations serialize instead of deadlocking or racing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from allianceauth.services.hooks import get_extension_logger

from ..models import AuditLog, Binding, BindCode, Config, Event, normalize_qq
from . import audit, codes, events, locks
from .cards import CARD_MAX_BYTES, byte_length, clean_card
from .roster import in_fresh_roster
from .util import mask_qq, validate_nickname

logger = get_extension_logger(__name__)

Action = AuditLog.Action

CODE_RATE_LIMIT = 5  # codes per user ...
CODE_RATE_WINDOW = 3600  # ... per this many seconds
TRUSTED_RATE_LIMIT = 5  # code-free (trusted) binds / QQ changes per user per window

MSG_TAKEN = "该 QQ 已被绑定，如有疑问请联系 QQ 管理员。"
MSG_QQ_CHANGED = "这个成员的 QQ 刚刚变了，页面上的信息已经过时。请重新打开页面核对后再操作。"


def _qq_changed(binding, expected_qq) -> bool:
    """True when a manager acted on a page that showed another QQ.

    ``expected_qq=None`` skips the check; anything else (also ``""``) must
    equal the binding's current QQ. Member rebinds keep the binding's pk, so
    the pk alone does not say which QQ the manager reviewed.
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
    code: str | None = None  # plain-text code; only returned once
    expires_at: datetime | None = None
    retry_after: timedelta | None = None  # remaining cooldown


@dataclass
class ClaimResult(Result):
    qq: str = ""
    binding: Binding | None = None


def _live_codes(user, now):
    return BindCode.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True, expires_at__gt=now
    )


def _invalidate_codes(user, now) -> int:
    """Invalidate every unused code of the user (live or not yet expired)."""
    return BindCode.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=now)


def _format_remaining(delta: timedelta) -> str:
    minutes = max(1, int((delta.total_seconds() + 59) // 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _count(key: str, limit: int) -> bool:
    """Count one attempt in a fixed window; True when over ``limit``."""
    cache.add(key, 0, CODE_RATE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, CODE_RATE_WINDOW)
        count = 1
    return count > limit


def _rate_limited(user) -> bool:
    """Count one code generation; True when the user is over the limit."""
    return _count(f"qqbot:codes:{user.pk}", CODE_RATE_LIMIT)


def _trusted_rate_limited(user) -> bool:
    """Count one code-free (trusted) bind; True when the user is over the
    limit. Separate from the code counter so failed code attempts never
    block an old member."""
    return _count(f"qqbot:trusted:{user.pk}", TRUSTED_RATE_LIMIT)


def _last_qq_change(user, existing, qq_n, cooldown: timedelta, now):
    """When the user last changed their QQ, for the rebind cooldown.

    With a binding that is ``qq_changed_at``. Without one, the cooldown of a
    binding the member removed themselves (``UNBIND``) still runs, so
    "unbind, then submit another QQ" cannot skip it; binding the *same* QQ
    again is not a change (an unbind by mistake can be undone at once). The
    time is kept in that audit row's ``detail``. A manager's forced unbind
    does not carry it over.
    """
    if existing is not None:
        return existing.qq_changed_at
    # qq_changed_at <= unbind time, so older unbinds cannot matter.
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
    ``None`` when it is already over (for the member pages)."""
    now = now or timezone.now()
    config = config or Config.get_solo()
    if not qq_changed_at or not config.rebind_cooldown_hours:
        return None
    ends = qq_changed_at + timedelta(hours=config.rebind_cooldown_hours)
    return ends if ends > now else None


def _user_binding_for_update(user):
    # No select_related together with select_for_update: PostgreSQL refuses
    # FOR UPDATE on the nullable side of an outer join.
    user_id = getattr(user, "pk", user)
    return Binding.objects.select_for_update().filter(user_id=user_id).first()


def _current_qq(user_id) -> str:
    """The user's bound QQ (plain read). Only the user's own operations change
    it, and they hold the user lock, so it is stable once that lock is held."""
    return Binding.objects.filter(user_id=user_id).values_list("qq", flat=True).first() or ""


def _lock_user_and_qqs(user_id, *qqs) -> None:
    """Lock the user, then the given QQs plus the user's current QQ."""
    locks.lock_user(user_id)
    locks.lock_qqs(*qqs, _current_qq(user_id))


def _card_event(binding, kinds):
    if Event.Kind.CARD not in kinds:
        events.emit(Event.Kind.CARD, binding.qq)


# --------------------------------------------------------------------------
# submit / codes
# --------------------------------------------------------------------------


def submit(user, qq, nickname, now=None) -> SubmitResult:
    """A member submits (or changes) their QQ and nickname."""
    now = now or timezone.now()
    qq_n = normalize_qq(qq)
    if not qq_n:
        return SubmitResult(False, "invalid", "请输入正确的 QQ 号（5–11 位数字，不能以 0 开头）。")
    try:
        nickname = validate_nickname(nickname)
    except ValidationError as exc:
        return SubmitResult(False, "invalid", " ".join(exc.messages))

    config = Config.get_solo()
    with transaction.atomic():
        _lock_user_and_qqs(user.pk, qq_n)
        existing = _user_binding_for_update(user)

        # Same QQ as the current binding: only the nickname can change.
        if existing is not None and existing.qq == qq_n:
            if existing.nickname == nickname:
                return SubmitResult(True, "unchanged", "没有需要修改的内容。", binding=existing)
            old_nickname = existing.nickname
            existing.nickname = nickname
            existing.save(update_fields=["nickname", "updated_at"])
            audit.log(Action.NICKNAME, actor=user, qq=qq_n, target_user=user,
                      old=old_nickname, new=nickname)
            _card_event(existing, events.refresh_kinds(existing))
            return SubmitResult(True, "nickname_updated", "昵称已更新。", binding=existing)

        if Binding.objects.filter(qq=qq_n, status=Binding.Status.VERIFIED).exclude(
            user_id=user.pk
        ).exists():
            return SubmitResult(False, "taken", MSG_TAKEN)

        cooldown = timedelta(hours=config.rebind_cooldown_hours)
        changed_at = _last_qq_change(user, existing, qq_n, cooldown, now) if cooldown else None
        if changed_at:
            ends = changed_at + cooldown
            if now < ends:
                remaining = ends - now
                return SubmitResult(
                    False,
                    "cooldown",
                    f"换绑太频繁，请在 {_format_remaining(remaining)}后再试。",
                    retry_after=remaining,
                )

        if in_fresh_roster(qq_n, now):
            if _trusted_rate_limited(user):
                return SubmitResult(
                    False, "rate_limited",
                    f"绑定或换绑太频繁（每小时最多 {TRUSTED_RATE_LIMIT} 次），请稍后再试。",
                )
            return _submit_trusted(user, existing, qq_n, nickname, now)

        # Pending: the current binding stays untouched until the code is used.
        if _rate_limited(user):
            return SubmitResult(
                False, "rate_limited", f"生成验证码太频繁（每小时最多 {CODE_RATE_LIMIT} 次），请稍后再试。"
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
            except IntegrityError:  # hash collision with an old code
                continue
        else:  # pragma: no cover - practically impossible
            raise RuntimeError("could not generate a unique code")
        audit.log(Action.CODE, actor=user, qq=qq_n, target_user=user, expires_at=expires_at)
        logger.info("qqbot: code generated for %s / %s", user, mask_qq(qq_n))
        return SubmitResult(
            True,
            "pending",
            "请在有效期内申请加入 QQ 群，并在「验证信息」里填写验证码。",
            binding=existing,
            code=code,
            expires_at=expires_at,
        )


def _submit_trusted(user, existing, qq_n, nickname, now) -> SubmitResult:
    """Old member: the QQ is in a fresh roster, bind without a code."""
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
            "这个 QQ 同时被其他账号认领，请联系 QQ 管理员处理。",
            binding=binding,
        )
    return SubmitResult(True, "trusted", "绑定成功（老成员免验证）。", binding=binding)


def live_code(user, now=None) -> BindCode | None:
    now = now or timezone.now()
    return _live_codes(user, now).order_by("-pk").first()


def cancel_code(user, now=None) -> int:
    """Invalidate the user's live code(s). Returns how many were invalidated."""
    now = now or timezone.now()
    with transaction.atomic():
        locks.lock_user(user.pk)
        return _invalidate_codes(user, now)


# --------------------------------------------------------------------------
# claim (bot API)
# --------------------------------------------------------------------------


def claim(qq, text, now=None) -> ClaimResult:
    """The bot reports that ``qq`` sent ``text`` (e.g. a join-request comment)."""
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
        logger.warning("qqbot: claim for %s hit an integrity error, retrying", mask_qq(qq_n))
        return _claim(qq_n, code_hash, now)


def _claim(qq_n, code_hash, now) -> ClaimResult:
    with transaction.atomic():
        # Find the code's owner without locking the code row, so the user
        # lock can be taken first (same order as submit / unbind).
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
# --------------------------------------------------------------------------


def confirm(binding, actor, expected_qq=None) -> Result:
    """A QQ manager confirms a binding (makes it verified).

    ``expected_qq`` is the QQ the manager was looking at; the confirmation is
    refused (``qq_changed``) when the binding holds another QQ by now.
    """
    with transaction.atomic():
        user_id = Binding.objects.filter(pk=binding.pk).values_list("user_id", flat=True).first()
        if user_id is None:
            return Result(False, "not_found", "绑定不存在。")
        _lock_user_and_qqs(user_id)
        binding = Binding.objects.select_for_update().filter(pk=binding.pk).first()
        if binding is None:
            return Result(False, "not_found", "绑定不存在。")
        if _qq_changed(binding, expected_qq):
            return Result(False, "qq_changed", MSG_QQ_CHANGED)
        if binding.status == Binding.Status.VERIFIED:
            return Result(True, "unchanged", "该绑定已经是已验证状态。")
        if Binding.objects.filter(qq=binding.qq, status=Binding.Status.VERIFIED).exclude(
            pk=binding.pk
        ).exists():
            return Result(False, "taken", "该 QQ 已被其他账号验证，不能确认。")
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
        return Result(True, "confirmed", "已确认绑定。")


def unbind(user, actor=None, forced=False, expected_qq=None) -> Result:
    """Remove the user's binding (member or, with ``forced``, a manager).

    ``expected_qq`` (managers): refuse with ``qq_changed`` when the binding
    holds another QQ than the one shown on the manager's page.
    """
    now = timezone.now()
    with transaction.atomic():
        _lock_user_and_qqs(user.pk)
        binding = _user_binding_for_update(user)
        if binding is not None and _qq_changed(binding, expected_qq):
            return Result(False, "qq_changed", MSG_QQ_CHANGED)
        _invalidate_codes(user, now)
        if binding is None:
            return Result(False, "not_bound", "没有绑定 QQ。")
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
            qq_changed_at=changed_at.isoformat() if changed_at else None,
        )
        events.refresh_qq(qq)
        logger.info("qqbot: %s unbound %s", user, mask_qq(qq))
        return Result(True, "unbound", "已解除绑定。")


def set_nickname(user, nickname) -> Result:
    try:
        nickname = validate_nickname(nickname)
    except ValidationError as exc:
        return Result(False, "invalid", " ".join(exc.messages))
    with transaction.atomic():
        locks.lock_user(user.pk)
        binding = _user_binding_for_update(user)
        if binding is None:
            return Result(False, "not_bound", "没有绑定 QQ。")
        if binding.nickname == nickname:
            return Result(True, "unchanged", "没有需要修改的内容。")
        old = binding.nickname
        binding.nickname = nickname
        binding.save(update_fields=["nickname", "updated_at"])
        audit.log(Action.NICKNAME, actor=user, qq=binding.qq, target_user=user,
                  old=old, new=nickname)
        events.refresh_binding(binding)
        return Result(True, "nickname_updated", "昵称已更新。")


def set_card_override(binding, card, actor) -> Result:
    """Set (or, with an empty string, clear) a manager-chosen group card."""
    card = clean_card(card or "")
    if byte_length(card) > CARD_MAX_BYTES:
        return Result(
            False, "invalid", f"群名片太长：最多 {CARD_MAX_BYTES} 字节（一个汉字占 3 字节）。"
        )
    with transaction.atomic():
        user_id = Binding.objects.filter(pk=binding.pk).values_list("user_id", flat=True).first()
        if user_id is None:
            return Result(False, "not_found", "绑定不存在。")
        locks.lock_user(user_id)
        binding = Binding.objects.select_for_update().filter(pk=binding.pk).first()
        if binding is None:
            return Result(False, "not_found", "绑定不存在。")
        if binding.card_override == card:
            return Result(True, "unchanged", "没有需要修改的内容。")
        old = binding.card_override
        binding.card_override = card
        binding.save(update_fields=["card_override", "updated_at"])
        audit.log(Action.CARD, actor=actor, qq=binding.qq, target_user=binding.user,
                  old=old, new=card)
        events.refresh_binding(binding)
        return Result(True, "card_cleared" if not card else "card_set",
                      "已恢复自动群名片。" if not card else "群名片已设置。")


def conflicts() -> list[tuple[str, list[Binding]]]:
    """QQs with no verified binding and at least two trusted bindings."""
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


# Display states of one binding (member pages, services card).
STATE_VERIFIED = "verified"
STATE_TRUSTED = "trusted"
STATE_CONFLICT = "conflict"  # other accounts claim the same QQ, none verified
STATE_TAKEN = "taken"  # another account verified the same QQ (it wins)


def binding_state(binding: Binding | None) -> str:
    """Display state of ``binding``; ``""`` for no binding. Read only.

    Mirrors the eligibility rules: a verified binding always wins, and two
    or more trusted bindings of one QQ without a verified one are a conflict.
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
    """Called from ``pre_delete`` of ``User``: snapshot the QQ and tell the bot."""
    with transaction.atomic():
        _lock_user_and_qqs(user.pk)
        _invalidate_codes(user, timezone.now())
        binding = Binding.objects.filter(user_id=user.pk).first()
        if binding is None:
            return
        events.emit(Event.Kind.RECHECK, binding.qq)
        audit.log(Action.USER_DELETED, qq=binding.qq, target_user=user, status=binding.status)
