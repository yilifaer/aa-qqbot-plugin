"""Who may be in which QQ group (docs/SPEC.md 3.5).

谁可以待在哪个 QQ 群里（见 docs/SPEC.md 3.5）。
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from django.contrib.auth.models import User
from django.utils import timezone

from ..models import Binding, BindCode, Config, QQGroup, normalize_qq
from .access import base_access_user_ids, chunked, has_main_character, user_group_ids
from .cards import render_card

ALLOW, DENY, REVIEW = "allow", "deny", "review"

OK = "OK"
NOT_BOUND = "NOT_BOUND"
PENDING_VERIFY = "PENDING_VERIFY"
CONFLICT = "CONFLICT"
USER_INACTIVE = "USER_INACTIVE"
NO_MAIN = "NO_MAIN"
NO_ACCESS = "NO_ACCESS"
GROUP_ROLE_MISSING = "GROUP_ROLE_MISSING"
GROUP_MISCONFIGURED = "GROUP_MISCONFIGURED"


@dataclass(frozen=True)
class Decision:
    qq: str
    decision: str
    reason: str
    card: str | None  # only for allow / 仅在 allow 时有值


class _Evaluator:
    """Loads everything needed to judge a set of QQs in a constant number of
    queries (one per 500 QQs / users for each step), then decides per group.

    ``users`` are extra users (with ``profile__main_character`` loaded) that
    are judged by the user-level rules only, see :meth:`decide_user`.

    用固定数量的查询（每一步每 500 个 QQ / 用户查一次）加载判断一批 QQ
    所需的全部数据，然后按群逐个做出判断。

    ``users`` 是额外传入的用户（已加载 ``profile__main_character``），
    只按用户层面的规则判断，见 ``decide_user``。
    """

    def __init__(self, groups: list[QQGroup], qqs: list[str], now=None, *, config=None,
                 users=()):
        self.now = now or timezone.now()
        self.groups = groups
        self.qqs = qqs
        self.config = config if config is not None else Config.get_solo()

        by_qq: dict[str, list[Binding]] = defaultdict(list)
        for chunk in chunked(qqs):
            for b in (
                Binding.objects.filter(qq__in=chunk)
                .select_related("user", "user__profile", "user__profile__main_character")
                .order_by("pk")
            ):
                by_qq[b.qq].append(b)

        # The one binding that decides for each QQ (None = conflict).
        # 每个 QQ 最终起决定作用的那条绑定（None 表示冲突）。
        self.winner: dict[str, Binding | None] = {}
        for qq, rows in by_qq.items():
            verified = [b for b in rows if b.status == Binding.Status.VERIFIED]
            if verified:
                self.winner[qq] = verified[0]
            elif len(rows) >= 2:
                self.winner[qq] = None
            else:
                self.winner[qq] = rows[0]

        self.pending: set[str] = set()
        unbound = [qq for qq in qqs if qq not in by_qq]
        for chunk in chunked(unbound):
            self.pending.update(
                BindCode.objects.filter(
                    qq__in=chunk,
                    used_at__isnull=True,
                    invalidated_at__isnull=True,
                    expires_at__gt=self.now,
                ).values_list("qq", flat=True)
            )

        user_ids = {b.user_id for b in self.winner.values() if b is not None}
        user_ids |= {u.pk for u in users}
        self.access = base_access_user_ids(user_ids) if user_ids else set()

        role_groups = [g for g in groups if g.kind == QQGroup.Kind.ROLE]
        self.required: dict[int, set[int]] = {g.pk: set() for g in role_groups}
        self.user_groups: dict[int, set[int]] = {}
        if role_groups:
            through = QQGroup.required_groups.through
            for group_pk, auth_group_pk in through.objects.filter(
                qqgroup_id__in=[g.pk for g in role_groups]
            ).values_list("qqgroup_id", "group_id"):
                self.required[group_pk].add(auth_group_pk)
            if user_ids:
                self.user_groups = user_group_ids(user_ids)

        self._cards: dict[int, str] = {}

    def card(self, binding: Binding) -> str:
        if binding.pk not in self._cards:
            self._cards[binding.pk] = render_card(binding, self.config)
        return self._cards[binding.pk]

    def _user_rules(self, group: QQGroup, user) -> tuple[str, str] | None:
        """Rule 3 (the user-level part); ``None`` means allow.

        规则 3（用户层面的部分）；返回 ``None`` 表示允许。
        """
        if not user.is_active:
            return DENY, USER_INACTIVE
        if not has_main_character(user):
            return DENY, NO_MAIN
        if user.pk not in self.access:
            return DENY, NO_ACCESS
        if group.kind == QQGroup.Kind.ROLE:
            required = self.required.get(group.pk, set())
            if not required:
                return REVIEW, GROUP_MISCONFIGURED
            if not (required & self.user_groups.get(user.pk, set())):
                return DENY, GROUP_ROLE_MISSING
        return None

    def decide(self, group: QQGroup, qq: str) -> Decision:
        if qq not in self.winner:
            if qq in self.pending:
                return Decision(qq, DENY, PENDING_VERIFY, None)
            return Decision(qq, DENY, NOT_BOUND, None)
        binding = self.winner[qq]
        if binding is None:
            return Decision(qq, REVIEW, CONFLICT, None)
        denied = self._user_rules(group, binding.user)
        if denied:
            return Decision(qq, *denied, None)
        return Decision(qq, ALLOW, OK, self.card(binding))

    def decide_user(self, group: QQGroup, user, qq: str = "") -> Decision:
        """The user-level rules only (no QQ rules, no card): would ``user``
        be allowed into ``group`` once their QQ is verified?

        只检查用户层面的规则（不看 QQ 规则，也不生成群名片）：等 ``user``
        的 QQ 变成已验证后，他能不能进 ``group``？
        """
        denied = self._user_rules(group, user)
        if denied:
            return Decision(qq, *denied, None)
        return Decision(qq, ALLOW, OK, None)


def _normalized(qqs: Iterable) -> list[str]:
    seen: dict[str, None] = {}
    for q in qqs or ():
        n = normalize_qq(q)
        if n:
            seen.setdefault(n, None)
    return list(seen)


def evaluate(group: QQGroup, qqs: Iterable[str], now=None) -> dict[str, Decision]:
    """Decisions for ``qqs`` in ``group``, keyed by normalized QQ.

    Invalid QQ numbers are skipped (the API reports them as ``BAD_QQ``).
    Uses a constant number of queries regardless of how many QQs are given.

    对 ``qqs`` 在 ``group`` 中逐个做出判断，结果以规范化后的 QQ 为键。

    无效的 QQ 号会被跳过（API 会把它们报告为 ``BAD_QQ``）。
    不管传入多少个 QQ，查询次数都是固定的。
    """
    qqs = _normalized(qqs)
    if not qqs:
        return {}
    ev = _Evaluator([group], qqs, now)
    return {qq: ev.decide(group, qq) for qq in qqs}


def active_groups() -> list[QQGroup]:
    return list(QQGroup.objects.filter(is_active=True))


def evaluate_binding(binding: Binding, groups=None, now=None, config=None) -> dict[int, Decision]:
    """Decisions for the binding's QQ in every group (default: active groups),
    keyed by ``group.pk``.

    该绑定的 QQ 在每个群（默认：所有启用的群）中的判断结果，以 ``group.pk`` 为键。
    """
    groups = active_groups() if groups is None else list(groups)
    if not groups:
        return {}
    ev = _Evaluator(groups, [binding.qq], now, config=config)
    return {g.pk: ev.decide(g, binding.qq) for g in groups}


def evaluate_many(groups, qqs, now=None, config=None) -> dict[str, dict[int, Decision]]:
    """Decisions for many QQs in many groups: ``{qq: {group.pk: Decision}}``.

    Same result as calling :func:`evaluate_binding` per QQ, with a constant
    number of queries (used by the daily reconciliation).

    多个 QQ 在多个群中的判断结果：``{qq: {group.pk: Decision}}``。

    结果和对每个 QQ 分别调用 ``evaluate_binding`` 一样，但查询次数是固定的
    （每日对账时使用）。
    """
    groups = list(groups)
    qqs = _normalized(qqs)
    if not groups or not qqs:
        return {qq: {} for qq in qqs}
    ev = _Evaluator(groups, qqs, now, config=config)
    return {qq: {g.pk: ev.decide(g, qq) for g in groups} for qq in qqs}


def groups_for_user(user, now=None) -> list[QQGroup]:
    """Active groups the user may currently join (for the services card).

    * The user has a live code (new member, or changing QQ): the groups to
      apply to with that code. Only the user-level rules count (active, main
      character, basic access, role groups), because a used code makes the
      QQ verified and so decides it regardless of other claims.
    * Otherwise, the user's binding decides: the groups where its QQ is
      allowed. A QQ in conflict, or a trusted binding shadowed by somebody
      else's verified one, gives ``[]`` (DESIGN 4.2: no group numbers then).
    * No binding and no live code: ``[]``.

    用户当前可以加入的启用中的群（用于服务页面上的卡片）。

    * 用户有一个仍然有效的验证码（新成员，或正在更换 QQ）：返回可以用这个
      验证码申请加入的群。只看用户层面的规则（账号启用、主角色、基础访问权限、
      身份组小群），因为验证码一旦用掉，这个 QQ 就会变成已验证，不管别的账号怎么认领
      都以它为准。
    * 否则由用户的绑定决定：返回这个 QQ 被允许进入的群。如果 QQ 处于冲突状态，
      或者这是一条老成员免验证的绑定、但被别人的已验证绑定压过，则返回 ``[]``
      （DESIGN 4.2：这种情况下不显示群号）。
    * 既没有绑定也没有有效的验证码：返回 ``[]``。
    """
    if user is None or getattr(user, "pk", None) is None:
        return []
    now = now or timezone.now()
    has_code = BindCode.objects.filter(
        user_id=user.pk, used_at__isnull=True, invalidated_at__isnull=True, expires_at__gt=now
    ).exists()
    binding = None if has_code else Binding.objects.filter(user_id=user.pk).first()
    if not has_code and binding is None:
        return []
    groups = active_groups()
    if not groups:
        return []
    if has_code:
        fresh = (
            User.objects.select_related("profile", "profile__main_character")
            .filter(pk=user.pk)
            .first()
        )
        if fresh is None:
            return []
        ev = _Evaluator(groups, [], now, users=[fresh])
        return [g for g in groups if ev.decide_user(g, fresh).decision == ALLOW]
    ev = _Evaluator(groups, [binding.qq], now)
    winner = ev.winner.get(binding.qq)
    if winner is None or winner.pk != binding.pk:
        return []
    return [g for g in groups if ev.decide(g, binding.qq).decision == ALLOW]
