"""The ``qqbot_erase`` management command (core/privacy.py).

管理命令 ``qqbot_erase``（core/privacy.py）。
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ..core import audit, codes, privacy
from ..models import AuditLog, BindCode, Event, RosterEntry
from .utils import bind, create_group, create_member, put_in_roster

QQ = "12345678"
BOB_QQ = "23456789"
Action = AuditLog.Action


def erase(*args, **kwargs):
    out = StringIO()
    call_command("qqbot_erase", *args, stdout=out, **kwargs)
    return out.getvalue()


def snapshot():
    return {
        "audit": list(
            AuditLog.objects.order_by("pk").values_list(
                "pk", "actor_id", "actor_name", "qq", "target_user_id", "target_name", "detail"
            )
        ),
        "codes": list(BindCode.objects.order_by("pk").values_list("pk", flat=True)),
        "events": list(Event.objects.order_by("pk").values_list("pk", flat=True)),
        "roster": list(RosterEntry.objects.order_by("pk").values_list("pk", flat=True)),
    }


class EraseTests(TestCase):
    def setUp(self):
        self.group = create_group(100001)
        self.alice = create_member("alice")  # the person / 要删除数据的人
        self.bob = create_member("bob")
        bind(self.bob, BOB_QQ)
        # About alice: her QQ, her user, both.
        # 关于 alice 的记录：按她的 QQ、她的用户，或两者都有。
        self.bound = audit.log(
            Action.BIND, actor=self.alice, qq=QQ, target_user=self.alice, status="trusted"
        )
        self.claim = audit.log(Action.CLAIM_FAILED, qq=QQ, reason="qq_mismatch",
                               applicant_qq=BOB_QQ)
        self.nickname = audit.log(Action.NICKNAME, actor=self.alice, target_user=self.alice,
                                  old="old nick", new="new nick")
        # Other people's entries that mention alice.
        # 提到了 alice 的其他人的记录。
        self.acted = audit.log(Action.CONFIRM, actor=self.alice, qq=BOB_QQ, target_user=self.bob)
        self.conflict = audit.log(Action.CONFLICT, actor=self.bob, qq=BOB_QQ,
                                  target_user=self.bob, others=["alice", "carol"])
        self.applicant = audit.log(Action.CLAIM_FAILED, qq=BOB_QQ, target_user=self.bob,
                                   reason="qq_mismatch", applicant_qq=QQ)
        self.nested = audit.log(Action.GROUP, actor=self.bob, op="update",
                                changed={"seen": [QQ, {"by": "alice"}, 12345678, "alice2"]})
        self.unrelated = audit.log(Action.CARD, actor=self.bob, qq=BOB_QQ, target_user=self.bob,
                                   old="", new=f"{QQ} alice")
        now = timezone.now()

        def code(user, qq):
            return BindCode.objects.create(
                user=user, qq=qq, nickname="n", code_hash=codes.hash_code(codes.generate_code()),
                expires_at=now + timedelta(minutes=5),
            )

        self.alice_code = code(self.alice, "34567890")  # alice's code / alice 的验证码
        self.qq_code = code(self.bob, QQ)  # a code for alice's QQ / 申请 alice 的 QQ 的验证码
        self.bob_code = code(self.bob, BOB_QQ)
        self.event = Event.objects.create(kind=Event.Kind.RECHECK, qq=QQ)
        self.bob_event = Event.objects.create(kind=Event.Kind.RECHECK, qq=BOB_QQ)
        self.all_event = Event.objects.create(kind=Event.Kind.RECHECK_ALL)
        put_in_roster(self.group, [QQ, BOB_QQ])

    def row(self, entry):
        return AuditLog.objects.get(pk=entry.pk)

    def test_needs_qq_or_user(self):
        with self.assertRaisesMessage(CommandError, "--qq"):
            erase("--noinput")
        with self.assertRaisesMessage(CommandError, "--qq"):
            erase("--qq", " ", "--user", "", "--noinput")

    def test_invalid_qq(self):
        before = snapshot()
        for value in ("abc", "0123456", "1234", "123456789012"):
            with self.subTest(value=value), self.assertRaisesMessage(CommandError, "5-11"):
                erase("--qq", value, "--noinput")
        self.assertEqual(snapshot(), before)

    def test_qq_is_normalised(self):
        erase("--qq", " １２３４５６７８ ", "--noinput")
        self.assertFalse(Event.objects.filter(qq=QQ).exists())

    def test_refused_while_qq_bound(self):
        for status in ("verified", "trusted"):
            with self.subTest(status=status):
                binding = bind(create_member(f"dave_{status}"), QQ, status=status)
                before = snapshot()
                with self.assertRaisesMessage(CommandError, f"dave_{status} - {QQ} ({status})"):
                    erase("--qq", QQ, "--noinput")
                self.assertEqual(snapshot(), before)
                binding.delete()

    def test_refused_while_user_bound(self):
        bind(self.alice, "34567890")
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "Still bound: alice - 34567890"):
            erase("--qq", QQ, "--user", "alice", "--noinput")
        with self.assertRaisesMessage(CommandError, "Still bound"):
            erase("--user", "alice", "--dry-run")
        self.assertEqual(snapshot(), before)

    def test_refused_when_bound_after_preview(self):
        # A binding made between the preview and the transaction still stops it.
        # 在预览和事务之间新建的绑定，仍然会让它停下。
        person = privacy.find_person(QQ, "alice")
        bind(self.alice, "34567890")
        before = snapshot()
        with self.assertRaises(privacy.StillBound):
            privacy.erase(person)
        self.assertEqual(snapshot(), before)

    def test_dry_run_changes_nothing(self):
        before = snapshot()
        with mock.patch("builtins.input") as ask:
            out = erase("--qq", QQ, "--user", "alice", "--dry-run")
        ask.assert_not_called()
        self.assertEqual(snapshot(), before)
        self.assertIn(
            "3 audit log entries about the person (anonymised), 3 entries they made "
            "(actor cleared), 3 other entries",
            out,
        )
        self.assertIn("2 verification codes, 1 events and 1 member list entries", out)
        self.assertIn("Dry run", out)

    def test_erase_anonymises_audit_log(self):
        out = erase("--qq", QQ, "--user", "alice", "--noinput")
        self.assertIn("Done", out)
        for entry in (self.bound, self.claim, self.nickname):
            with self.subTest(action=entry.action):
                row = self.row(entry)
                self.assertEqual(
                    (row.qq, row.target_user_id, row.target_name, row.detail),
                    ("", None, "", {"erased": True}),
                )
                self.assertEqual((row.actor_id, row.actor_name), (None, ""))
                self.assertEqual(row.action, entry.action)
        # Entries alice made about others: only the actor goes.
        # alice 对别人做的操作：只去掉操作者。
        acted = self.row(self.acted)
        self.assertEqual((acted.actor_id, acted.actor_name), (None, ""))
        self.assertEqual((acted.qq, acted.target_user_id, acted.target_name),
                         (BOB_QQ, self.bob.pk, "bob"))
        # Her QQ / username inside other entries' details (exact matches only).
        # 其他记录详情里她的 QQ / 用户名（只替换完全相同的值）。
        self.assertEqual(self.row(self.conflict).detail["others"], ["[erased]", "carol"])
        self.assertEqual(self.row(self.conflict).actor_name, "bob")
        self.assertEqual(self.row(self.applicant).detail["applicant_qq"], "[erased]")
        self.assertEqual(
            self.row(self.nested).detail["changed"],
            {"seen": ["[erased]", {"by": "[erased]"}, 12345678, "alice2"]},
        )
        unrelated = self.row(self.unrelated)
        self.assertEqual(unrelated.detail, {"old": "", "new": f"{QQ} alice"})
        self.assertEqual((unrelated.actor_name, unrelated.qq), ("bob", BOB_QQ))
        self.assertEqual(AuditLog.objects.count(), 8)  # nothing deleted / 一条都没删
        self.assertFalse(AuditLog.objects.filter(qq=QQ).exists())
        self.assertFalse(AuditLog.objects.filter(target_user=self.alice).exists())
        self.assertFalse(AuditLog.objects.filter(actor=self.alice).exists())
        self.assertFalse(AuditLog.objects.filter(actor_name="alice").exists())

    def test_erase_deletes_codes_events_roster(self):
        out = erase("--qq", QQ, "--user", "alice", "--noinput")
        self.assertEqual(set(BindCode.objects.values_list("pk", flat=True)), {self.bob_code.pk})
        self.assertEqual(set(Event.objects.values_list("pk", flat=True)),
                         {self.bob_event.pk, self.all_event.pk})
        self.assertEqual(list(RosterEntry.objects.values_list("qq", flat=True)), [BOB_QQ])
        self.assertIn("2 verification codes, 1 events and 1 member list entries", out)
        self.assertIn("next full member list", out)
        # The user account itself is untouched; bob's binding too.
        # AA 用户本身不动；bob 的绑定也不动。
        self.assertTrue(type(self.alice).objects.filter(pk=self.alice.pk).exists())
        self.assertTrue(self.bob.qqbot_binding.pk)

    def test_erase_by_qq_only(self):
        out = erase("--qq", QQ, "--noinput")
        # Entries found by QQ; her own bind also loses its actor (she acted on
        # herself). The nickname entry (user only) and her actions on others
        # need --user.
        # 按 QQ 找到的记录；她自己的绑定记录同时去掉操作者（她对自己操作）。
        # 只关联用户的改昵称记录，以及她对别人的操作，需要 --user。
        bound = self.row(self.bound)
        self.assertEqual((bound.detail, bound.actor_id, bound.actor_name),
                         ({"erased": True}, None, ""))
        self.assertEqual(self.row(self.claim).detail, {"erased": True})
        self.assertEqual(self.row(self.nickname).detail, {"old": "old nick", "new": "new nick"})
        self.assertEqual(self.row(self.acted).actor_name, "alice")
        self.assertEqual(self.row(self.applicant).detail["applicant_qq"], "[erased]")
        self.assertEqual(self.row(self.conflict).detail["others"], ["alice", "carol"])
        self.assertTrue(BindCode.objects.filter(pk=self.alice_code.pk).exists())
        self.assertFalse(BindCode.objects.filter(qq=QQ).exists())
        self.assertIn("next full member list", out)

    def test_erase_by_user_only(self):
        out = erase("--user", "alice", "--noinput")
        self.assertEqual(self.row(self.bound).detail, {"erased": True})
        self.assertEqual(self.row(self.nickname).detail, {"erased": True})
        # The QQ-only entry and the QQ's events / roster need --qq.
        # 只关联 QQ 的记录，以及这个 QQ 的事件 / 名单，需要 --qq。
        self.assertEqual(self.row(self.claim).qq, QQ)
        self.assertTrue(Event.objects.filter(qq=QQ).exists())
        self.assertTrue(RosterEntry.objects.filter(qq=QQ).exists())
        self.assertFalse(BindCode.objects.filter(user=self.alice).exists())
        self.assertTrue(BindCode.objects.filter(pk=self.qq_code.pk).exists())
        self.assertNotIn("next full member list", out)

    def test_erase_deleted_user_by_saved_name(self):
        # carol's AA user is gone; entries keep only her username.
        # carol 的 AA 用户已经删除；记录里只剩她的用户名。
        gone = AuditLog.objects.create(
            action=Action.USER_DELETED, qq="45678901", target_name="carol",
            actor_name="carol", detail={"status": "verified"},
        )
        out = erase("--user", "carol", "--noinput")
        self.assertIn('No Alliance Auth user is called "carol"', out)
        row = self.row(gone)
        self.assertEqual((row.qq, row.target_name, row.actor_name, row.detail),
                         ("", "", "", {"erased": True}))
        self.assertEqual(self.row(self.conflict).detail["others"], ["alice", "[erased]"])

    def test_confirmation_yes(self):
        with mock.patch("builtins.input", return_value=" yes ") as ask:
            out = erase("--qq", QQ, "--user", "alice")
        ask.assert_called_once()
        self.assertIn('"yes"', ask.call_args.args[0])
        self.assertIn("To change", out)
        self.assertIn("Done", out)
        self.assertEqual(self.row(self.bound).detail, {"erased": True})

    def test_confirmation_refused(self):
        before = snapshot()
        for answer in ("", "no", "y", "YES please"):
            with self.subTest(answer=answer), \
                    mock.patch("builtins.input", return_value=answer):
                out = erase("--qq", QQ, "--user", "alice")
                self.assertIn("Cancelled", out)
                self.assertNotIn("Done", out)
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertIn("Cancelled", erase("--qq", QQ))
        self.assertEqual(snapshot(), before)

    def test_noinput_does_not_ask(self):
        with mock.patch("builtins.input") as ask:
            erase("--qq", QQ, "--no-input")
        ask.assert_not_called()
        self.assertFalse(Event.objects.filter(qq=QQ).exists())

    def test_log_line_has_counts_only(self):
        with self.assertLogs(privacy.logger, "INFO") as logs:
            erase("--qq", QQ, "--user", "alice", "--noinput")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("3 audit log entries anonymised", logs.output[0])
        self.assertNotIn(QQ, logs.output[0])
        self.assertNotIn("alice", logs.output[0])

    def test_one_transaction(self):
        before = snapshot()
        with mock.patch.object(privacy, "_roster", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            privacy.erase(privacy.find_person(QQ, "alice"))
        self.assertEqual(snapshot(), before)

    def test_preview_matches_erase(self):
        person = privacy.find_person(QQ, "alice")
        self.assertEqual(privacy.preview(person), privacy.erase(person))
        # Run again: nothing left to do.
        # 再运行一次：已经没有要做的了。
        self.assertEqual(
            privacy.erase(person),
            {"audit_about": 0, "audit_actor": 0, "audit_mentions": 0,
             "codes": 0, "events": 0, "roster": 0},
        )
