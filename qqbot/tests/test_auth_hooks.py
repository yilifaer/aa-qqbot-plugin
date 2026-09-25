"""The sidebar menu entry and the qqbot tab bar (DESIGN.md 4.1)."""

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


class MenuItemTests(TestCase):
    def setUp(self):
        cache.clear()
        self.my_qq = reverse("qqbot:my_qq")
        self.manage = reverse("qqbot:manage_index")

    def test_member_links_to_own_page(self):
        html = render_menu(create_member("m"))
        self.assertIn(f'href="{self.my_qq}"', html)
        self.assertIn("QQ 绑定", html)

    def test_member_and_manager_links_to_own_page(self):
        user = create_member("both")
        AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
        html = render_menu(fresh(user))
        self.assertIn(f'href="{self.my_qq}"', html)

    def test_manager_without_basic_access_links_to_manage_pages(self):
        user = create_member("mgr", state_perm=False)
        AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
        user = fresh(user)
        html = render_menu(user)
        self.assertIn(f'href="{self.manage}"', html)
        self.assertNotIn(f'href="{self.my_qq}"', html)
        # ... and the link works for them.
        self.client.force_login(user)
        r = self.client.get(self.manage, follow=True)
        self.assertEqual(r.status_code, 200)
        # The shared hook object is not changed for the next user.
        self.assertIn(f'href="{self.my_qq}"', render_menu(create_member("next")))

    def test_hidden_without_permissions(self):
        self.assertEqual(render_menu(create_member("nobody", state_perm=False)), "")
        self.assertEqual(render_menu(create_user("nomain", main=False)), "")


class TabBarTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_manager_without_basic_access_sees_no_member_tab(self):
        user = create_member("mgr", state_perm=False)
        AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
        self.client.force_login(user)
        r = self.client.get(reverse("qqbot:manage_bindings"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, f'href="{reverse("qqbot:my_qq")}"')
        self.assertContains(r, "QQ 管理")

    def test_member_sees_member_tab_only(self):
        self.client.force_login(create_member("m"))
        r = self.client.get(reverse("qqbot:my_qq"))
        self.assertContains(r, "我的 QQ")
        self.assertNotContains(r, f'href="{reverse("qqbot:manage_index")}"')
