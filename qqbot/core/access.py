"""Base access checks (the foundation of eligibility).

These deliberately do **not** use ``user.has_perm``: that returns ``True``
for every permission of a superuser, but being a superuser must not let
anybody into the QQ groups. The permission has to be granted explicitly to
the user, to one of their groups or to their AA state.

基础访问权限检查（资格判断的基础）。

这里故意 **不** 使用 ``user.has_perm``：对超级用户，它对所有权限都返回
``True``，但超级用户身份不能让任何人进 QQ 群。这个权限必须明确授予给用户
本人、用户所在的某个组（Group），或者用户的 AA 状态（State）。
"""

from collections.abc import Iterable, Iterator

from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef

from allianceauth.authentication.models import UserProfile

APP_LABEL = "qqbot"
BASIC_ACCESS = "basic_access"

# Large IN lists are split into chunks of this size.
# 很长的 IN 列表会按这个大小拆成多段。
CHUNK_SIZE = 500

_UserPerm = User.user_permissions.through
_UserGroup = User.groups.through


def chunked(items, size: int = CHUNK_SIZE) -> Iterator[list]:
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _perm_filter(prefix: str) -> dict:
    return {
        f"{prefix}codename": BASIC_ACCESS,
        f"{prefix}content_type__app_label": APP_LABEL,
    }


def base_access_user_ids(user_ids: Iterable[int]) -> set[int]:
    """Ids of the users that were granted ``qqbot.basic_access``.

    Only the permission condition is checked (user permission, group
    permission or state permission); ``is_active`` and the main character are
    the caller's business. One query per 500 ids.

    被授予了 ``qqbot.basic_access`` 的用户 id。

    只检查权限条件（用户权限、组权限或状态权限）；``is_active`` 和主角色
    由调用方自己检查。每 500 个 id 查询一次。
    """
    ids = {int(pk) for pk in user_ids if pk is not None}
    result: set[int] = set()
    for chunk in chunked(sorted(ids)):
        via_user = _UserPerm.objects.filter(user_id=OuterRef("pk"), **_perm_filter("permission__"))
        via_group = _UserGroup.objects.filter(
            user_id=OuterRef("pk"), **_perm_filter("group__permissions__")
        )
        via_state = UserProfile.objects.filter(
            user_id=OuterRef("pk"), **_perm_filter("state__permissions__")
        )
        qs = User.objects.filter(pk__in=chunk).filter(
            Exists(via_user) | Exists(via_group) | Exists(via_state)
        )
        result.update(qs.values_list("pk", flat=True))
    return result


def has_main_character(user) -> bool:
    try:
        return user.profile.main_character_id is not None
    except UserProfile.DoesNotExist:
        return False


def has_base_access(user) -> bool:
    """Active, has a main character and was explicitly granted basic_access.

    账号已启用、有主角色，并且被明确授予了 basic_access。
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active or not has_main_character(user):
        return False
    return user.pk in base_access_user_ids([user.pk])


def user_group_ids(user_ids: Iterable[int]) -> dict[int, set[int]]:
    """AA (Django) group ids per user; every requested id is a key.

    每个用户所在的 AA（Django）组的 id；每个传入的用户 id 都会作为键出现。
    """
    ids = {int(pk) for pk in user_ids if pk is not None}
    result: dict[int, set[int]] = {pk: set() for pk in ids}
    for chunk in chunked(sorted(ids)):
        rows = _UserGroup.objects.filter(user_id__in=chunk).values_list("user_id", "group_id")
        for user_id, group_id in rows:
            result[user_id].add(group_id)
    return result

