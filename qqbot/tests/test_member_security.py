"""Member side (the services card and its POST targets): permissions,
POST-only, CSRF and escaping."""

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from ..models import Binding, BindCode, Config, QQGroup
from .utils import bind, create_group, create_member

SERVICES = reverse("services:services")
BACK = SERVICES + "#qqbot"
MY_QQ = reverse("qqbot:my_qq")
SUBMIT = reverse("qqbot:member_submit")
CANCEL = reverse("qqbot:member_code_cancel")
NICKNAME = reverse("qqbot:member_nickname")
UNBIND = reverse("qqbot:member_unbind")

ALL_URLS = [MY_QQ, SUBMIT, CANCEL, NICKNAME, UNBIND]
POST_ONLY = [SUBMIT, CANCEL, NICKNAME]

QQ = "12345678"
XSS = "<script>alert(1)</script>"
XSS_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"


class PermissionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_anonymous_redirected_to_login(self):
        login_url = reverse(settings.LOGIN_URL)
        for url in ALL_URLS:
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    r = getattr(self.client, method)(url)
                    self.assertEqual(r.status_code, 302)
                    self.assertTrue(r.url.startswith(login_url), r.url)

    def test_without_permission_forbidden(self):
        user = create_member("noperm", state_perm=False)
        self.client.force_login(user)
        for url in ALL_URLS:
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    data = {"qq": QQ, "nickname": "凯拉"} if method == "post" else None
                    r = getattr(self.client, method)(url, data)
                    self.assertEqual(r.status_code, 403)
        self.assertFalse(BindCode.objects.filter(user=user).exists())

    def test_member_allowed(self):
        self.client.force_login(create_member("member"))
        r = self.client.get(MY_QQ)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)
        self.assertContains(self.client.get(SERVICES), 'id="qqbot"')

    def test_post_only(self):
        self.client.force_login(create_member("member"))
        for url in POST_ONLY:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_unbind_get_does_not_unbind(self):
        user = create_member("member")
        bind(user, QQ)
        self.client.force_login(user)
        r = self.client.get(UNBIND)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)
        self.assertTrue(Binding.objects.filter(user=user).exists())
        self.assertEqual(self.client.put(UNBIND).status_code, 405)
        self.assertTrue(Binding.objects.filter(user=user).exists())


class CsrfTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_member("member")
        bind(self.user, QQ, nickname="凯拉")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_posts_without_token_rejected(self):
        for url, data in (
            (SUBMIT, {"qq": "87654321", "nickname": "凯拉"}),
            (CANCEL, {}),
            (NICKNAME, {"nickname": "小凯"}),
            (UNBIND, {"confirm": "1"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        binding = Binding.objects.get(user=self.user)
        self.assertEqual(binding.nickname, "凯拉")
        self.assertFalse(BindCode.objects.filter(user=self.user).exists())

    def test_post_with_token_accepted(self):
        page = self.client.get(SERVICES)
        self.assertContains(page, "csrfmiddlewaretoken")
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        r = self.client.post(NICKNAME, {"nickname": "小凯", "csrfmiddlewaretoken": token})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "小凯")
        r = self.client.post(UNBIND, {"confirm": "1", "csrfmiddlewaretoken": token})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Binding.objects.filter(user=self.user).exists())

    def test_every_card_form_has_a_token(self):
        html = self.client.get(SERVICES).content.decode()
        card = html[html.index('id="qqbot"'):]
        forms = card.split("<form")[1:]
        self.assertGreaterEqual(len(forms), 3)  # nickname, rebind, unbind
        for form in forms:
            self.assertIn('method="post"', form[:80])
            self.assertIn("csrfmiddlewaretoken", form[: form.index("</form>")])


class EscapingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_external_text_escaped(self):
        user = create_member("member", character_name=f"Pilot{XSS}", corp_ticker="<b>")
        config = Config.get_solo()
        config.rules_text = f"第一行\n{XSS}\n第三行"
        config.save()
        create_group("100001", name=f"群{XSS}", description=f"说明{XSS}")
        create_group("100002", kind=QQGroup.Kind.ROLE, name=XSS)  # misconfigured: hidden
        # Nickname validation would reject this; bypass it to test escaping.
        bind(user, QQ, nickname=XSS)
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertEqual(r.status_code, 200)
        content = r.content.decode()
        self.assertNotIn(XSS, content)
        self.assertNotIn("<b>", content)
        self.assertIn(XSS_ESCAPED, content)
        self.assertIn("&lt;b&gt;", content)
        self.assertIn("第一行<br>", content)
        self.assertIn("100001", content)

    def test_unbound_card_prefix_escaped(self):
        user = create_member("member", character_name=f"P{XSS}")
        self.client.force_login(user)
        content = self.client.get(SERVICES).content.decode()
        self.assertNotIn(XSS, content)
        self.assertIn("&lt;script&gt;", content)

    def test_pending_nickname_escaped(self):
        # Codes store the nickname as submitted; make sure it is escaped too.
        user = create_member("member")
        self.client.force_login(user)
        self.client.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        BindCode.objects.filter(user=user).update(nickname=XSS)
        content = self.client.get(SERVICES).content.decode()
        self.assertIn("正在验证的 QQ", content)
        self.assertNotIn(XSS, content)
        self.assertIn(XSS_ESCAPED, content)
