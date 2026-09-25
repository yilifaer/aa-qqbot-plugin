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

按用户、按 QQ 号把绑定写操作串行化（排队执行）的行锁。

``core/bindings.py`` 里的每个写操作都在 ``transaction.atomic()`` 内，
按下面的顺序加锁：

1. ``lock_user(user_id)`` -- 最多调用一次，锁住本次操作要修改其绑定或
   验证码的那一个用户。
2. ``lock_qqs(*qqs)`` -- 最多调用一次，锁住本次操作要“先读后写”其绑定的
   所有 QQ（目标 QQ；用户换 QQ 时还包括旧 QQ）。
3. 之后才锁该用户自己的 ``BindCode`` / ``Binding`` 行，以及第 2 步已锁住
   的 QQ 上其他用户的绑定。

锁的键是分条（striped）的：用户和 QQ 会被哈希到 ``USER_STRIPES`` /
``QQ_STRIPES`` 个预先建好的 :class:`~qqbot.models.Lock` 行上（由初始迁移
创建），并且所有用户行都排在所有 QQ 行前面。所以只要按键的升序加锁，
就形成一个全局统一的加锁顺序，这些锁之间不会互相死锁。两个不相关的用户
或 QQ 如果落在同一条上，只会互相短暂等待。

这些锁行没有被任何外键引用，所以插入指向某个用户的审计日志行时，
不会去等这些锁（直接锁 ``auth_user`` 行就会等）。

在 SQLite 上 ``select_for_update`` 不起作用；不过 SQLite 本来就会让写操作
排队执行。
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
    if not connection.in_atomic_block:  # pragma: no cover - programming error / 属于编程错误
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
        # 只有迁移创建的锁行丢失时才会走到这里（例如测试清空数据库之后）。
        # 这里仍然按升序加锁。
        Lock.objects.bulk_create([Lock(pk=k) for k in missing], ignore_conflicts=True)
        list(
            Lock.objects.select_for_update()
            .filter(pk__in=missing)
            .order_by("pk")
            .values_list("pk", flat=True)
        )


def lock_user(user_id) -> None:
    """Serialize binding / code writes of one user. Call first.

    让同一个用户的绑定 / 验证码写操作排队执行。必须最先调用。
    """
    if user_id is not None:
        _acquire([user_key(user_id)])


def lock_qqs(*qqs) -> None:
    """Serialize binding writes on these QQs. Call once, after ``lock_user``.

    让这些 QQ 上的绑定写操作排队执行。只调用一次，并且要在 ``lock_user`` 之后。
    """
    _acquire([qq_key(n) for n in (normalize_qq(q) for q in qqs) if n])


def ensure_rows() -> None:
    """Create any missing lock rows (for tests that flush the database).

    补建缺失的锁行（给会清空数据库的测试用）。
    """
    Lock.objects.bulk_create([Lock(pk=k) for k in all_keys()], ignore_conflicts=True)
