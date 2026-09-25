"""Who may be in which QQ group (docs/SPEC.md 3.5)."""

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
    card: str | None  # only for allow


class _Evaluator:
    """Loads everything needed to judge a set of QQs in a constant number of
    queries (one per 500 QQs / users for each step), then decides per group.

    ``users`` are extra users (with ``profile__main_character`` loaded) that
    are judged by the user-level rules only, see :meth:`decide_user`.
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
        """Rule 3 (the user-level part); ``None`` means allow."""
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
        be allowed into ``group`` once their QQ is verified?"""
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
    keyed by ``group.pk``."""
    groups = active_groups() if groups is None else list(groups)
    if not groups:
        return {}
    ev = _Evaluator(groups, [binding.qq], now, config=config)
    return {g.pk: ev.decide(g, binding.qq) for g in groups}


def evaluate_many(groups, qqs, now=None, config=None) -> dict[str, dict[int, Decision]]:
    """Decisions for many QQs in many groups: ``{qq: {group.pk: Decision}}``.

    Same result as calling :func:`evaluate_binding` per QQ, with a constant
    number of queries (used by the daily reconciliation).
    """
    groups = list(groups)
    qqs = _normalized(qqs)
    if not groups or not qqs:
        return {qq: {} for qq in qqs}
    ev = _Evaluator(groups, qqs, now, config=config)
    return {qq: {g.pk: ev.decide(g, qq) for g in groups} for qq in qqs}


def groups_for_user(user, now=None) -> list[QQGroup]:
    """Active groups the user may currently join (for the member page).

    * The user has a live code (new member, or changing QQ): the groups to
      apply to with that code. Only the user-level rules count (active, main
      character, basic access, role groups), because a used code makes the
      QQ verified and so decides it regardless of other claims.
    * Otherwise, the user's binding decides: the groups where its QQ is
      allowed. A QQ in conflict, or a trusted binding shadowed by somebody
      else's verified one, gives ``[]`` (DESIGN 4.2: no group numbers then).
    * No binding and no live code: ``[]``.
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
