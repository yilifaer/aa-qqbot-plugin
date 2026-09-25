"""Row locks that serialize binding writes per user and per QQ number.

Every write in ``core/bindings.py`` takes its locks in this order, inside
``transaction.atomic()``:

1. ``lock_user(user_id)`` -- at most once, for the one user whose binding or
   codes the operation changes.
2. ``lock_qqs(*qqs)`` -- at most once, for every QQ whose bindings the
   operation reads-then-writes (the target QQ and, when the user changes
   QQ, the old one).
3. Only then the user's own ``BindCode`` / ``Binding`` rows, and the
   bindings of other users on a QQ locked in step 2.

Keys are striped: users and QQs are hashed onto ``USER_STRIPES`` /
``QQ_STRIPES`` pre-created :class:`~qqbot.models.Lock` rows (created by
the initial migration), and all user rows sort before all QQ rows. Taking keys in
ascending order therefore gives one global lock order, so these locks cannot
deadlock with each other. Two unrelated users or QQs that share a stripe
merely wait for each other briefly.

The lock rows are not referenced by any foreign key, so inserting audit log
rows that point at a user never waits on these locks (unlike locking the
``auth_user`` row itself).

On SQLite ``select_for_update`` is a no-op; SQLite serializes writers anyway.
"""

from django.db import connection

from ..models import Lock, normalize_qq

USER_STRIPES = 64
QQ_STRIPES = 64


def user_key(user_id) -> int:
    return int(user_id) % USER_STRIPES


def qq_key(qq) -> int:
    return USER_STRIPES + int(qq) % QQ_STRIPES


def all_keys() -> list[int]:
    return list(range(USER_STRIPES + QQ_STRIPES))


def _acquire(keys) -> None:
    keys = sorted(set(keys))
    if not keys:
        return
    if not connection.in_atomic_block:  # pragma: no cover - programming error
        raise RuntimeError("qqbot locks must be taken inside transaction.atomic()")
    got = set(
        Lock.objects.select_for_update()
        .filter(pk__in=keys)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    missing = [k for k in keys if k not in got]
    if missing:
        # Only when the rows created by the migration are gone (e.g. after a
        # test database flush). Still in ascending order.
        Lock.objects.bulk_create([Lock(pk=k) for k in missing], ignore_conflicts=True)
        list(
            Lock.objects.select_for_update()
            .filter(pk__in=missing)
            .order_by("pk")
            .values_list("pk", flat=True)
        )


def lock_user(user_id) -> None:
    """Serialize binding / code writes of one user. Call first."""
    if user_id is not None:
        _acquire([user_key(user_id)])


def lock_qqs(*qqs) -> None:
    """Serialize binding writes on these QQs. Call once, after ``lock_user``."""
    _acquire([qq_key(n) for n in (normalize_qq(q) for q in qqs) if n])


def ensure_rows() -> None:
    """Create any missing lock rows (for tests that flush the database)."""
    Lock.objects.bulk_create([Lock(pk=k) for k in all_keys()], ignore_conflicts=True)
