"""Member page states and flows ("我的 QQ")."""

import re
from datetime import timedelta

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..core import bindings, codes
from ..models import Binding, BindCode, QQGroup
from .utils import add_to_groups, bind, create_group, create_member, create_user, member_state, put_in_roster

MY_QQ = reverse("qqbot:my_qq")
SUBMIT = reverse("qqbot:member_submit")
CANCEL = reverse("qqbot:member_code_cancel")
NICKNAME = reverse("qqbot:member_nickname")
UNBIND = reverse("qqbot:member_unbind")

CODE_RE = re.compile(r"QQ-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}")

QQ = "12345678"
QQ_MASKED = "12****78"
OTHER_QQ = "87654321"


def page_code(response) -> str | None:
    match = CODE_RE.search(response.content.decode())
    return match.group(0) if match else None


def message_texts(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class MemberViewTestCase(TestCase):
    def setUp(self):
        cache.clear()  # code rate-limit counters
        self.user = create_member("kaela", character_name="Kaela Voss", corp_ticker="IGC")
        self.client.force_login(self.user)
        self.fixed = create_group("100001", name="联盟聊天群", description="全联盟日常聊天")
        self.role = create_group("100002", kind=QQGroup.Kind.ROLE, required=["Cap"],
                                 name="旗舰群")

    def post(self, url, data=None):
        return self.client.post(url, data or {}, follow=True)


class NotBoundPageTests(MemberViewTestCase):
    def test_form_with_card_prefix_and_rules(self):
        r = self.client.get(MY_QQ)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "绑定 QQ")
        self.assertContains(r, "[IGC] Kaela Voss -")
        self.assertContains(r, 'name="nickname"')
        self.assertContains(r, 'name="qq"')
        self.assertContains(r, "入群须知")
        self.assertContains(r, "禁止讨论政治话题")
        self.assertContains(r, "<br>")
        self.assertEqual(r.context["qqbot_nav"], "member")
        # No groups are listed before binding.
        self.assertNotContains(r, "100001")

    def test_no_main_character_notice(self):
        # AA's URL hook redirects users without a main before our view runs,
        # so call the view directly to check its own notice.
        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from ..views import member

        user = create_user("nomain", main=False, state=member_state())
        request = RequestFactory().get(MY_QQ)
        request.user = user
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        response = member.my_qq(request)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("你还没有设置主角色", content)
        self.assertNotIn('name="qq"', content)

    def test_no_main_character_cannot_submit(self):
        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from ..views import member

        user = create_user("nomain2", main=False, state=member_state())
        request = RequestFactory().post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        request.user = user
        request._dont_enforce_csrf_checks = True
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        response = member.submit(request)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BindCode.objects.filter(user=user).exists())
        self.assertFalse(Binding.objects.filter(user=user).exists())


class SubmitFlowTests(MemberViewTestCase):
    def test_trusted_when_in_fresh_roster(self):
        put_in_roster(self.fixed, [QQ])
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertContains(r, "绑定成功（老成员免验证）")
        self.assertContains(r, QQ_MASKED)
        self.assertContains(r, "老成员免验证")
        self.assertContains(r, "[IGC] Kaela Voss - 凯拉")
        # Eligible fixed group shown, role group (not in Cap) hidden.
        self.assertContains(r, "联盟聊天群")
        self.assertContains(r, "100001")
        self.assertNotContains(r, "100002")
        # The full QQ never appears, even for the owner.
        self.assertNotContains(r, QQ)
        self.assertEqual(Binding.objects.get(user=self.user).status, Binding.Status.TRUSTED)

    def test_pending_shows_code_and_instructions(self):
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertContains(r, "等待验证")
        code = page_code(r)
        self.assertIsNotNone(code)
        live = bindings.live_code(self.user)
        self.assertEqual(codes.hash_code(code), live.code_hash)
        self.assertContains(r, "验证信息")
        self.assertContains(r, "重新生成")
        self.assertContains(r, "取消")
        self.assertContains(r, QQ_MASKED)
        self.assertNotContains(r, QQ)
        # Groups the member may apply to with the code.
        self.assertContains(r, "100001")
        self.assertNotContains(r, "100002")
        self.assertEqual(self.client.session["qqbot_code"]["code"], code)
        self.assertFalse(Binding.objects.filter(user=self.user).exists())

    def test_pending_role_group_listed_when_in_required_group(self):
        add_to_groups(self.user, "Cap")
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertContains(r, "固定群")
        self.assertContains(r, "身份组小群")
        self.assertContains(r, "100002")

    def test_code_redisplayed_from_session_only(self):
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        code = page_code(r)
        r = self.client.get(MY_QQ)
        self.assertEqual(page_code(r), code)

        # Another browser (new session) does not see the code.
        from django.test import Client

        other = Client()
        other.force_login(self.user)
        r = other.get(MY_QQ)
        self.assertContains(r, "等待验证")
        self.assertIsNone(page_code(r))
        self.assertContains(r, "只在生成它的那个浏览器里显示")

    def test_session_code_not_matching_live_code_is_hidden(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        session = self.client.session
        session["qqbot_code"] = {**session["qqbot_code"], "code": "QQ-222222"}
        session.save()
        r = self.client.get(MY_QQ)
        self.assertContains(r, "等待验证")
        self.assertIsNone(page_code(r))

    def test_regenerate_invalidates_old_code(self):
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        old = page_code(r)
        old_row = bindings.live_code(self.user)
        r = self.post(SUBMIT, {"regenerate": "1"})
        new = page_code(r)
        self.assertIsNotNone(new)
        self.assertNotEqual(old, new)
        old_row.refresh_from_db()
        self.assertIsNotNone(old_row.invalidated_at)
        live = bindings.live_code(self.user)
        self.assertEqual(live.qq, QQ)
        self.assertEqual(live.nickname, "凯拉")
        self.assertEqual(codes.hash_code(new), live.code_hash)

    def test_regenerate_without_code(self):
        r = self.post(SUBMIT, {"regenerate": "1"})
        self.assertIn("验证码已经失效，请重新填写 QQ 号和昵称后提交。", message_texts(r))
        self.assertIsNone(bindings.live_code(self.user))

    def test_cancel(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        r = self.post(CANCEL)
        self.assertIn("验证码已取消。", message_texts(r))
        self.assertIsNone(bindings.live_code(self.user))
        self.assertNotIn("qqbot_code", self.client.session)
        self.assertNotContains(r, "等待验证")
        self.assertContains(r, "绑定 QQ")

    def test_expired_code_prefills_form(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        BindCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        r = self.client.get(MY_QQ)
        self.assertNotContains(r, "等待验证")
        self.assertContains(r, "验证码已经失效")
        self.assertContains(r, f'value="{QQ}"')
        self.assertContains(r, 'value="凯拉"')
        # The notice is shown once.
        r = self.client.get(MY_QQ)
        self.assertNotContains(r, "验证码已经失效")
        self.assertNotIn("qqbot_code", self.client.session)

    def test_used_code_is_forgotten(self):
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        code = page_code(r)
        result = bindings.claim(QQ, f"入群申请 {code}")
        self.assertTrue(result.ok)
        r = self.client.get(MY_QQ)
        self.assertContains(r, "已验证")
        self.assertNotIn("qqbot_code", self.client.session)
        self.assertNotContains(r, "验证码已经失效")

    def test_invalid_input(self):
        r = self.post(SUBMIT, {"qq": "0123", "nickname": "凯拉"})
        self.assertIn("请输入正确的 QQ 号（5–11 位数字，不能以 0 开头）。", message_texts(r))
        r = self.post(SUBMIT, {"qq": QQ, "nickname": ""})
        self.assertIn("请填写昵称。", message_texts(r))
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "a" * 13})
        self.assertTrue(any("最长" in m for m in message_texts(r)))
        self.assertIsNone(bindings.live_code(self.user))

    def test_taken(self):
        other = create_member("other", character_name="Other Pilot")
        bind(other, QQ, status=Binding.Status.VERIFIED)
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertIn(bindings.MSG_TAKEN, message_texts(r))
        self.assertNotContains(r, "Other Pilot")

    def test_cooldown(self):
        put_in_roster(self.fixed, [QQ, OTHER_QQ])
        bind(self.user, QQ, status=Binding.Status.TRUSTED, qq_changed_at=timezone.now())
        r = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertTrue(any("换绑太频繁" in m for m in message_texts(r)))
        self.assertEqual(Binding.objects.get(user=self.user).qq, QQ)

    def test_rate_limited(self):
        for _ in range(bindings.CODE_RATE_LIMIT):
            self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertTrue(any("太频繁" in m for m in message_texts(r)))

    def test_rebind_to_roster_qq(self):
        put_in_roster(self.fixed, [OTHER_QQ])
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertContains(r, "绑定成功")
        self.assertEqual(Binding.objects.get(user=self.user).qq, OTHER_QQ)

    def test_same_qq_updates_nickname(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "小凯"})
        self.assertIn("昵称已更新。", message_texts(r))
        r = self.post(SUBMIT, {"qq": QQ, "nickname": "小凯"})
        self.assertIn("没有需要修改的内容。", message_texts(r))


class BoundPageTests(MemberViewTestCase):
    def test_verified(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r = self.client.get(MY_QQ)
        self.assertContains(r, QQ_MASKED)
        self.assertContains(r, "已验证")
        self.assertContains(r, "[IGC] Kaela Voss - 凯拉")
        self.assertContains(r, "修改昵称")
        self.assertContains(r, "换绑 QQ")
        self.assertContains(r, "解除绑定")
        self.assertContains(r, "100001")
        self.assertNotContains(r, "100002")
        self.assertNotContains(r, QQ)

    def test_role_group_shown_when_eligible(self):
        add_to_groups(self.user, "Cap")
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(MY_QQ)
        self.assertContains(r, "身份组小群")
        self.assertContains(r, "旗舰群")
        self.assertContains(r, "100002")

    def test_inactive_group_hidden(self):
        QQGroup.objects.filter(pk=self.fixed.pk).update(is_active=False)
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(MY_QQ)
        self.assertNotContains(r, "100001")
        self.assertContains(r, "目前没有你可以加入的群")

    def test_conflict_hides_groups_and_other_user(self):
        other = create_member("rival", character_name="Rival Pilot", corp_ticker="RIV")
        bind(self.user, QQ, status=Binding.Status.TRUSTED, nickname="凯拉")
        bind(other, QQ, status=Binding.Status.TRUSTED, nickname="对手")
        r = self.client.get(MY_QQ)
        self.assertContains(r, "冲突 - 请联系 QQ 管理员")
        self.assertContains(r, "同时被其他账号认领")
        self.assertNotContains(r, "100001")
        self.assertNotContains(r, "Rival Pilot")
        self.assertNotContains(r, "rival")
        self.assertNotContains(r, "对手")
        self.assertNotContains(r, QQ)

    def test_trusted_shadowed_by_verified(self):
        other = create_member("owner", character_name="Owner Pilot")
        bind(self.user, QQ, status=Binding.Status.TRUSTED)
        bind(other, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(MY_QQ)
        self.assertContains(r, "已被其他账号验证")
        self.assertNotContains(r, "100001")
        self.assertNotContains(r, "Owner Pilot")

    def test_never_shows_other_users_qq(self):
        other = create_member("someone", character_name="Some One")
        bind(other, OTHER_QQ, status=Binding.Status.VERIFIED)
        put_in_roster(self.fixed, [OTHER_QQ, "55555555"])
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(MY_QQ)
        for text in (OTHER_QQ, "87****21", "55555555", "Some One"):
            self.assertNotContains(r, text)

    def test_nickname_change(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r = self.post(NICKNAME, {"nickname": "小凯"})
        self.assertIn("昵称已更新。", message_texts(r))
        self.assertContains(r, "[IGC] Kaela Voss - 小凯")
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "小凯")

    def test_nickname_invalid(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r = self.post(NICKNAME, {"nickname": "bad<>"})
        self.assertTrue(any("昵称只能包含" in m for m in message_texts(r)))
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "凯拉")

    def test_nickname_not_bound(self):
        r = self.post(NICKNAME, {"nickname": "小凯"})
        self.assertIn("没有绑定 QQ。", message_texts(r))

    def test_unbind_confirm_then_post(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(UNBIND)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "确认解除绑定")
        self.assertContains(r, QQ_MASKED)
        self.assertTrue(Binding.objects.filter(user=self.user).exists())
        r = self.post(UNBIND)
        self.assertIn("已解除绑定。", message_texts(r))
        self.assertFalse(Binding.objects.filter(user=self.user).exists())
        self.assertContains(r, "绑定 QQ")

    def test_unbind_get_when_not_bound_redirects(self):
        r = self.client.get(UNBIND)
        self.assertRedirects(r, MY_QQ)

    def test_bound_with_pending_rebind(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertContains(r, "等待验证")
        self.assertContains(r, QQ_MASKED)
        self.assertContains(r, "87****21")
        self.assertIsNotNone(page_code(r))
        self.assertEqual(Binding.objects.get(user=self.user).qq, QQ)
