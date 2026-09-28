"""Django admin: pages load, bindings / audit log are read-only, and group /
settings saves go through the same events and audit as the manage pages."""

from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.admin.utils import get_deleted_objects
from django.contrib.auth.models import Group, Permission, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allianceauth.authentication.models import User as AAUser

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
            "trusted_window_days": self.config.trusted_window_days,
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

    def test_config_card_format_change_survives_reconcile_failure(self):
        # The settings are saved before the callback runs: a failure there
        # is logged, and the admin still gets the normal redirect.
        def broken():
            raise RuntimeError("boom")

        with mock.patch("qqbot.tasks.queue_reconcile", new=broken), \
                self.assertLogs("django", "ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.config_post(card_format="{character_name} {nickname}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Config.get_solo().card_format, "{character_name} {nickname}")

    def test_binding_page_has_no_delete_link(self):
        r = self.get(url(Binding, "change", self.binding.pk))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, url(Binding, "delete", self.binding.pk))
        r = self.get(url(Binding, "changelist"))
        self.assertNotContains(r, "delete_selected")

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


def user_url(view, *args):
    # AA registers its user admin on a proxy model (authentication.User),
    # not on auth.User.
    # AA 的用户后台注册在代理模型（authentication.User）上，不是 auth.User。
    return reverse(f"admin:{AAUser._meta.app_label}_{AAUser._meta.model_name}_{view}", args=args)


class UserDeletionTests(TestCase):
    """Deleting a bound AA user in the admin (the binding cascades).

    The admin deletes the proxy model, so pre_delete is sent with the proxy
    class; AA re-sends it with the base User (authentication/admin.py,
    redirect_pre_delete), which is what qqbot listens to. "Exactly one"
    USER_DELETED / RECHECK below checks that qqbot handles it once.

    在后台删除已绑定 QQ 的 AA 用户（绑定级联删除）。后台删的是代理模型，
    pre_delete 的发送方是代理类；AA 会以基础 User 为发送方再发一次，qqbot
    监听的正是这一次。下面的「恰好 1 条」就是在检查 qqbot 只处理了一次。
    """

    def setUp(self):
        self.root = create_user("root", superuser=True)
        self.alice = create_member("alice")
        self.bob = create_member("bob")
        bind(self.alice, "12345678")
        bind(self.bob, "23456789", status="trusted")

    def assert_deleted_once(self, user, qq):
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        self.assertFalse(Binding.objects.filter(qq=qq).exists())
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.USER_DELETED, qq=qq).count(), 1
        )
        self.assertEqual(Event.objects.filter(kind=Event.Kind.RECHECK, qq=qq).count(), 1)

    def test_nothing_blocks_the_deletion(self):
        request = RequestFactory().get("/")
        request.user = self.root
        _objs, _counts, perms_needed, protected = get_deleted_objects(
            [AAUser.objects.get(pk=self.alice.pk)], request, django_admin.site
        )
        self.assertEqual(perms_needed, set())
        self.assertEqual(protected, [])

    def test_superuser_deletes_one(self):
        self.client.force_login(self.root)
        r = self.client.get(user_url("delete", self.alice.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["perms_lacking"])
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(user_url("delete", self.alice.pk), {"post": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assert_deleted_once(self.alice, "12345678")
        self.assertTrue(Binding.objects.filter(qq="23456789").exists())

    def test_superuser_deletes_selected(self):
        self.client.force_login(self.root)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(
                user_url("changelist"),
                {
                    "action": "delete_selected",
                    "_selected_action": [self.alice.pk, self.bob.pk],
                    "post": "yes",
                },
            )
        self.assertEqual(r.status_code, 302)
        self.assert_deleted_once(self.alice, "12345678")
        self.assert_deleted_once(self.bob, "23456789")

    def staff(self, *perms):
        user = create_user("it", superuser=False)
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.user_permissions.add(*perms)
        return user

    def test_staff_with_auth_delete_user(self):
        # Limitation: create_member() users have no notifications, character
        # ownerships or ESI tokens, so auth.delete_user alone is enough here.
        # On a real site Django also checks the delete permission of every
        # other cascaded, admin-registered model (see docs/GUIDE.md).
        # 局限：create_member() 建的用户没有通知、角色归属和 ESI token，所以这里
        # 只要 auth.delete_user 就够。真实站点上 Django 还会检查其他跟着被删、
        # 在后台注册过的类型的删除权限（见 docs/GUIDE.md）。
        perm = Permission.objects.get(
            codename="delete_user", content_type__app_label="auth", content_type__model="user"
        )
        self.client.force_login(self.staff(perm))
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(user_url("delete", self.alice.pk), {"post": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assert_deleted_once(self.alice, "12345678")

    def test_staff_with_only_the_proxy_permission_is_refused(self):
        # AA checks auth | user | Can delete user; the permission with the
        # same name on AA's proxy model is not enough (a trap for IT).
        # AA 检查的是 auth | user | Can delete user；AA 代理模型上同名的权限不够（IT 的坑）。
        perm = Permission.objects.get(
            codename="delete_user",
            content_type__app_label=AAUser._meta.app_label,
            content_type__model=AAUser._meta.model_name,
        )
        self.client.force_login(self.staff(perm))
        r = self.client.post(user_url("delete", self.alice.pk), {"post": "yes"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())
