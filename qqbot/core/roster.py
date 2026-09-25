"""Group member lists ("rosters") reported by the bot.

机器人上报的群成员名单（roster）。
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import Binding, Config, QQGroup, RosterEntry, normalize_qq
from .access import chunked


def update_roster(group: QQGroup, qqs, now=None) -> dict:
    """Replace the group's roster with ``qqs`` (a complete member list).

    Invalid numbers are silently dropped. Returns ``{"added", "removed",
    "total"}``.

    用 ``qqs``（一份完整的成员列表）替换该群的群成员名单。

    无效的号码会被直接丢掉，不报错。返回 ``{"added", "removed", "total"}``。
    """
    now = now or timezone.now()
    wanted = {n for n in (normalize_qq(q) for q in (qqs or ())) if n}
    with transaction.atomic():
        existing = set(RosterEntry.objects.filter(group=group).values_list("qq", flat=True))
        to_remove = existing - wanted
        to_add = wanted - existing
        for chunk in chunked(sorted(to_remove)):
            RosterEntry.objects.filter(group=group, qq__in=chunk).delete()
        RosterEntry.objects.bulk_create(
            [RosterEntry(group=group, qq=qq, seen_at=now) for qq in sorted(to_add)],
            ignore_conflicts=True,
            batch_size=500,
        )
        RosterEntry.objects.filter(group=group).update(seen_at=now)
        QQGroup.objects.filter(pk=group.pk).update(last_roster_at=now)
        group.last_roster_at = now
    return {"added": len(to_add), "removed": len(to_remove), "total": len(wanted)}


def fresh_roster_cutoff(now=None, config=None):
    now = now or timezone.now()
    config = config or Config.get_solo()
    return now - timedelta(days=config.roster_max_age_days)


def in_fresh_roster(qq, now=None) -> bool:
    """True when ``qq`` is in the roster of an active group whose last complete
    roster is not older than ``Config.roster_max_age_days``.

    当 ``qq`` 出现在某个启用中的群的群成员名单里，并且该群最近一次完整名单
    不早于 ``Config.roster_max_age_days`` 天前时，返回 True。
    """
    qq = normalize_qq(qq)
    if not qq:
        return False
    return RosterEntry.objects.filter(
        qq=qq,
        group__is_active=True,
        group__last_roster_at__isnull=False,
        group__last_roster_at__gte=fresh_roster_cutoff(now),
    ).exists()


def unbound_roster(group=None):
    """Roster entries of active groups whose QQ has no binding at all.

    Returns a queryset of :class:`RosterEntry` (with ``group`` selected),
    ordered by group and QQ.

    启用中的群的群成员名单里，QQ 完全没有任何绑定的条目。

    返回 ``RosterEntry`` 的查询集（已通过 select_related 加载 ``group``），
    按群和 QQ 排序。
    """
    qs = RosterEntry.objects.filter(group__is_active=True)
    if group is not None:
        qs = qs.filter(group=group)
    return (
        qs.exclude(qq__in=Binding.objects.values("qq"))
        .select_related("group")
        .order_by("group__kind", "group__sort_order", "group__name", "qq")
    )
