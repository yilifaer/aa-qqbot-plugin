"""Member flows in the "QQ 绑定" card on AA's services page (DECISIONS.md #18).

Every state is checked on the real services page (``/services/``); every
POST goes back to ``/services/#qqbot``.
"""

import re
from datetime import timedelta
from html.parser import HTMLParser

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..core import bindings, codes
from ..models import Binding, BindCode, QQGroup
from .utils import add_to_groups, bind, create_group, create_member, put_in_roster

SERVICES = reverse("services:services")
BACK = SERVICES + "#qqbot"
MY_QQ = reverse("qqbot:my_qq")
SUBMIT = reverse("qqbot:member_submit")
CANCEL = reverse("qqbot:member_code_cancel")
NICKNAME = reverse("qqbot:member_nickname")
UNBIND = reverse("qqbot:member_unbind")

CODE_RE = re.compile(r"QQ-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}")

QQ = "12345678"
QQ_MASKED = "12****78"
OTHER_QQ = "87654321"

CODE_HIDDEN = "验证码只在生成它的浏览器里显示，请点「重新生成」。"
UNBIND_CONFIRM_MSG = "请先勾选「我确认要解除绑定」，再点「解除绑定」。"


class _CardFinder(HTMLParser):
    """Finds the source span of the element with ``id="qqbot"``."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.start = self.end = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        if self.start is None and ("id", "qqbot") in attrs:
            self.start = self.getpos()
            self.depth = 1
        elif self.start is not None and self.end is None:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "div" and self.start is not None and self.end is None:
            self.depth -= 1
            if self.depth == 0:
                self.end = self.getpos()


def _offset(html: str, pos) -> int:
    line, col = pos
    lines = html.split("\n")
    return sum(len(x) + 1 for x in lines[: line - 1]) + col


def card_of(html: str) -> str:
    """The HTML of the QQ card (``id="qqbot"``) inside a services page."""
    finder = _CardFinder()
    finder.feed(html)
    finder.close()
    assert finder.start is not None and finder.end is not None, "QQ card not found"
    return html[_offset(html, finder.start): _offset(html, finder.end) + len("</div>")]


def page_code(html: str) -> str | None:
    match = CODE_RE.search(html)
    return match.group(0) if match else None


_RESULT_RE = re.compile(r'id="qqbot-result">\s*<i [^>]*></i>\s*<div>(.*?)</div>', re.S)


def message_texts(card: str) -> list[str]:
    """The outcome shown at the top of the card (``#qqbot-result``)."""
    return [m.strip() for m in _RESULT_RE.findall(card)]


class MemberCardTestCase(TestCase):
    def setUp(self):
        cache.clear()  # code rate-limit counters
        self.user = create_member("kaela", character_name="Kaela Voss", corp_ticker="IGC")
        self.client.force_login(self.user)
        self.fixed = create_group("100001", name="联盟聊天群", description="全联盟日常聊天")
        self.role = create_group("100002", kind=QQGroup.Kind.ROLE, required=["Cap"],
                                 name="旗舰群")

    def card(self, client=None) -> str:
        r = (client or self.client).get(SERVICES)
        self.assertEqual(r.status_code, 200)
        return card_of(r.content.decode())

    def post(self, url, data=None):
        """POST, check the redirect back to the card; return ``(response, card)``."""
        r = self.client.post(url, data or {})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)
        follow = self.client.get(SERVICES)
        self.assertEqual(follow.status_code, 200)
        return r, card_of(follow.content.decode())


class UnboundCardTests(MemberCardTestCase):
    def test_form_with_card_prefix_and_rules(self):
        card = self.card()
        self.assertIn("绑定后可以加入联盟 QQ 群", card)
        self.assertIn(f'action="{SUBMIT}"', card)
        self.assertIn('<span class="input-group-text text-wrap text-break text-start">[IGC] Kaela Voss -', card)
        self.assertIn('name="nickname"', card)
        self.assertIn('name="qq"', card)
        self.assertIn("绑定", card)
        self.assertIn("老成员（已经在联盟 QQ 群里）填完马上生效", card)
        self.assertIn('text-bg-warning">未启用', card)
        # Rules: small muted text below the form, escaped, with <br>.
        rules = card[card.index('id="qqbot-rules"'):]
        self.assertIn("入群须知", rules)
        self.assertIn("禁止讨论政治话题", rules)
        self.assertIn("<br>", rules)
        self.assertLess(card.index('id="qqbot-bind-form"'), card.index('id="qqbot-rules"'))
        self.assertIn('class="small text-body-secondary" id="qqbot-rules"', card)
        # No groups before binding, no member actions.
        self.assertNotIn("100001", card)
        self.assertNotIn(NICKNAME, card)
        self.assertNotIn(UNBIND, card)
        self.assertNotIn("card-footer", card)  # nothing to put there

    def test_one_card_on_the_page(self):
        html = self.client.get(SERVICES).content.decode()
        self.assertEqual(html.count('id="qqbot"'), 1)

    def test_no_main_character_cannot_submit(self):
        from django.contrib.messages.middleware import MessageMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from ..views import member
        from .utils import create_user, member_state

        user = create_user("nomain2", main=False, state=member_state())
        request = RequestFactory().post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        request.user = user
        request._dont_enforce_csrf_checks = True
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        response = member.submit(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], BACK)
        self.assertFalse(BindCode.objects.filter(user=user).exists())
        self.assertFalse(Binding.objects.filter(user=user).exists())


class SubmitFlowTests(MemberCardTestCase):
    def test_trusted_when_in_fresh_roster(self):
        put_in_roster(self.fixed, [QQ])
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertIn("绑定成功（老成员免验证）。", message_texts(card))
        self.assertIn(QQ_MASKED, card)
        self.assertIn("老成员免验证", card)
        self.assertIn("[IGC] Kaela Voss - 凯拉", card)
        self.assertIn('text-bg-success">已启用', card)
        # Eligible fixed group shown, role group (not in Cap) hidden.
        self.assertIn("固定群", card)
        self.assertIn("联盟聊天群", card)
        self.assertIn("100001", card)
        self.assertNotIn("100002", card)
        # The full QQ never appears, even for the owner.
        self.assertNotIn(QQ, card)
        self.assertEqual(Binding.objects.get(user=self.user).status, Binding.Status.TRUSTED)

    def test_pending_shows_code_and_instructions(self):
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertTrue(any("验证码" in m for m in message_texts(card)))
        self.assertIn('text-bg-primary">待验证', card)
        code = page_code(card)
        self.assertIsNotNone(code)
        self.assertIn(f'id="qqbot-code">{code}</div>', card)
        live = bindings.live_code(self.user)
        self.assertEqual(codes.hash_code(code), live.code_hash)
        self.assertRegex(card, r"还有大约 \d+ 分钟有效")
        # The three short steps.
        for step in ("在 QQ 里搜索下面的群号", "点「申请加入」", "在「验证信息」里填验证码"):
            self.assertIn(step, card)
        # Footer: regenerate (POST submit with regenerate) and cancel.
        self.assertIn('name="regenerate" value="1"', card)
        self.assertIn("重新生成", card)
        self.assertIn(f'action="{CANCEL}"', card)
        self.assertIn("取消", card)
        self.assertIn(QQ_MASKED, card)
        self.assertNotIn(QQ, card)
        # Groups the member may apply to with the code.
        self.assertIn("可以申请的群", card)
        self.assertIn("100001", card)
        self.assertNotIn("100002", card)
        self.assertEqual(self.client.session["qqbot_code"]["code"], code)
        self.assertFalse(Binding.objects.filter(user=self.user).exists())

    def test_pending_role_group_listed_when_in_required_group(self):
        add_to_groups(self.user, "Cap")
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertIn("固定群", card)
        self.assertIn("身份组小群", card)
        self.assertIn("100002", card)
        self.assertLess(card.index("固定群"), card.index("身份组小群"))

    def test_code_redisplayed_from_session_only(self):
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        code = page_code(card)
        self.assertEqual(page_code(self.card()), code)

        # Another browser (new session) does not see the code.
        from django.test import Client

        other = Client()
        other.force_login(self.user)
        card = self.card(other)
        self.assertIn("待验证", card)
        self.assertIsNone(page_code(card))
        self.assertIn(CODE_HIDDEN, card)

    def test_session_code_not_matching_live_code_is_hidden(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        session = self.client.session
        session["qqbot_code"] = {**session["qqbot_code"], "code": "QQ-222222"}
        session.save()
        card = self.card()
        self.assertIn("待验证", card)
        self.assertIsNone(page_code(card))
        self.assertIn(CODE_HIDDEN, card)

    def test_regenerate_invalidates_old_code(self):
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        old = page_code(card)
        old_row = bindings.live_code(self.user)
        _r, card = self.post(SUBMIT, {"regenerate": "1"})
        new = page_code(card)
        self.assertIsNotNone(new)
        self.assertNotEqual(old, new)
        old_row.refresh_from_db()
        self.assertIsNotNone(old_row.invalidated_at)
        live = bindings.live_code(self.user)
        self.assertEqual(live.qq, QQ)
        self.assertEqual(live.nickname, "凯拉")
        self.assertEqual(codes.hash_code(new), live.code_hash)

    def test_regenerate_without_code(self):
        r, card = self.post(SUBMIT, {"regenerate": "1"})
        self.assertIn("验证码已经失效，请重新填写 QQ 号和昵称后提交。", message_texts(card))
        self.assertIsNone(bindings.live_code(self.user))

    def test_cancel(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        r, card = self.post(CANCEL)
        self.assertIn("验证码已取消。", message_texts(card))
        self.assertIsNone(bindings.live_code(self.user))
        self.assertNotIn("qqbot_code", self.client.session)
        self.assertNotIn("待验证", card)
        self.assertIn("绑定后可以加入联盟 QQ 群", card)

    def test_expired_code_prefills_form(self):
        self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        BindCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        card = self.card()
        self.assertNotIn("待验证", card)
        self.assertIn("验证码已经失效", card)
        self.assertIn(f'value="{QQ}"', card)
        self.assertIn('value="凯拉"', card)
        # The notice is shown once.
        card = self.card()
        self.assertNotIn("验证码已经失效", card)
        self.assertNotIn("qqbot_code", self.client.session)

    def test_used_code_is_forgotten(self):
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        code = page_code(card)
        result = bindings.claim(QQ, f"入群申请 {code}")
        self.assertTrue(result.ok)
        card = self.card()
        self.assertIn("已验证", card)
        self.assertNotIn("qqbot_code", self.client.session)
        self.assertNotIn("验证码已经失效", card)

    def test_invalid_input(self):
        r, card = self.post(SUBMIT, {"qq": "0123", "nickname": "凯拉"})
        self.assertIn("请输入正确的 QQ 号（5–11 位数字，不能以 0 开头）。", message_texts(card))
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": ""})
        self.assertIn("请填写昵称。", message_texts(card))
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "a" * 13})
        self.assertTrue(any("最长" in m for m in message_texts(card)))
        self.assertIsNone(bindings.live_code(self.user))

    def test_message_shown_in_the_card_once(self):
        """The outcome is shown inside the card, not as an AA message above
        the row of cards (off-screen after the jump to #qqbot on phones)."""
        self.client.post(SUBMIT, {"qq": "0123", "nickname": "凯拉"})
        r = self.client.get(SERVICES)
        html = r.content.decode()
        self.assertEqual(html.count("请输入正确的 QQ 号"), 1)
        self.assertEqual(message_texts(card_of(html)), ["请输入正确的 QQ 号（5–11 位数字，不能以 0 开头）。"])
        self.assertIn('class="alert alert-danger d-flex gap-2 py-2" role="alert" id="qqbot-result"', html)
        self.assertEqual(list(get_messages(r.wsgi_request)), [])
        # Shown once.
        self.assertEqual(message_texts(self.card()), [])

    def test_taken(self):
        other = create_member("other", character_name="Other Pilot")
        bind(other, QQ, status=Binding.Status.VERIFIED)
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertIn(bindings.MSG_TAKEN, message_texts(card))
        self.assertNotIn("Other Pilot", card)

    def test_cooldown(self):
        put_in_roster(self.fixed, [QQ, OTHER_QQ])
        bind(self.user, QQ, status=Binding.Status.TRUSTED, qq_changed_at=timezone.now())
        r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertTrue(any("换绑太频繁" in m for m in message_texts(card)))
        self.assertEqual(Binding.objects.get(user=self.user).qq, QQ)

    def test_rate_limited(self):
        for _ in range(bindings.CODE_RATE_LIMIT):
            self.client.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertTrue(any("太频繁" in m for m in message_texts(card)))

    def test_rebind_to_roster_qq(self):
        put_in_roster(self.fixed, [OTHER_QQ])
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertTrue(any("绑定成功" in m for m in message_texts(card)))
        self.assertIn("87****21", card)
        self.assertEqual(Binding.objects.get(user=self.user).qq, OTHER_QQ)

    def test_same_qq_updates_nickname(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "小凯"})
        self.assertIn("昵称已更新。", message_texts(card))
        r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "小凯"})
        self.assertIn("没有需要修改的内容。", message_texts(card))


class BoundCardTests(MemberCardTestCase):
    def test_verified(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        card = self.card()
        self.assertIn(QQ_MASKED, card)
        self.assertIn("（已验证）", card)
        self.assertIn('text-bg-success">已启用', card)
        self.assertIn("我的群名片：", card)
        self.assertIn("[IGC] Kaela Voss - 凯拉", card)
        self.assertIn("你可以加入的群", card)
        self.assertIn("100001", card)
        self.assertNotIn("100002", card)
        self.assertNotIn(QQ, card)
        # Footer toggles and their inline forms.
        for label, panel in (("改昵称", "qqbot-nickname-panel"), ("换绑", "qqbot-rebind-panel"),
                             ("解绑", "qqbot-unbind-panel")):
            self.assertIn(label, card)
            self.assertIn(f'data-bs-target="#{panel}"', card)
            self.assertIn(f'id="{panel}"', card)
        self.assertIn(f'action="{NICKNAME}"', card)
        self.assertIn(f'action="{UNBIND}"', card)
        self.assertIn('id="qqbot-rebind-form"', card)
        self.assertIn("新的 QQ 验证通过之前，现在的绑定保持不变", card)
        # Without JavaScript the panels are simply shown.
        self.assertIn("<noscript>", card)
        # No manager button for a plain member.
        self.assertNotIn(reverse("qqbot:manage_index"), card)

    def test_trusted_label(self):
        bind(self.user, QQ, status=Binding.Status.TRUSTED)
        self.assertIn("（老成员免验证）", self.card())

    def test_role_group_shown_when_eligible(self):
        add_to_groups(self.user, "Cap")
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        card = self.card()
        self.assertIn("身份组小群", card)
        self.assertIn("旗舰群", card)
        self.assertIn("100002", card)

    def test_inactive_group_hidden(self):
        QQGroup.objects.filter(pk=self.fixed.pk).update(is_active=False)
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        card = self.card()
        self.assertNotIn("100001", card)
        self.assertIn("目前没有你可以加入的群", card)

    def test_conflict_hides_groups_and_other_user(self):
        other = create_member("rival", character_name="Rival Pilot", corp_ticker="RIV")
        bind(self.user, QQ, status=Binding.Status.TRUSTED, nickname="凯拉")
        bind(other, QQ, status=Binding.Status.TRUSTED, nickname="对手")
        card = self.card()
        self.assertIn('text-bg-danger">冲突', card)
        self.assertIn("冲突 - 请联系 QQ 管理员", card)
        self.assertIn("同时被其他账号认领", card)
        self.assertIn('class="alert alert-danger', card)
        self.assertNotIn("100001", card)
        self.assertNotIn("你可以加入的群", card)
        self.assertNotIn("Rival Pilot", card)
        self.assertNotIn("rival", card)
        self.assertNotIn("对手", card)
        self.assertNotIn(QQ, card)
        # Nickname change and unbinding still work from the card.
        self.assertIn(f'action="{NICKNAME}"', card)
        self.assertIn(f'action="{UNBIND}"', card)

    def test_trusted_shadowed_by_verified(self):
        other = create_member("owner", character_name="Owner Pilot")
        bind(self.user, QQ, status=Binding.Status.TRUSTED)
        bind(other, QQ, status=Binding.Status.VERIFIED)
        card = self.card()
        self.assertIn('text-bg-danger">已被占用', card)
        self.assertIn("已被其他账号验证", card)
        self.assertIn("请联系 QQ 管理员", card)
        self.assertNotIn("100001", card)
        self.assertNotIn("Owner Pilot", card)
        self.assertIn(f'action="{UNBIND}"', card)

    def test_never_shows_other_users_qq(self):
        other = create_member("someone", character_name="Some One")
        bind(other, OTHER_QQ, status=Binding.Status.VERIFIED)
        put_in_roster(self.fixed, [OTHER_QQ, "55555555"])
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        card = self.card()
        for text in (OTHER_QQ, "87****21", "55555555", "Some One"):
            self.assertNotIn(text, card)

    def test_nickname_change(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        card = self.card()
        self.assertIn('id="qqbot-nickname-input"', card)
        self.assertIn('value="凯拉"', card)
        r, card = self.post(NICKNAME, {"nickname": "小凯"})
        self.assertIn("昵称已更新。", message_texts(card))
        self.assertIn("[IGC] Kaela Voss - 小凯", card)
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "小凯")

    def test_nickname_invalid(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        r, card = self.post(NICKNAME, {"nickname": "bad<>"})
        self.assertTrue(any("昵称只能包含" in m for m in message_texts(card)))
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "凯拉")

    def test_nickname_not_bound(self):
        r, card = self.post(NICKNAME, {"nickname": "小凯"})
        self.assertIn("没有绑定 QQ。", message_texts(card))

    def test_rebind_form_keeps_nickname(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        card = self.card()
        form = card[card.index('id="qqbot-rebind-form"'):]
        form = form[: form.index("</form>")]
        self.assertIn('name="nickname" value="凯拉"', form)
        self.assertIn('name="qq"', form)
        _r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertIn("正在验证的 QQ：<strong class=\"font-monospace\">87****21</strong>", card)

    def test_expired_rebind_code_notice_and_prefill(self):
        """A bound member's re-bind code ran out: say so once and re-open
        换绑 with the new QQ filled in (not only for unbound members)."""
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        BindCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        card = self.card()
        self.assertIn('id="qqbot-stale"', card)
        self.assertIn("你上次换绑拿到的验证码已经失效了", card)
        self.assertIn(QQ_MASKED, card)
        self.assertIn('class="collapse qqbot-panel show" id="qqbot-rebind-panel"', card)
        self.assertIn('data-bs-target="#qqbot-rebind-panel" aria-expanded="true"', card)
        self.assertIn(f'placeholder="新的 QQ 号" value="{OTHER_QQ}"', card)
        self.assertIn('class="collapse qqbot-panel" id="qqbot-nickname-panel"', card)
        self.assertNotIn("qqbot_code", self.client.session)
        # Once.
        card = self.card()
        self.assertNotIn("失效", card)
        self.assertNotIn(OTHER_QQ, card)
        self.assertIn('class="collapse qqbot-panel" id="qqbot-rebind-panel"', card)
        self.assertEqual(Binding.objects.get(user=self.user).qq, QQ)

    def test_pending_rebind_explains_problem_badge(self):
        """A re-bind from a QQ in conflict: the red header badge comes with
        the red explanation (DESIGN.md 4.2 ⑤), the new QQ's groups stay."""
        other = create_member("other", character_name="Other Pilot")
        bind(other, QQ, status=Binding.Status.TRUSTED)
        bind(self.user, QQ, status=Binding.Status.TRUSTED)
        _r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertIsNotNone(page_code(card))
        self.assertIn('text-bg-danger">冲突', card)
        problem = card[card.index('id="qqbot-problem"'):]
        problem = problem[: problem.index("请联系 QQ 管理员。")]
        self.assertIn("现在的 QQ：冲突 - 请联系 QQ 管理员", problem)
        self.assertIn("同时被其他账号认领", problem)
        self.assertIn("新 QQ 验证通过后", problem)
        self.assertIn("100001", card)  # where to apply with the new QQ's code
        self.assertNotIn("Other Pilot", card)

    def test_bound_with_pending_rebind(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        _r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertIn("现在绑定的 QQ", card)
        self.assertIn(QQ_MASKED, card)
        self.assertIn("87****21", card)
        self.assertIsNotNone(page_code(card))
        self.assertIn("重新生成", card)
        # The badge shows the current binding, which still works until the
        # new QQ is verified (DESIGN.md 4.2 ③).
        self.assertIn('text-bg-success">已启用', card)
        self.assertNotIn('id="qqbot-problem"', card)
        self.assertEqual(Binding.objects.get(user=self.user).qq, QQ)


class UnbindTests(MemberCardTestCase):
    def test_unbind_form_in_card(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        card = self.card()
        form = card[card.index('id="qqbot-unbind-form"'):]
        form = form[: form.index("</form>")]
        self.assertIn('type="checkbox" name="confirm" value="1" id="qqbot-unbind-confirm" required', form)
        self.assertIn("我确认要解除绑定", form)
        self.assertIn('class="btn btn-danger"', form)
        self.assertIn("csrfmiddlewaretoken", form)

    def test_unbind_requires_confirm_checkbox(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        for data in ({}, {"confirm": ""}):
            with self.subTest(data=data):
                r, card = self.post(UNBIND, data)
                self.assertIn(UNBIND_CONFIRM_MSG, message_texts(card))
                self.assertTrue(Binding.objects.filter(user=self.user).exists())
                self.assertIn(QQ_MASKED, card)

    def test_unbind_with_confirm(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r, card = self.post(UNBIND, {"confirm": "1"})
        self.assertIn("已解除绑定。", message_texts(card))
        self.assertFalse(Binding.objects.filter(user=self.user).exists())
        self.assertIn("绑定后可以加入联盟 QQ 群", card)

    def test_unbind_not_bound(self):
        r, card = self.post(UNBIND, {"confirm": "1"})
        self.assertTrue(message_texts(card))
        self.assertFalse(Binding.objects.filter(user=self.user).exists())

    def test_unbind_get_redirects_without_unbinding(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        r = self.client.get(UNBIND)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)
        self.assertTrue(Binding.objects.filter(user=self.user).exists())

    def test_unbind_mentions_running_cooldown(self):
        bind(self.user, QQ, qq_changed_at=timezone.now())
        card = self.card()
        self.assertIn('id="qqbot-unbind-cooldown"', card)
        self.assertIn("解除绑定不会让换绑冷却重新计算：还要等 24 小时才能改绑别的 QQ", card)
        self.assertIn("还要等 24 小时才能换绑", card)  # rebind panel too
        self.post(UNBIND, {"confirm": "1"})
        r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "凯拉"})
        self.assertTrue(any("换绑太频繁" in m for m in message_texts(card)))
        self.assertFalse(BindCode.objects.exists())

    def test_no_cooldown_warning_when_over(self):
        bind(self.user, QQ, qq_changed_at=timezone.now() - timedelta(days=2))
        card = self.card()
        self.assertNotIn("换绑冷却", card)
        self.assertNotIn('id="qqbot-unbind-cooldown"', card)
        self.assertIn("换绑后 24 小时内不能再换", card)


class OldPageTests(MemberCardTestCase):
    def test_my_qq_redirects_to_card(self):
        r = self.client.get(MY_QQ)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], BACK)


class ManagerButtonTests(MemberCardTestCase):
    def test_manager_gets_manage_button(self):
        from allianceauth.tests.auth_utils import AuthUtils

        from .utils import MANAGE

        AuthUtils.add_permission_to_user_by_name(MANAGE, self.user, disconnect_signals=True)
        manage = reverse("qqbot:manage_index")
        for setup in (lambda: None, lambda: bind(self.user, QQ)):
            setup()
            card = self.card()
            self.assertIn(f'href="{manage}"', card)
            self.assertIn("QQ 管理", card)


class CardLengthHintTests(TestCase):
    """DESIGN.md 6: when the card is too long the card says so."""

    LONG_NAME = "Kaela Vossington-Smithers"  # "[IGC] <name> - " is 34 bytes

    def setUp(self):
        cache.clear()

    def login(self, name):
        user = create_member("m", character_name=name, corp_ticker="IGC")
        self.client.force_login(user)
        return user

    def card(self):
        return card_of(self.client.get(SERVICES).content.decode())

    def test_short_name_no_hint(self):
        user = self.login("Kaela Voss")
        self.assertNotIn("角色名会被自动缩短", self.card())
        bind(user, QQ, nickname="凯拉")
        self.assertNotIn("已自动缩短", self.card())

    def test_long_name_form_hint(self):
        self.login(self.LONG_NAME)
        card = self.card()
        # 60 - 34 = 26 bytes: 8 CJK characters or 26 letters.
        self.assertIn("昵称最多写 8 个汉字（或 26 个英文字母、数字）", card)
        self.assertIn("角色名会被自动缩短", card)

    def test_long_card_notice_when_bound(self):
        user = self.login(self.LONG_NAME)
        bind(user, QQ, nickname="凯拉" * 6)
        card = self.card()
        self.assertNotIn(f"我的群名片：<strong class=\"text-break\">[IGC] {self.LONG_NAME}", card)
        self.assertIn("角色名已自动缩短", card)

    def test_manager_override_is_not_reported(self):
        user = self.login(self.LONG_NAME)
        bind(user, QQ, nickname="凯拉" * 6, card_override="固定名片")
        card = self.card()
        self.assertIn("固定名片", card)
        self.assertNotIn("角色名已自动缩短", card)


class QQInputTests(MemberCardTestCase):
    def test_qq_inputs_do_not_cut_pasted_numbers(self):
        """maxlength=11 made the browser silently drop the last digit of
        " 1234567890" (leading space) -- a different, valid QQ."""
        card = self.card()
        self.assertNotIn('maxlength="11"', card)
        self.assertIn('name="qq"', card)
        bind(self.user, QQ)
        card = self.card()
        self.assertNotIn('maxlength="11"', card)
        self.assertIn('name="qq"', card)


class CardResultTests(MemberCardTestCase):
    """A failed action comes back with its form open and filled in, and the
    outcome inside the card (docs/SPEC.md 5)."""

    def test_failed_bind_keeps_input(self):
        _r, card = self.post(SUBMIT, {"qq": "0123", "nickname": "小明"})
        self.assertEqual(message_texts(card), ["请输入正确的 QQ 号（5–11 位数字，不能以 0 开头）。"])
        self.assertIn('placeholder="你的 QQ 号" value="0123"', card)
        self.assertIn('placeholder="你的昵称" value="小明"', card)
        # The typed values are escaped.
        _r, card = self.post(SUBMIT, {"qq": '"><b>1', "nickname": "<i>x"})
        self.assertIn('value="&quot;&gt;&lt;b&gt;1"', card)
        self.assertIn('value="&lt;i&gt;x"', card)
        self.assertNotIn("<b>1", card)
        # A later page load starts empty again.
        card = self.card()
        self.assertIn('placeholder="你的 QQ 号" value=""', card)

    def test_taken_keeps_input(self):
        other = create_member("other", character_name="Other Pilot")
        bind(other, QQ, status=Binding.Status.VERIFIED)
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertEqual(message_texts(card), [bindings.MSG_TAKEN])
        self.assertIn(f'value="{QQ}"', card)  # the member's own input only

    def test_success_shown_in_card(self):
        put_in_roster(self.fixed, [QQ])
        _r, card = self.post(SUBMIT, {"qq": QQ, "nickname": "凯拉"})
        self.assertIn('class="alert alert-success', card)
        self.assertEqual(message_texts(card), ["绑定成功（老成员免验证）。"])
        self.assertNotIn(' show" id="qqbot-', card)  # no panel opened

    def test_failed_nickname_reopens_panel(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED, nickname="凯拉")
        _r, card = self.post(NICKNAME, {"nickname": "老张😀"})
        self.assertTrue(message_texts(card)[0].startswith("昵称只能包含"))
        self.assertIn('class="collapse qqbot-panel show" id="qqbot-nickname-panel"', card)
        self.assertIn('data-bs-target="#qqbot-nickname-panel" aria-expanded="true"', card)
        self.assertIn('id="qqbot-nickname-input"', card)
        self.assertIn('value="老张😀"', card)
        self.assertIn('class="collapse qqbot-panel" id="qqbot-rebind-panel"', card)
        # The re-bind form still sends the saved nickname.
        self.assertIn('<input type="hidden" name="nickname" value="凯拉">', card)
        self.assertEqual(Binding.objects.get(user=self.user).nickname, "凯拉")

    def test_failed_rebind_reopens_panel(self):
        put_in_roster(self.fixed, [QQ, OTHER_QQ])
        bind(self.user, QQ, status=Binding.Status.TRUSTED, qq_changed_at=timezone.now() - timedelta(hours=16, minutes=6))
        _r, card = self.post(SUBMIT, {"qq": OTHER_QQ, "nickname": "n"})
        self.assertEqual(message_texts(card), ["换绑太频繁，请在 7 小时 54 分钟后再试。"])
        self.assertIn('class="collapse qqbot-panel show" id="qqbot-rebind-panel"', card)
        self.assertIn(f'placeholder="新的 QQ 号" value="{OTHER_QQ}"', card)
        # The wait is shown as time left (no clock time in AA's UTC).
        self.assertIn("还要等 7 小时 54 分钟才能换绑", card)
        self.assertNotRegex(card, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_unbind_without_confirm_reopens_panel(self):
        bind(self.user, QQ, status=Binding.Status.VERIFIED)
        _r, card = self.post(UNBIND, {})
        self.assertEqual(message_texts(card), [UNBIND_CONFIRM_MSG])
        self.assertIn('class="alert alert-warning', card)
        self.assertIn('class="collapse qqbot-panel show" id="qqbot-unbind-panel"', card)

    def test_result_is_dropped_when_old_or_malformed(self):
        from ..views.member import RESULT_KEY, RESULT_MAX_AGE

        now = timezone.now().timestamp()
        for data in (
            {"level": "error", "text": "旧消息", "ok": False, "at": now - RESULT_MAX_AGE - 5},
            {"level": "bogus", "text": "旧消息", "ok": False, "at": now},
            {"level": "error", "text": "", "ok": False, "at": now},
            {"level": "error", "text": "旧消息", "ok": False},
            "旧消息",
        ):
            with self.subTest(data=data):
                session = self.client.session
                session[RESULT_KEY] = data
                session.save()
                card = self.card()
                self.assertNotIn("旧消息", card)
                self.assertNotIn('id="qqbot-result"', card)
                self.assertNotIn(RESULT_KEY, self.client.session)
