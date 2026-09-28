"""The sidebar menu entry and the manage layout (DECISIONS.md #18, DESIGN.md 4.1).

Members have no qqbot menu entry (they use AA's "服务" page); QQ managers
get "QQ 管理".
"""

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from allianceauth.tests.auth_utils import AuthUtils

from ..auth_hooks import QQBotMenuItem
from ..core import attention, bindings
from ..models import QQGroup
from .utils import MANAGE, bind, create_group, create_member, create_user, put_in_roster


def render_menu(user) -> str:
    request = RequestFactory().get("/dashboard/")
    request.user = user
    return QQBotMenuItem().render(request)


def fresh(user):
    # Permission caches live on the instance.
    return type(user).objects.get(pk=user.pk)


def make_manager(user):
    AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
    return fresh(user)


class MenuItemTests(TestCase):
    def setUp(self):
        cache.clear()
        self.my_qq = reverse("qqbot:my_qq")
        self.manage = reverse("qqbot:manage_index")

    def test_member_has_no_entry(self):
        self.assertEqual(render_menu(create_member("m")), "")

    def test_member_and_manager_links_to_manage_pages(self):
        html = render_menu(make_manager(create_member("both")))
        self.assertIn(f'href="{self.manage}"', html)
        self.assertIn("QQ 管理", html)
        self.assertNotIn(f'href="{self.my_qq}"', html)

    def test_manager_without_basic_access_links_to_manage_pages(self):
        user = make_manager(create_member("mgr", state_perm=False))
        html = render_menu(user)
        self.assertIn(f'href="{self.manage}"', html)
        # ... and the link works for them.
        self.client.force_login(user)
        r = self.client.get(self.manage, follow=True)
        self.assertEqual(r.status_code, 200)

    def test_hidden_without_permissions(self):
        self.assertEqual(render_menu(create_member("nobody", state_perm=False)), "")
        self.assertEqual(render_menu(create_user("nomain", main=False)), "")

    def test_rendered_sidebar(self):
        """On a real page: a member sees no qqbot entry, a manager sees one."""
        member = create_member("m")
        self.client.force_login(member)
        r = self.client.get(reverse("services:services"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, f'href="{self.manage}"')
        self.assertNotContains(r, f'href="{self.my_qq}"')

        manager = make_manager(create_member("mgr"))
        self.client.force_login(manager)
        r = self.client.get(reverse("services:services"))
        self.assertContains(r, f'href="{self.manage}"')
        self.assertNotContains(r, f'href="{self.my_qq}"')


class MenuBadgeTests(TestCase):
    """The number badge: conflicts + misconfigured role groups (DECISIONS #24).

    数字角标：冲突数 + 配置错误的身份组小群数（决定 #24）。
    """

    def setUp(self):
        cache.clear()
        self.hook = QQBotMenuItem()
        self.manager = make_manager(create_member("mgr"))

    def render(self, user):
        request = RequestFactory().get("/dashboard/")
        request.user = user
        html = self.hook.render(request)
        return html, self.hook.count

    def conflict(self, qq="33333333"):
        bind(create_member(f"a{qq}"), qq, status="trusted")
        bind(create_member(f"b{qq}"), qq, status="trusted")

    def test_manager_without_main_has_no_entry(self):
        # AA sends them back to the dashboard from every plugin page.
        user = make_manager(create_user("nomainmgr", main=False))
        self.assertEqual(self.render(user), ("", None))

    def test_no_badge_when_nothing_to_do(self):
        html, count = self.render(self.manager)
        self.assertIn("QQ 管理", html)
        self.assertIsNone(count)

    def test_conflict_counts(self):
        self.conflict()
        self.assertEqual(self.render(self.manager)[1], 1)

    def test_misconfigured_role_group_counts(self):
        group = create_group("223456", kind="role", required=["Cap"])
        group.required_groups.clear()
        self.assertEqual(self.render(self.manager)[1], 1)
        # A disabled one does not.
        QQGroup.objects.filter(pk=group.pk).update(is_active=False)
        self.assertIsNone(self.render(self.manager)[1])

    def test_both_add_up(self):
        self.conflict()
        create_group("223456", kind="role")
        self.assertEqual(self.render(self.manager)[1], 2)

    def test_not_counted(self):
        g = create_group("123456")
        put_in_roster(g, [str(40_000_000 + i) for i in range(5)])  # unbound
        for i in range(3):  # the trusted review list
            bind(create_member(f"t{i}"), str(50_000_000 + i), status="trusted",
                 qq_changed_at=timezone.now())
        bindings.submit(create_member("p"), "60000000", "p")  # a pending code
        self.assertIsNone(self.render(self.manager)[1])

    def test_count_does_not_leak_between_requests(self):
        self.conflict()
        self.assertEqual(self.render(self.manager)[1], 1)
        # The same (process-wide) hook object, a member next: no entry, no count.
        self.assertEqual(self.render(create_member("m")), ("", None))

    def test_badge_on_a_real_page(self):
        self.conflict()
        self.client.force_login(self.manager)
        r = self.client.get(reverse("services:services"))
        html = r.content.decode()
        start = html.index(f'href="{reverse("qqbot:manage_index")}"')
        self.assertRegex(html[start:start + 600], r'class="badge[^"]*">\s*1\s*<')

    def test_counts_are_two_queries(self):
        for i in range(5):
            self.conflict(str(70_000_000 + i))
        create_group("223456", kind="role")
        with self.assertNumQueries(2):
            counts = attention.attention_counts()
        self.assertEqual(counts, {"conflicts": 5, "misconfigured": 1, "total": 6})


class ManageLayoutTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_no_member_tab(self):
        user = make_manager(create_member("mgr"))
        self.client.force_login(user)
        r = self.client.get(reverse("qqbot:manage_bindings"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "我的 QQ")
        self.assertNotContains(r, f'href="{reverse("qqbot:my_qq")}"')
        # A small link back to their own card on the services page.
        self.assertContains(r, f'href="{reverse("services:services")}#qqbot"')

    def test_manager_without_basic_access_gets_no_services_link(self):
        user = make_manager(create_member("mgr", state_perm=False))
        self.client.force_login(user)
        r = self.client.get(reverse("qqbot:manage_bindings"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="qqbot-services-link"')
        self.assertContains(r, "QQ 管理")
