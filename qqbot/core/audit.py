"""Audit log helper."""

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
    """
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None  # AnonymousUser
    # Make the detail JSON-safe (datetimes etc. become strings).
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
