"""The sidebar menu entry and the manage layout (DECISIONS.md #18, DESIGN.md 4.1).

Members have no qqbot menu entry (they use AA's "服务" page); QQ managers
get "QQ 管理".
"""

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allianceauth.tests.auth_utils import AuthUtils

from ..auth_hooks import QQBotMenuItem
from .utils import MANAGE, create_member, create_user


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
