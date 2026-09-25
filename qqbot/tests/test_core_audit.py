from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from ..core import audit
from ..models import AuditLog
from .utils import create_member


class AuditLogTests(TestCase):
    def test_snapshots_names(self):
        actor = create_member("boss")
        target = create_member("alice")
        entry = audit.log(AuditLog.Action.BIND, actor=actor, qq="12345678", target_user=target, a=1)
        entry.refresh_from_db()
        self.assertEqual(entry.action, "bind")
        self.assertEqual(entry.actor, actor)
        self.assertEqual(entry.actor_name, "boss")
        self.assertEqual(entry.target_user, target)
        self.assertEqual(entry.target_name, "alice")
        self.assertEqual(entry.qq, "12345678")
        self.assertEqual(entry.detail, {"a": 1})

    def test_system_actor(self):
        entry = audit.log(AuditLog.Action.VERIFY)
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_name, "")
        self.assertEqual(entry.target_name, "")
        self.assertEqual(entry.qq, "")
        self.assertEqual(entry.detail, {})

    def test_anonymous_actor_is_system(self):
        entry = audit.log(AuditLog.Action.VERIFY, actor=AnonymousUser())
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_name, "")

    def test_detail_is_json_safe(self):
        when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt_timezone.utc)
        entry = audit.log(AuditLog.Action.CODE, expires_at=when, items=("a", "b"))
        entry.refresh_from_db()
        self.assertTrue(entry.detail["expires_at"].startswith("2026-01-02T03:04:05"))
        self.assertEqual(entry.detail["items"], ["a", "b"])

    def test_names_survive_user_deletion(self):
        target = create_member("alice")
        entry = audit.log(AuditLog.Action.UNBIND, actor=target, target_user=target)
        target.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.target_user)
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.target_name, "alice")
        self.assertEqual(entry.actor_name, "alice")
