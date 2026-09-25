"""Audit log helper.

审计日志辅助函数。
"""

import json

from django.core.serializers.json import DjangoJSONEncoder

from ..models import AuditLog


class _Encoder(DjangoJSONEncoder):
    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            return str(o)


def _username(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    return user.get_username()


def log(action, *, actor=None, qq="", target_user=None, **detail) -> AuditLog:
    """Write an audit record. ``actor=None`` means the bot or the system.

    Usernames are snapshotted so the record survives user deletion.

    写入一条审计记录。``actor=None`` 表示操作者是机器人或系统。

    用户名会另存一份快照，这样即使用户被删除，记录依然完整。
    """
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None  # AnonymousUser / 匿名用户
    # Make the detail JSON-safe (datetimes etc. become strings).
    # 把 detail 转成能安全存成 JSON 的数据（datetime 等会变成字符串）。
    detail = json.loads(json.dumps(detail, cls=_Encoder))
    return AuditLog.objects.create(
        action=action,
        actor=actor,
        actor_name=_username(actor),
        qq=qq or "",
        target_user=target_user,
        target_name=_username(target_user),
        detail=detail,
    )
