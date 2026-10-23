"""Erase one person's personal data (``python manage.py qqbot_erase``).

The person is given by a QQ number and / or an AA username. Nothing is done
while a binding exists for that QQ or that user: it must be removed first.
Audit log entries about the person are anonymised. Entries on the same QQ
that belong to other accounts (for example someone who claimed it and was
force-unbound) are kept, because they record what those accounts did and
keep their force-unbind restriction and cooldown working; only the fields
that name the person are cleared from them.

删除某一个人的个人数据（``python manage.py qqbot_erase``）。

用 QQ 号和 / 或 AA 用户名指定这个人。只要这个 QQ 或这个用户还有绑定，就什么
都不做：必须先去掉绑定。关于这个人的审计日志会被匿名化。同一个 QQ 上属于其他
账号的记录（例如认领过这个 QQ、后来被强制解绑的人）会保留，因为它们记录的是
那些账号做过什么，并且让那些账号的强制解绑限制和换绑冷却继续有效；只把其中
指向这个人的字段清掉。
"""

from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q

from allianceauth.services.hooks import get_extension_logger

from ..models import AuditLog, Binding, BindCode, Event, RosterEntry
from . import events, locks

logger = get_extension_logger(__name__)

# Replaces the person's QQ / username inside other entries' details.
# 在其他审计日志的 detail 里，用它替换这个人的 QQ / 用户名。
ERASED = "[erased]"

# Audit log entries read per query while looking for the QQ / username.
# 查找 QQ / 用户名时，每次查询读取的审计日志条数。
AUDIT_CHUNK_SIZE = 1000

# Detail keys that name another person in an entry about someone else
# (written in core/bindings.py): other claimants, the winner of a conflict,
# and the QQ of an applicant who used someone else's code. Only these are
# cleared: other keys (via, op, status, group ids ...) may hold the same
# text by chance and must stay.
# 在关于别人的记录里指向另一个人的 detail 键（在 core/bindings.py 里写入）：
# 其他认领者、冲突中胜出的人、拿别人验证码申请的人的 QQ。只清这几个键：其他键
# （via、op、status、群号……）可能碰巧是同样的文字，必须保留。
USERNAME_KEYS = ("others", "winner")
QQ_KEYS = ("applicant_qq",)
# Only these actions write the keys above.
# 只有这些操作会写上面的键。
MENTION_ACTIONS = (
    AuditLog.Action.CONFLICT,
    AuditLog.Action.CONFLICT_RESOLVED,
    AuditLog.Action.CLAIM_FAILED,
)

# The whole detail of an audit log entry about the person.
# 关于这个人的审计日志，detail 整个换成这个。
ERASED_DETAIL = {"erased": True}


@dataclass
class Person:
    """Whom to erase. ``qq`` is normalised (or ``""``); ``user`` is the AA
    user named ``username`` if it still exists.

    要删除谁的数据。``qq`` 已经规范化（或为 ``""``）；``user`` 是名为
    ``username`` 的 AA 用户（如果这个用户还在）。
    """

    qq: str = ""
    username: str = ""
    user: User | None = None


class StillBound(Exception):
    """A binding still exists for the person's QQ or AA user.

    这个人的 QQ 或 AA 用户还有绑定。
    """

    def __init__(self, bindings):
        super().__init__("still bound")
        self.bindings = list(bindings)


def find_person(qq="", username="") -> Person:
    """``qq`` must already be normalised (``models.normalize_qq``).

    ``qq`` 必须已经规范化过（``models.normalize_qq``）。
    """
    username = (username or "").strip()
    user = User.objects.filter(username=username).first() if username else None
    return Person(qq=qq or "", username=username, user=user)


def _any(*parts):
    """OR of the given ``Q`` objects, skipping ``None``. Never matches
    everything: an empty QQ / username is never used as a filter.

    把给出的 ``Q`` 用 OR 连起来，跳过 ``None``。不会匹配全部数据：空的 QQ /
    用户名从来不会被当成过滤条件。
    """
    q = None
    for part in parts:
        if part is not None:
            q = part if q is None else q | part
    return q if q is not None else Q(pk__in=[])


def _about_q(person: Person) -> Q:
    """Audit log entries about the person: their AA user or username is the
    target, or the entry is on their QQ and has no target account. Entries on
    the QQ about other accounts are not included (see the module docstring).

    关于这个人的审计日志：目标是他的 AA 用户或用户名，或者记录在他的 QQ 上、
    并且没有目标账号。这个 QQ 上关于其他账号的记录不算在内（见模块说明）。
    """
    return _any(
        Q(target_user_id=person.user.pk) if person.user else None,
        Q(target_name=person.username) if person.username else None,
        Q(qq=person.qq, target_user__isnull=True, target_name="") if person.qq else None,
    )


def _actor_q(person: Person) -> Q:
    """Audit log entries in which the person acted.

    这个人作为操作者的审计日志。
    """
    return _any(
        Q(actor_id=person.user.pk) if person.user else None,
        Q(actor_name=person.username) if person.username else None,
    )


def _other_accounts_q(person: Person) -> Q:
    """Entries on the person's QQ about other accounts (kept as they are).

    这个人的 QQ 上关于其他账号的记录（原样保留）。
    """
    if not person.qq:
        return Q(pk__in=[])
    return Q(qq=person.qq) & ~_about_q(person)


def other_accounts(person: Person) -> list[str]:
    """Usernames of the other accounts with entries on the person's QQ.

    在这个人的 QQ 上有记录的其他账号的用户名。
    """
    names = (
        AuditLog.objects.filter(_other_accounts_q(person))
        .exclude(target_name="")
        .values_list("target_name", flat=True)
        .distinct()
    )
    return sorted(set(names))


def _codes(person: Person):
    return BindCode.objects.filter(
        _any(
            Q(qq=person.qq) if person.qq else None,
            Q(user_id=person.user.pk) if person.user else None,
        )
    )


def _events(person: Person):
    return Event.objects.filter(qq=person.qq) if person.qq else Event.objects.none()


def _roster(person: Person):
    return RosterEntry.objects.filter(qq=person.qq) if person.qq else RosterEntry.objects.none()


def bindings_for(person: Person):
    """Bindings (any status) of the person's QQ or AA user.

    这个人的 QQ 或 AA 用户的绑定（任何状态）。
    """
    return (
        Binding.objects.filter(
            _any(
                Q(qq=person.qq) if person.qq else None,
                Q(user_id=person.user.pk) if person.user else None,
            )
        )
        .select_related("user")
        .order_by("pk")
    )


def _scrub(detail, person: Person):
    """``detail`` with the person's username / QQ replaced by :data:`ERASED`
    under :data:`USERNAME_KEYS` / :data:`QQ_KEYS` only (lists included).
    Returns ``(new_detail, changed)``.

    只在 ``USERNAME_KEYS`` / ``QQ_KEYS`` 这几个键下（包括列表）把这个人的
    用户名 / QQ 换成 ``ERASED``。返回 ``(新的 detail, 是否有改动)``。
    """
    if not isinstance(detail, dict):
        return detail, False
    out = dict(detail)
    changed = False
    for keys, needle in ((USERNAME_KEYS, person.username), (QQ_KEYS, person.qq)):
        if not needle:
            continue
        for key in keys:
            value = out.get(key)
            if value == needle:
                out[key] = ERASED
                changed = True
            elif isinstance(value, list) and needle in value:
                out[key] = [ERASED if item == needle else item for item in value]
                changed = True
    return out, changed


def _mentions(person: Person) -> list[tuple[int, object]]:
    """``(pk, new detail)`` of the other audit log entries whose detail names
    the person (see :data:`USERNAME_KEYS`, :data:`QQ_KEYS`).

    detail 里指向这个人的其他审计日志：``(pk, 新的 detail)``（见
    ``USERNAME_KEYS``、``QQ_KEYS``）。
    """
    # Checked in Python, not with a database JSON lookup: JSON containment
    # is not available on every supported database.
    # 在 Python 里检查，不用数据库的 JSON 查询：不是每个支持的数据库都能做
    # JSON 包含查询。
    rows = (
        AuditLog.objects.filter(action__in=MENTION_ACTIONS)
        .exclude(_about_q(person))
        .order_by("pk")
        .values_list("pk", "detail")
    )
    found = []
    for pk, detail in rows.iterator(chunk_size=AUDIT_CHUNK_SIZE):
        new, changed = _scrub(detail, person)
        if changed:
            found.append((pk, new))
    return found


def _check_unbound(person: Person) -> None:
    bound = list(bindings_for(person))
    if bound:
        raise StillBound(bound)


def preview(person: Person) -> dict:
    """What :func:`erase` would change, as counts; changes nothing.

    Raises :class:`StillBound` when a binding exists.

    ``erase`` 会改动多少数据（只计数，不做任何修改）。

    还有绑定时抛出 ``StillBound``。
    """
    _check_unbound(person)
    return {
        "audit_about": AuditLog.objects.filter(_about_q(person)).count(),
        "audit_actor": AuditLog.objects.filter(_actor_q(person)).count(),
        "audit_mentions": len(_mentions(person)),
        "audit_kept": AuditLog.objects.filter(_other_accounts_q(person)).count(),
        "codes": _codes(person).count(),
        "events": _events(person).count(),
        "roster": _roster(person).count(),
    }


def erase(person: Person) -> dict:
    """Anonymise the person's audit log entries and delete their codes,
    events and member list entries, all in one transaction. Entries on the
    QQ about other accounts are only counted (``audit_kept``). Returns the
    counts (same keys as :func:`preview`).

    Raises :class:`StillBound` (and changes nothing) when a binding exists.

    匿名化这个人的审计日志，并删除他的验证码、事件和群成员名单记录，全部在
    一个事务里完成。这个 QQ 上关于其他账号的记录只计数（``audit_kept``）。
    返回各项数量（键和 ``preview`` 相同）。

    还有绑定时抛出 ``StillBound``，不做任何修改。
    """
    with transaction.atomic():
        # Same lock order as core/bindings.py: user first, then the QQ.
        # 加锁顺序和 core/bindings.py 相同：先用户，再 QQ。
        locks.lock_user(person.user.pk if person.user else None)
        locks.lock_qqs(person.qq)
        _check_unbound(person)
        # Both read the entries about the person, so they run before those
        # entries change.
        # 这两步都要读关于这个人的记录，所以要在修改这些记录之前执行。
        mentions = _mentions(person)
        counts = {
            "audit_kept": AuditLog.objects.filter(_other_accounts_q(person)).count(),
            # ERASED, not "": an empty actor is shown as "Bot / system".
            # 用 ERASED 而不是空字符串：空的操作者会显示成“机器人 / 系统”。
            "audit_actor": AuditLog.objects.filter(_actor_q(person)).update(
                actor=None, actor_name=ERASED
            ),
        }
        counts["audit_about"] = AuditLog.objects.filter(_about_q(person)).update(
            qq="", target_user=None, target_name="", detail=dict(ERASED_DETAIL)
        )
        counts["audit_mentions"] = len(mentions)
        for pk, detail in mentions:
            AuditLog.objects.filter(pk=pk).update(detail=detail)
        counts["codes"], _ = _codes(person).delete()
        counts["events"], _ = _events(person).delete()
        if counts["events"]:
            # A deleted event may be the unbind notice the bot has not
            # fetched yet; one "recheck all" (it carries no QQ) makes the
            # bot check every group again soon instead.
            # 删掉的事件里可能有机器人还没取走的解绑通知；改发一条“全部重查”
            # （不带 QQ），让机器人尽快把所有群重新检查一遍。
            events.emit(Event.Kind.RECHECK_ALL)
        counts["roster"], _ = _roster(person).delete()
    # Counts only: the log must not keep the QQ or the username.
    # 只记数量：日志里不能留下 QQ 或用户名。
    logger.info(
        "qqbot erase: %(audit_about)d audit log entries anonymised, %(audit_actor)d actors "
        "and %(audit_mentions)d mentions cleared; %(codes)d codes, %(events)d events and "
        "%(roster)d member list entries deleted",
        counts,
    )
    return counts
