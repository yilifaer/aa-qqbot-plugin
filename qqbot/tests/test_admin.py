"""Django admin: pages load, bindings / audit log are read-only, and group /
settings saves go through the same events and audit as the manage pages."""

from unittest import mock

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ..core import audit
from ..models import AuditLog, Binding, Config, Event, QQGroup
from .utils import bind, create_group, create_member, create_user


def url(model, view, *args):
    return reverse(f"admin:qqbot_{model._meta.model_name}_{view}", args=args)


class AdminTests(TestCase):
    def setUp(self):
        self.admin = create_user("root", superuser=True)
        self.client.force_login(self.admin)
        self.member = create_member("alice")
        self.binding = bind(self.member, "12345678")
        self.group = create_group(100001)
        self.log = audit.log(AuditLog.Action.BIND, qq="12345678", target_user=self.member)
        self.config = Config.get_solo()

    def get(self, u):
        return self.client.get(u)

    def test_pages_load(self):
        pages = [
            reverse("admin:index"),
            reverse("admin:app_list", args=["qqbot"]),
            url(QQGroup, "changelist"),
            url(QQGroup, "add"),
            url(QQGroup, "change", self.group.pk),
            url(QQGroup, "delete", self.group.pk),
            url(Binding, "changelist"),
            url(Binding, "change", self.binding.pk),
            url(AuditLog, "changelist"),
            url(AuditLog, "change", self.log.pk),
            url(Config, "changelist"),
            url(Config, "change", self.config.pk),
        ]
        for page in pages:
            with self.subTest(page=page):
                self.assertEqual(self.get(page).status_code, 200)

    def test_search_and_filters(self):
        for model, q in ((Binding, "alice"), (AuditLog, "1234"), (QQGroup, "100001")):
            with self.subTest(model=model):
                r = self.get(url(model, "changelist") + f"?q={q}")
                self.assertEqual(r.status_code, 200)
        r = self.get(url(Binding, "changelist") + "?status=verified")
        self.assertContains(r, "12345678")

    def test_binding_read_only(self):
        self.assertEqual(self.get(url(Binding, "add")).status_code, 403)
        self.assertEqual(self.get(url(Binding, "delete", self.binding.pk)).status_code, 403)
        r = self.get(url(Binding, "change", self.binding.pk))
        self.assertNotContains(r, 'name="_save"')
        r = self.client.post(
            url(Binding, "change", self.binding.pk), {"qq": "87654321", "nickname": "x"}
        )
        self.assertEqual(r.status_code, 403)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.qq, "12345678")
        r = self.client.post(
            url(Binding, "changelist"),
            {"action": "delete_selected", "_selected_action": [self.binding.pk], "post": "yes"},
        )
        self.assertTrue(Binding.objects.filter(pk=self.binding.pk).exists())

    def test_audit_log_read_only(self):
        self.assertEqual(self.get(url(AuditLog, "add")).status_code, 403)
        self.assertEqual(self.get(url(AuditLog, "delete", self.log.pk)).status_code, 403)
        r = self.client.post(url(AuditLog, "change", self.log.pk), {"qq": "99999"})
        self.assertEqual(r.status_code, 403)
        self.log.refresh_from_db()
        self.assertEqual(self.log.qq, "12345678")

    def test_config_cannot_be_added_twice_or_deleted(self):
        self.assertEqual(self.get(url(Config, "add")).status_code, 403)
        self.assertEqual(self.get(url(Config, "delete", self.config.pk)).status_code, 403)

    def test_group_save_emits_events_and_audit(self):
        cap = Group.objects.create(name="Cap")
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(
                url(QQGroup, "add"),
                {
                    "name": "旗舰群",
                    "group_id": "２００００１",
                    "kind": "role",
                    "required_groups": [cap.pk],
                    "description": "",
                    "sort_order": 100,
                    "is_active": "on",
                },
            )
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context.get("errors"))
        group = QQGroup.objects.get(group_id="200001")
        self.assertEqual(list(group.required_groups.all()), [cap])
        self.assertEqual(
            list(Event.objects.values_list("kind", flat=True)),
            [Event.Kind.GROUPS, Event.Kind.RECHECK_ALL],
        )
        log = AuditLog.objects.filter(action=AuditLog.Action.GROUP).get()
        self.assertEqual(log.actor, self.admin)
        self.assertEqual(log.detail["op"], "create")
        self.assertEqual(log.detail["required_groups"], ["Cap"])

    def test_group_form_rules_apply(self):
        r = self.client.post(
            url(QQGroup, "add"),
            {"name": "x", "group_id": "200001", "kind": "role", "sort_order": 1},
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(QQGroup.objects.filter(group_id="200001").exists())
        self.assertFalse(Event.objects.exists())

    def test_group_delete_emits_events_and_audit(self):
        r = self.client.post(url(QQGroup, "delete", self.group.pk), {"post": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(QQGroup.objects.exists())
        self.assertEqual(Event.objects.filter(kind=Event.Kind.GROUPS).count(), 1)
        log = AuditLog.objects.filter(action=AuditLog.Action.GROUP).get()
        self.assertEqual((log.detail["op"], log.detail["group_id"]), ("delete", "100001"))

    def test_group_bulk_delete_emits_events_and_audit(self):
        other = create_group(100002)
        r = self.client.post(
            url(QQGroup, "changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [self.group.pk, other.pk],
                "post": "yes",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(QQGroup.objects.exists())
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.GROUP).count(), 2)
        self.assertTrue(Event.objects.filter(kind=Event.Kind.RECHECK_ALL).exists())

    def config_post(self, **changes):
        data = {
            "rules_text": self.config.rules_text,
            "card_format": self.config.card_format,
            "code_ttl_minutes": self.config.code_ttl_minutes,
            "roster_max_age_days": self.config.roster_max_age_days,
            "rebind_cooldown_hours": self.config.rebind_cooldown_hours,
        }
        data.update(changes)
        return self.client.post(url(Config, "change", self.config.pk), data)

    def test_config_card_format_change_queues_reconcile(self):
        with mock.patch("qqbot.tasks.queue_reconcile") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.config_post(card_format="{character_name} {nickname}")
        self.assertEqual(r.status_code, 302)
        queue.assert_called_once_with()
        log = AuditLog.objects.filter(action=AuditLog.Action.CONFIG).get()
        self.assertEqual(log.detail["changed"], ["card_format"])

    def test_config_other_change_no_reconcile(self):
        with mock.patch("qqbot.tasks.queue_reconcile") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.config_post(code_ttl_minutes=20)
        self.assertEqual(r.status_code, 302)
        queue.assert_not_called()
        self.assertEqual(Config.get_solo().code_ttl_minutes, 20)

    def test_config_bad_card_format_rejected(self):
        r = self.config_post(card_format="{unknown}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Config.get_solo().card_format, Config.DEFAULT_CARD_FORMAT)


class NonSuperuserTests(TestCase):
    def test_staff_without_permissions_cannot_see_qqbot_admin(self):
        user = create_member("staff")
        user.is_staff = True
        user.save()
        self.client.force_login(user)
        self.assertEqual(self.client.get(url(Binding, "changelist")).status_code, 403)
        self.assertEqual(self.client.get(url(AuditLog, "changelist")).status_code, 403)
