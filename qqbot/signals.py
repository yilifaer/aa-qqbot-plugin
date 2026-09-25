"""Signal receivers that keep the bot informed (docs/SPEC.md section 7).

Every receiver only *schedules* work with ``transaction.on_commit`` so the
bot is told about a change once the change is visible to other connections.
The scheduled work runs through :func:`_safe`, and every receiver body is
wrapped by :func:`_never_raise`: several of these signals fire inside Alliance
Auth's own code paths (e.g. ``state_changed`` during state assignment, the
``User`` save/delete), and an exception escaping from here would break AA.
Anything we miss is caught by the daily reconciliation task.

Within one transaction the same piece of work is scheduled at most once (AA
saves a profile several times while reassigning a state, for example).

让机器人及时得知变化的信号接收器（docs/SPEC.md 第 7 节）。

每个接收器只用 ``transaction.on_commit`` *安排* 工作，这样要等改动对其他
数据库连接可见之后，才会通知机器人。安排好的工作通过 :func:`_safe` 执行，
每个接收器本身也包在 :func:`_never_raise` 里：这些信号有好几个是在
Alliance Auth 自己的代码里触发的（例如分配 State 时的 ``state_changed``、
``User`` 的保存 / 删除），如果异常从这里漏出去，就会把 AA 弄坏。这里漏掉
的情况由每天的对账任务兜底。

同一个事务里，同一件工作最多只安排一次（例如 AA 在重新分配 State 时会
把 profile 保存好几次）。
"""

import functools

from django.contrib.auth.models import Group, Permission, User
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
from django.dispatch import receiver

from allianceauth.authentication.models import State, UserProfile
from allianceauth.authentication.signals import state_changed
from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.hooks import get_extension_logger

from .core import access, bindings, events
from .models import Event, QQGroup

logger = get_extension_logger(__name__)

_KEY_ATTR = "_qqbot_on_commit_key"
_RECHECK_ALL_KEY = ("recheck_all",)

# Attribute names used to carry snapshots from pre_* to post_* signals.
# 用来把快照从 pre_* 信号带到 post_* 信号的属性名。
_OLD_ACTIVE = "_qqbot_old_is_active"
_OLD_PROFILE = "_qqbot_old_profile"
_CLEARED_IDS = "_qqbot_cleared_ids"


# --------------------------------------------------------------------------
# helpers
# 辅助函数
# --------------------------------------------------------------------------


def _safe(func, *args, **kwargs):
    """Run ``func`` in its own transaction; log and swallow any exception.

    在独立事务中运行 ``func``；出任何异常都只记日志，不往外抛。
    """
    try:
        with transaction.atomic():
            return func(*args, **kwargs)
    except Exception:
        logger.exception("qqbot: %s failed", getattr(func, "__name__", func))
        return None


def _never_raise(receiver_func):
    """Signal receivers must never raise into the sender.

    信号接收器绝不能把异常抛给发送方。
    """

    @functools.wraps(receiver_func)
    def wrapper(*args, **kwargs):
        try:
            receiver_func(*args, **kwargs)
        except Exception:
            logger.exception("qqbot: signal receiver %s failed", receiver_func.__name__)

    return wrapper


def _already_scheduled(key) -> bool:
    """True when a callback with ``key`` is pending in the current transaction.

    当前事务里已经有一个 ``key`` 相同、正在等待执行的回调时，返回 True。
    """
    try:
        conn = transaction.get_connection()
        if not conn.in_atomic_block:
            return False
        return any(getattr(entry[1], _KEY_ATTR, None) == key for entry in conn.run_on_commit)
    except Exception:  # private API changed: just schedule again (harmless) / 私有 API 变了：再安排一次即可（无害）
        return False


def _schedule(key, func, *args):
    """``on_commit(_safe(func, *args))``, at most once per ``key`` and transaction.

    即 ``on_commit(_safe(func, *args))``，同一个事务里每个 ``key`` 最多安排一次。
    """
    if _already_scheduled(key):
        return

    def callback():
        _safe(func, *args)

    setattr(callback, _KEY_ATTR, key)
    transaction.on_commit(callback)


def schedule_refresh_user(user_id) -> None:
    if user_id is None:
        return
    _schedule(("user", int(user_id)), events.refresh_user, int(user_id))


def schedule_recheck_all() -> None:
    _schedule(_RECHECK_ALL_KEY, events.emit, Event.Kind.RECHECK_ALL)


def _basic_access_perm_id():
    perms = Permission.objects.filter(**_has_basic_access_filter())
    return perms.values_list("pk", flat=True).first()


def _is_basic_access(perm) -> bool:
    return (
        perm is not None
        and perm.codename == access.BASIC_ACCESS
        and perm.content_type.app_label == access.APP_LABEL
    )


def _group_matters(group) -> bool:
    """True when membership of ``group`` can change a QQ decision: the group
    carries basic_access or is required by a role QQ group.

    当 ``group`` 的成员变化可能改变 QQ 的判断结果时返回 True：该组带有
    basic_access 权限，或者是某个身份组小群要求的组。
    """
    if group is None or group.pk is None:
        return False
    if _group_has_basic_access(group):
        return True
    return QQGroup.objects.filter(required_groups=group).exists()


def _has_basic_access_filter():
    return {"content_type__app_label": access.APP_LABEL, "codename": access.BASIC_ACCESS}


def _group_has_basic_access(group) -> bool:
    return group.pk is not None and group.permissions.filter(**_has_basic_access_filter()).exists()


def _state_has_basic_access(state) -> bool:
    if state is None or state.pk is None:
        return False
    return state.permissions.filter(**_has_basic_access_filter()).exists()


def _changed_ids(instance, action, pk_set):
    """The related ids an m2m change touched (``pre_clear`` snapshots them).

    这次 m2m 变化涉及的关联 id（``pre_clear`` 时会先把它们存成快照）。
    """
    if action == "post_clear":
        return getattr(instance, _CLEARED_IDS, None) or set()
    return pk_set or set()


# --------------------------------------------------------------------------
# AA state
# AA 的 State
# --------------------------------------------------------------------------


@receiver(state_changed, dispatch_uid="qqbot_state_changed")
@_never_raise
def on_state_changed(sender, user=None, state=None, **kwargs):
    schedule_refresh_user(getattr(user, "pk", None))


# --------------------------------------------------------------------------
# User: is_active, delete
# User：is_active、删除
# --------------------------------------------------------------------------


@receiver(pre_save, sender=User, dispatch_uid="qqbot_user_pre_save")
@_never_raise
def on_user_pre_save(sender, instance, update_fields=None, raw=False, **kwargs):
    if raw or instance.pk is None:
        return
    if update_fields is not None and "is_active" not in update_fields:
        return
    old = User.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()
    setattr(instance, _OLD_ACTIVE, old)


@receiver(post_save, sender=User, dispatch_uid="qqbot_user_post_save")
@_never_raise
def on_user_post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or created:
        return
    old = instance.__dict__.pop(_OLD_ACTIVE, None)
    if old is not None and old != instance.is_active:
        schedule_refresh_user(instance.pk)


@receiver(pre_delete, sender=User, dispatch_uid="qqbot_user_pre_delete")
@_never_raise
def on_user_pre_delete(sender, instance, **kwargs):
    # Runs while the binding still exists; own savepoint so a failure here
    # cannot poison the deleting transaction.
    # 运行时绑定还在；使用独立的保存点，这样这里出错也不会连累正在删除
    # 用户的那个事务。
    try:
        with transaction.atomic():
            bindings.on_user_deleted(instance)
    except Exception:
        logger.exception("qqbot: on_user_deleted failed for user %s", instance.pk)


# --------------------------------------------------------------------------
# User.groups / User.user_permissions
# 用户所在的组 / 用户直接拥有的权限
# --------------------------------------------------------------------------


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="qqbot_user_groups")
@_never_raise
def on_user_groups_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    if not reverse:
        # user.groups.add(...) etc.: ``instance`` is the user.
        # user.groups.add(...) 等：``instance`` 是用户。
        if action.startswith("post_"):
            schedule_refresh_user(instance.pk)
        return
    # group.user_set.add(...) etc.: ``instance`` is the group, pk_set users.
    # group.user_set.add(...) 等：``instance`` 是组，pk_set 是用户 id。
    if action == "pre_clear":
        if _group_matters(instance):
            setattr(instance, _CLEARED_IDS, set(instance.user_set.values_list("pk", flat=True)))
        return
    if not action.startswith("post_"):
        return
    if action != "post_clear" and not _group_matters(instance):
        return
    for user_id in _changed_ids(instance, action, pk_set):
        schedule_refresh_user(user_id)
    instance.__dict__.pop(_CLEARED_IDS, None)


@receiver(m2m_changed, sender=User.user_permissions.through, dispatch_uid="qqbot_user_perms")
@_never_raise
def on_user_permissions_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    if not reverse:
        if action.startswith("post_"):
            schedule_refresh_user(instance.pk)
        return
    # permission.user_set.add(...): ``instance`` is the permission.
    # permission.user_set.add(...)：``instance`` 是权限。
    if not _is_basic_access(instance):
        return
    if action == "pre_clear":
        setattr(instance, _CLEARED_IDS, set(instance.user_set.values_list("pk", flat=True)))
        return
    if not action.startswith("post_"):
        return
    for user_id in _changed_ids(instance, action, pk_set):
        schedule_refresh_user(user_id)
    instance.__dict__.pop(_CLEARED_IDS, None)


# --------------------------------------------------------------------------
# UserProfile (main character) and EveCharacter (name / corp / alliance)
# UserProfile（主角色）和 EveCharacter（名字 / 军团 / 联盟）
# --------------------------------------------------------------------------


@receiver(pre_save, sender=UserProfile, dispatch_uid="qqbot_profile_pre_save")
@_never_raise
def on_profile_pre_save(sender, instance, update_fields=None, raw=False, **kwargs):
    if raw or instance.pk is None:
        return
    if update_fields is not None and not {"main_character", "state"} & set(update_fields):
        return
    old = (
        UserProfile.objects.filter(pk=instance.pk)
        .values_list("main_character_id", "state_id")
        .first()
    )
    setattr(instance, _OLD_PROFILE, old)


@receiver(post_save, sender=UserProfile, dispatch_uid="qqbot_profile_post_save")
@_never_raise
def on_profile_post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    old = instance.__dict__.pop(_OLD_PROFILE, None)
    if created or old is None:
        return  # a new profile has no binding yet / 新建的 profile 还没有绑定
    if old != (instance.main_character_id, instance.state_id):
        schedule_refresh_user(instance.user_id)


@receiver(post_save, sender=EveCharacter, dispatch_uid="qqbot_character_post_save")
@_never_raise
def on_character_post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or created:
        return  # a brand-new character is nobody's main yet / 全新的角色还不是任何人的主角色
    user_ids = UserProfile.objects.filter(main_character_id=instance.pk).values_list(
        "user_id", flat=True
    )
    for user_id in user_ids:
        schedule_refresh_user(user_id)


# --------------------------------------------------------------------------
# Permission / membership changes that can affect many users at once
# 可能一次影响很多用户的权限 / 成员变化
# --------------------------------------------------------------------------


def _perm_m2m_touches_basic_access(instance, action, reverse, pk_set, holder_has_it):
    """Shared logic for ``Group.permissions`` and ``State.permissions``.

    Forward (``instance`` holds permissions): relevant when basic_access is in
    ``pk_set``, or on clear when the holder had it. Reverse (``instance`` is
    the permission): relevant when it is basic_access.

    ``Group.permissions`` 和 ``State.permissions`` 共用的判断逻辑。

    正向（``instance`` 是拥有权限的一方）：``pk_set`` 里有 basic_access 时相关；
    清空（clear）时，如果原来拥有 basic_access 也相关。反向（``instance`` 是
    权限本身）：它就是 basic_access 时相关。
    """
    if reverse:
        return _is_basic_access(instance)
    if action == "pre_clear":
        setattr(instance, _CLEARED_IDS, holder_has_it(instance))
        return False
    if action == "post_clear":
        return bool(instance.__dict__.pop(_CLEARED_IDS, False))
    perm_id = _basic_access_perm_id()
    return perm_id is not None and perm_id in (pk_set or ())


@receiver(m2m_changed, sender=Group.permissions.through, dispatch_uid="qqbot_group_perms")
@_never_raise
def on_group_permissions_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    if not (action.startswith("post_") or action == "pre_clear"):
        return
    if _perm_m2m_touches_basic_access(instance, action, reverse, pk_set, _group_has_basic_access):
        schedule_recheck_all()


@receiver(m2m_changed, sender=State.permissions.through, dispatch_uid="qqbot_state_perms")
@_never_raise
def on_state_permissions_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    if not (action.startswith("post_") or action == "pre_clear"):
        return
    if _perm_m2m_touches_basic_access(instance, action, reverse, pk_set, _state_has_basic_access):
        schedule_recheck_all()


def _on_state_members_changed(sender, instance, action, reverse, pk_set=None, **kwargs):
    # AA re-assigns states itself (state_changed covers each user); the bot
    # additionally gets one recheck_all when a state carrying basic_access
    # changed its membership rules.
    # AA 会自己重新分配 State（每个用户都会收到 state_changed）；当带有
    # basic_access 的 State 改了成员规则时，再额外给机器人发一次 recheck_all。
    if not action.startswith("post_"):
        return
    if reverse:
        states = State.objects.filter(pk__in=pk_set or ())
        if any(_state_has_basic_access(s) for s in states):
            schedule_recheck_all()
        return
    if _state_has_basic_access(instance):
        schedule_recheck_all()


for _field in ("member_characters", "member_corporations", "member_alliances", "member_factions"):
    m2m_changed.connect(
        _never_raise(_on_state_members_changed),
        sender=getattr(State, _field).through,
        dispatch_uid=f"qqbot_state_{_field}",
        weak=False,
    )


@receiver(pre_delete, sender=Group, dispatch_uid="qqbot_group_pre_delete")
@_never_raise
def on_group_pre_delete(sender, instance, **kwargs):
    # Deleting a group removes its members without m2m_changed signals.
    # 删除组时会直接移除组成员，不会发出 m2m_changed 信号。
    if _group_matters(instance):
        schedule_recheck_all()


@receiver(pre_delete, sender=State, dispatch_uid="qqbot_state_pre_delete")
@_never_raise
def on_state_pre_delete(sender, instance, **kwargs):
    if _state_has_basic_access(instance):
        schedule_recheck_all()
