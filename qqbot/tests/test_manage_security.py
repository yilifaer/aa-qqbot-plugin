"""Manager pages: permissions for every URL, POST-only, CSRF and escaping."""

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import URLPattern, reverse

from allianceauth.tests.auth_utils import AuthUtils

from ..models import AuditLog, Binding, Config, QQGroup
from ..views import manage_urls
from .utils import MANAGE, bind, create_group, create_member, create_user, put_in_roster

XSS = "<script>alert(1)</script>"
XSS_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"

# Routes whose GET is refused (POST only).
POST_ONLY = {"manage_binding_card", "manage_binding_confirm"}
# Routes whose GET shows a page that then POSTs to the same URL.
GET_AND_POST = {
    "manage_group_create",
    "manage_group_edit",
    "manage_group_delete",
    "manage_binding_unbind",
    "manage_settings",
}


def create_manager(username="boss"):
    user = create_member(username)
    AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
    return user


def manage_urls_with_args(group_pk, binding_pk):
    """Every route of manage_urls, reversed with suitable pks."""
    result = {}
    for pattern in manage_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        name = pattern.name
        kwargs = {}
        if "<int:pk>" in str(pattern.pattern):
            kwargs["pk"] = group_pk if name.startswith("manage_group") else binding_pk
        result[name] = reverse(f"qqbot:{name}", kwargs=kwargs)
    return result


class Fixture:
    def make_objects(self):
        cache.clear()
        self.group = create_group("123456", name="聊天群")
        self.member = create_member("alice")
        self.binding = bind(self.member, "11111111", status="trusted")
        self.urls = manage_urls_with_args(self.group.pk, self.binding.pk)

    def assert_unchanged(self):
        self.assertTrue(QQGroup.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(Binding.objects.filter(pk=self.binding.pk, status="trusted").exists())
        self.assertFalse(AuditLog.objects.exists())


class PermissionTests(Fixture, TestCase):
    def setUp(self):
        self.make_objects()

    def test_every_route_is_covered(self):
        self.assertEqual(
            set(self.urls),
            {
                "manage_index", "manage_groups", "manage_group_create", "manage_group_edit",
                "manage_group_delete", "manage_bindings", "manage_binding",
                "manage_binding_card", "manage_binding_confirm", "manage_binding_unbind",
                "manage_pending", "manage_settings", "manage_audit",
            },
        )

    def test_anonymous_redirected_to_login(self):
        login_url = reverse(settings.LOGIN_URL)
        for name, url in self.urls.items():
            for method in ("get", "post"):
                with self.subTest(name=name, method=method):
                    r = getattr(self.client, method)(url)
                    self.assertEqual(r.status_code, 302)
                    self.assertTrue(r.url.startswith(login_url), r.url)
        self.assert_unchanged()

    def test_member_without_manage_forbidden(self):
        self.client.force_login(create_member("bob"))
        for name, url in self.urls.items():
            for method in ("get", "post"):
                with self.subTest(name=name, method=method):
                    data = {"name": "x", "group_id": "654321", "kind": "fixed", "card": "x"}
                    r = getattr(self.client, method)(url, data if method == "post" else None)
                    self.assertEqual(r.status_code, 403)
        self.assert_unchanged()

    def test_superuser_may_manage(self):
        # permission_required uses has_perm, which a superuser always passes.
        self.client.force_login(create_user("root", superuser=True))
        self.assertEqual(self.client.get(self.urls["manage_bindings"]).status_code, 200)

    def test_manager_allowed(self):
        self.client.force_login(create_manager())
        for name, url in self.urls.items():
            with self.subTest(name=name):
                r = self.client.get(url)
                if name == "manage_index":
                    self.assertRedirects(r, self.urls["manage_bindings"])
                elif name in POST_ONLY:
                    self.assertEqual(r.status_code, 405)
                else:
                    self.assertEqual(r.status_code, 200)
        self.assert_unchanged()

    def test_manager_without_basic_access_may_manage(self):
        user = create_member("mgr", state_perm=False)
        AuthUtils.add_permission_to_user_by_name(MANAGE, user, disconnect_signals=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.urls["manage_pending"]).status_code, 200)

    def test_get_never_changes_anything(self):
        self.client.force_login(create_manager())
        for name in GET_AND_POST:
            self.client.get(self.urls[name])
        self.assert_unchanged()

    def test_other_methods_refused(self):
        self.client.force_login(create_manager())
        for name in POST_ONLY | GET_AND_POST:
            with self.subTest(name=name):
                self.assertEqual(self.client.put(self.urls[name]).status_code, 405)
                self.assertEqual(self.client.delete(self.urls[name]).status_code, 405)
        self.assert_unchanged()


class CsrfTests(Fixture, TestCase):
    def setUp(self):
        self.make_objects()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(create_manager())

    def test_posts_without_token_rejected(self):
        posts = {
            "manage_group_create": {"name": "n", "group_id": "654321", "kind": "fixed",
                                    "sort_order": 1, "is_active": "on"},
            "manage_group_edit": {"name": "n", "group_id": "123456", "kind": "fixed",
                                  "sort_order": 1},
            "manage_group_delete": {},
            "manage_binding_card": {"card": "abc"},
            "manage_binding_confirm": {},
            "manage_binding_unbind": {},
            "manage_settings": {"card_format": "{nickname}", "code_ttl_minutes": 10,
                                "roster_max_age_days": 7, "rebind_cooldown_hours": 24},
        }
        for name, data in posts.items():
            with self.subTest(name=name):
                self.assertEqual(self.client.post(self.urls[name], data).status_code, 403)
        self.assert_unchanged()
        self.assertEqual(Config.get_solo().card_format, Config.DEFAULT_CARD_FORMAT)

    def test_forms_carry_token(self):
        for name in ("manage_group_create", "manage_binding", "manage_binding_unbind",
                     "manage_settings", "manage_group_delete", "manage_pending"):
            with self.subTest(name=name):
                r = self.client.get(self.urls[name])
                self.assertContains(r, "csrfmiddlewaretoken")


class EscapingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(create_manager())

    def test_names_cards_and_descriptions_escaped(self):
        group = create_group("123456", name=XSS, description=XSS)
        role = create_group("223456", kind="role", required=[XSS], name="小群")
        member = create_member("x" + "1", character_name=XSS, corp_ticker="<b>")
        other = create_member("x2", character_name=XSS)
        b = bind(member, "11111111", status="trusted", nickname=XSS, card_override=XSS)
        bind(other, "11111111", status="trusted", nickname=XSS)
        put_in_roster(group, ["22222222"])
        AuditLog.objects.create(action="group", actor_name=XSS, target_name=XSS,
                                detail={XSS: XSS, "list": [XSS]})

        pages = [
            reverse("qqbot:manage_groups"),
            reverse("qqbot:manage_group_edit", args=[group.pk]),
            reverse("qqbot:manage_group_edit", args=[role.pk]),
            reverse("qqbot:manage_group_delete", args=[group.pk]),
            reverse("qqbot:manage_bindings"),
            reverse("qqbot:manage_binding", args=[b.pk]),
            reverse("qqbot:manage_binding_unbind", args=[b.pk]),
            reverse("qqbot:manage_pending"),
            reverse("qqbot:manage_audit"),
        ]
        for url in pages:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                content = r.content.decode()
                self.assertNotIn(XSS, content)
                self.assertNotIn("<b>", content)
                self.assertIn(XSS_ESCAPED, content)

    def test_settings_rules_text_escaped(self):
        config = Config.get_solo()
        config.rules_text = XSS
        config.save()
        r = self.client.get(reverse("qqbot:manage_settings"))
        self.assertNotIn(XSS, r.content.decode())
        self.assertContains(r, XSS_ESCAPED)

    def test_search_term_escaped(self):
        r = self.client.get(reverse("qqbot:manage_bindings"), {"q": XSS})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(XSS, r.content.decode())
