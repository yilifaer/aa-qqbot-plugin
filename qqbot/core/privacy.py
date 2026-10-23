"""Erase one person's personal data (``python manage.py qqbot_erase``).

The person is given by a QQ number and / or an AA username. Nothing is done
while a binding exists for that QQ or that user: it must be unbound (or the
AA user deleted) first, so the bot has already been told. Audit log entries
are kept but anonymised, so the log still shows what happened to whom else.

删除某一个人的个人数据（``python manage.py qqbot_erase``）。

用 QQ 号和 / 或 AA 用户名指定这个人。只要这个 QQ 或这个用户还有绑定，就什么
都不做：必须先解绑（或者删除这个 AA 用户），这样机器人已经收到通知。审计日志
保留，但会匿名化，这样日志里仍然看得到其他人身上发生过什么。
"""

from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Q

from allianceauth.services.hooks import get_extension_logger

from ..models import AuditLog, Binding, BindCode, Event, RosterEntry
from . import locks

logger = get_extension_logger(__name__)

# Replaces the person's QQ / username inside other entries' details.
# 在其他审计日志的 detail 里，用它替换这个人的 QQ / 用户名。
ERASED = "[erased]"

# Audit log entries read per query while looking for the QQ / username.
# 查找 QQ / 用户名时，每次查询读取的审计日志条数。
AUDIT_CHUNK_SIZE = 1000

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
    """Audit log entries about the person.

    关于这个人的审计日志。
    """
    return _any(
        Q(qq=person.qq) if person.qq else None,
        Q(target_user_id=person.user.pk) if person.user else None,
        Q(target_name=person.username) if person.username else None,
    )


def _actor_q(person: Person) -> Q:
    """Audit log entries in which the person acted: by their user or
    username, and entries about the person in which the target acted on
    themselves (e.g. their own bind, found by ``--qq`` alone).

    这个人作为操作者的审计日志：按他的用户或用户名找到的记录，以及关于这个人、
    并且是目标用户自己操作的记录（例如他自己的绑定记录，只给 ``--qq`` 时也能找到）。
    """
    self_acted = Q(actor_id=F("target_user_id")) | (
        Q(actor_name=F("target_name")) & ~Q(actor_name="")
    )
    return _any(
        Q(actor_id=person.user.pk) if person.user else None,
        Q(actor_name=person.username) if person.username else None,
        _about_q(person) & self_acted,
    )


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


def _scrub(value, needles):
    """``value`` with every string equal to one of ``needles`` replaced by
    :data:`ERASED`, recursively in dicts and lists. Returns
    ``(new_value, changed)``.

    把 ``value`` 里每个等于 ``needles`` 之一的字符串换成 ``ERASED``（会递归
    处理字典和列表）。返回 ``(新值, 是否有改动)``。
    """
    if isinstance(value, str):
        return (ERASED, True) if value in needles else (value, False)
    if isinstance(value, dict):
        changed = False
        out = {}
        for key, item in value.items():
            out[key], hit = _scrub(item, needles)
            changed = changed or hit
        return out, changed
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            new, hit = _scrub(item, needles)
            out.append(new)
            changed = changed or hit
        return out, changed
    return value, False


def _mentions(person: Person) -> list[tuple[int, object]]:
    """``(pk, new detail)`` of the other audit log entries whose detail holds
    the person's QQ or username.

    detail 里含有这个人的 QQ 或用户名的其他审计日志：``(pk, 新的 detail)``。
    """
    needles = {s for s in (person.qq, person.username) if s}
    # Checked in Python, not with a database text search: how JSON text is
    # stored (escaped or not) differs between database backends.
    # 在 Python 里检查，不用数据库的文本搜索：不同数据库保存 JSON 文本的方式
    # （是否转义）不一样。
    rows = (
        AuditLog.objects.exclude(_about_q(person))
        .order_by("pk")
        .values_list("pk", "detail")
    )
    found = []
    for pk, detail in rows.iterator(chunk_size=AUDIT_CHUNK_SIZE):
        new, changed = _scrub(detail, needles)
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
        "codes": _codes(person).count(),
        "events": _events(person).count(),
        "roster": _roster(person).count(),
    }


def erase(person: Person) -> dict:
    """Anonymise the person's audit log entries and delete their codes,
    events and member list entries, all in one transaction. Returns the
    counts (same keys as :func:`preview`).

    Raises :class:`StillBound` (and changes nothing) when a binding exists.

    匿名化这个人的审计日志，并删除他的验证码、事件和群成员名单记录，全部在
    一个事务里完成。返回各项数量（键和 ``preview`` 相同）。

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
            "audit_actor": AuditLog.objects.filter(_actor_q(person)).update(
                actor=None, actor_name=""
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
