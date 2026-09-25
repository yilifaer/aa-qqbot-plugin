"""Event outbox for the bot, and fingerprint-based change detection."""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import Binding, BindCode, Config, Event
from .cards import render_card
from .access import CHUNK_SIZE
from .eligibility import active_groups, evaluate_binding, evaluate_many

EVENT_RETENTION_DAYS = 30
CODE_RETENTION_DAYS = 7

# Events are only handed to the bot once they are this old. Ids come from an
# auto-increment counter and are assigned at INSERT time, but events are
# written inside transactions that commit later; without the delay a lower
# id could become visible after the bot's cursor (``after``) already moved
# past it, and that event would be lost for good. Every transaction that
# emits events is short (well under a second), so 10 s is ample.
EVENT_VISIBILITY_DELAY = timedelta(seconds=10)


def emit(kind, qq="") -> Event:
    return Event.objects.create(kind=kind, qq=qq or "")


@dataclass
class EventPage:
    events: list
    last_id: int
    has_more: bool


def poll(after: int, limit: int = 200, now=None) -> EventPage:
    """Events with ``id > after`` for the bot's ``events`` endpoint.

    Only events older than :data:`EVENT_VISIBILITY_DELAY` are returned, and
    the page stops before the first younger one, so ``last_id`` never moves
    past an id whose transaction may still be in flight. ``last_id`` is the
    last returned id (``after`` when nothing is returned); ``has_more`` says
    that more events are ready right now.
    """
    now = now or timezone.now()
    after = max(0, int(after))
    limit = max(1, int(limit))
    qs = Event.objects.filter(id__gt=after).order_by("id")
    first_young = (
        qs.filter(created_at__gt=now - EVENT_VISIBILITY_DELAY).values_list("id", flat=True).first()
    )
    if first_young is not None:
        qs = qs.filter(id__lt=first_young)
    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(rows, rows[-1].id if rows else after, has_more)


def emit_groups_changed() -> None:
    emit(Event.Kind.GROUPS)
    emit(Event.Kind.RECHECK_ALL)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def _fingerprint(binding: Binding, decisions: dict, card: str) -> str:
    decision_part = json.dumps(
        [binding.qq] + [[pk, d.decision, d.reason] for pk, d in sorted(decisions.items())],
        separators=(",", ":"),
    )
    card_part = binding.qq + "\n" + card
    return _sha(decision_part) + _sha(card_part)


def compute_fingerprint(binding: Binding, groups=None, config=None) -> str:
    """``sha256(decisions)[:32] + sha256(card)[:32]``.

    Both halves include the QQ, so a changed QQ counts as a change.
    """
    decisions = evaluate_binding(binding, groups, config=config)
    return _fingerprint(binding, decisions, render_card(binding, config))


def refresh_kinds(binding: Binding, groups=None, config=None) -> list[str]:
    """Recompute the fingerprint, emit events for what changed, save it.
    Returns the kinds of the events written."""
    return _store_fingerprint(binding, compute_fingerprint(binding, groups, config))


def _store_fingerprint(binding: Binding, new: str) -> list[str]:
    old = binding.fingerprint or ""
    kinds = []
    if new[:32] != old[:32]:
        kinds.append(Event.Kind.RECHECK)
    if new[32:] != old[32:]:
        kinds.append(Event.Kind.CARD)
    for kind in kinds:
        emit(kind, binding.qq)
    if new != old:
        Binding.objects.filter(pk=binding.pk).update(fingerprint=new)
        binding.fingerprint = new
    return kinds


def refresh_binding(binding: Binding) -> bool:
    """Emit ``recheck`` / ``card`` events when the binding's decisions / card
    changed since the last call. Returns True when any event was written."""
    return bool(refresh_kinds(binding))


def refresh_qq(qq: str, exclude_pk=None) -> int:
    """Refresh every binding that claims ``qq``; returns the events written."""
    n = 0
    qs = Binding.objects.filter(qq=qq).select_related("user__profile__main_character")
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    for b in qs:
        n += len(refresh_kinds(b))
    return n


def refresh_user(user_or_id) -> bool:
    """Refresh the user's binding, if any."""
    user_id = getattr(user_or_id, "pk", user_or_id)
    if user_id is None:
        return False
    binding = (
        Binding.objects.filter(user_id=user_id)
        .select_related("user__profile__main_character")
        .first()
    )
    if binding is None:
        return False
    return refresh_binding(binding)


def refresh_all() -> int:
    """Refresh every binding (daily reconciliation); returns events written.

    Bindings are judged in batches of 500 with one evaluation per batch, so
    the reads take a constant number of queries per batch; only changed
    fingerprints cost an UPDATE (plus their events).
    """
    groups = active_groups()
    config = Config.get_solo()
    n = 0
    qs = Binding.objects.select_related("user__profile__main_character").order_by("pk")
    batch: list[Binding] = []
    for binding in qs.iterator(chunk_size=CHUNK_SIZE):
        batch.append(binding)
        if len(batch) >= CHUNK_SIZE:
            n += _refresh_batch(batch, groups, config)
            batch = []
    if batch:
        n += _refresh_batch(batch, groups, config)
    return n


def _refresh_batch(batch: list[Binding], groups, config) -> int:
    decisions = evaluate_many(groups, [b.qq for b in batch], config=config)
    n = 0
    for binding in batch:
        fp = _fingerprint(binding, decisions.get(binding.qq, {}), render_card(binding, config))
        n += len(_store_fingerprint(binding, fp))
    return n


def prune(now=None) -> dict:
    """Delete events older than 30 days and codes that expired or were used /
    invalidated more than 7 days ago."""
    now = now or timezone.now()
    events, _ = Event.objects.filter(
        created_at__lt=now - timedelta(days=EVENT_RETENTION_DAYS)
    ).delete()
    cutoff = now - timedelta(days=CODE_RETENTION_DAYS)
    codes, _ = BindCode.objects.filter(
        Q(expires_at__lt=cutoff) | Q(used_at__lt=cutoff) | Q(invalidated_at__lt=cutoff)
    ).delete()
    return {"events": events, "codes": codes}
