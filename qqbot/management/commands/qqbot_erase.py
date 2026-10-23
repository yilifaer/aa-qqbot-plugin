"""``python manage.py qqbot_erase --qq <QQ> [--user <username>]``: erase one
person's personal data (for example on a deletion request).

Refused while a binding exists for that QQ or that AA user: unbind or
force-unbind it first (or delete the AA user). Audit log entries about the
person are anonymised, entries they made lose the actor, and their QQ /
username is removed from the details of other entries; their verification
codes, events and member list entries are deleted. Afterwards the rebind
cooldown and the force-unbind restriction (a code is needed to bind a QQ
that a manager force-unbound from that user) no longer know about this
person, because both read the audit log.

``python manage.py qqbot_erase --qq <QQ> [--user <用户名>]``：删除某一个人的
个人数据（例如收到删除请求时）。

只要这个 QQ 或这个 AA 用户还有绑定，就拒绝执行：请先解绑或强制解绑（或者删除
这个 AA 用户）。关于这个人的审计日志会被匿名化，他作为操作者的记录会去掉操作者，
其他记录详情里的他的 QQ / 用户名也会被去掉；他的验证码、事件和群成员名单记录会被
删除。之后换绑冷却和强制解绑限制（管理员从这个用户手里强制解绑过的 QQ，再绑时
必须用验证码）都不再知道这个人，因为这两项都是读审计日志的。
"""

from django.core.management.base import BaseCommand, CommandError

from ...core import privacy
from ...models import normalize_qq

# Bilingual plain strings (English first), as in qqbot_reconcile.
# 中英双语的普通字符串（英文在前），和 qqbot_reconcile 一样。
_COUNTS_EN = (
    "{audit_about} audit log entries about the person (anonymised), {audit_actor} entries "
    "they made (actor cleared), {audit_mentions} other entries with their QQ or username in "
    "the details (replaced by [erased]); {codes} verification codes, {events} events and "
    "{roster} member list entries (deleted)."
)
_COUNTS_ZH = (
    "关于此人的审计日志 {audit_about} 条（匿名化），此人操作的记录 {audit_actor} 条"
    "（清除操作者），详情里有其 QQ 或用户名的其他记录 {audit_mentions} 条（替换为 [erased]）；"
    "验证码 {codes} 个、事件 {events} 条、群成员名单记录 {roster} 条（删除）。"
)
PLAN = "To change: " + _COUNTS_EN + "\n将改动：" + _COUNTS_ZH
DONE = "Done: " + _COUNTS_EN + "\n完成：" + _COUNTS_ZH


class Command(BaseCommand):
    help = (
        "Erase one person's personal data: anonymise their audit log entries and delete their "
        "verification codes, events and member list entries. Unbind them first. / "
        "删除某一个人的个人数据：匿名化其审计日志，删除其验证码、事件和群成员名单记录。"
        "请先解绑。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--qq",
            default="",
            help="The person's QQ number. / 此人的 QQ 号。",
        )
        parser.add_argument(
            "--user",
            default="",
            help=(
                "The person's Alliance Auth username (also works after the user was deleted). / "
                "此人的 Alliance Auth 用户名（用户删除之后也可以用）。"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show what would change; change nothing. / 只显示会改动什么，不做任何修改。",
        )
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="Do not ask for confirmation. / 不要求确认。",
        )

    def handle(self, *args, **options):
        raw_qq = (options["qq"] or "").strip()
        username = (options["user"] or "").strip()
        if not raw_qq and not username:
            raise CommandError(
                "Give --qq and / or --user.\n请指定 --qq 和 / 或 --user。"
            )
        qq = normalize_qq(raw_qq) if raw_qq else ""
        if raw_qq and not qq:
            raise CommandError(
                "--qq must be 5-11 digits, not starting with 0.\n"
                "--qq 必须是 5–11 位数字，不能以 0 开头。"
            )
        person = privacy.find_person(qq, username)
        if username and person.user is None:
            self.stdout.write(
                f'No Alliance Auth user is called "{username}"; only the saved username is '
                "matched.\n"
                f"没有名为“{username}”的 Alliance Auth 用户；只按记录里保存的用户名匹配。"
            )
        try:
            counts = privacy.preview(person)
        except privacy.StillBound as e:
            raise CommandError(self._still_bound(e)) from None
        self.stdout.write(PLAN.format(**counts))
        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was changed.\n试运行：没有改动任何数据。")
            return
        if options["interactive"]:
            try:
                answer = input('Type "yes" to continue / 输入 "yes" 继续: ')
            except EOFError:  # no terminal / 没有终端可以输入
                answer = ""
            if answer.strip() != "yes":
                self.stdout.write("Cancelled: nothing was changed.\n已取消：没有改动任何数据。")
                return
        try:
            counts = privacy.erase(person)
        except privacy.StillBound as e:
            raise CommandError(self._still_bound(e)) from None
        self.stdout.write(self.style.SUCCESS(DONE.format(**counts)))
        if qq:
            self.stdout.write(
                "If the person is still in a managed QQ group, the bot reports this QQ again "
                "with its next full member list.\n"
                "如果此人还在受管的 QQ 群里，机器人下一次上报完整群成员名单时会再次报告这个 QQ。"
            )

    @staticmethod
    def _still_bound(error) -> str:
        bound = ", ".join(f"{b} ({b.status})" for b in error.bindings)
        return (
            f"Still bound: {bound}. Unbind or force-unbind first (or delete the Alliance Auth "
            "user), then run this command again. Nothing was changed.\n"
            f"仍有绑定：{bound}。请先解绑或强制解绑（或者删除这个 Alliance Auth 用户），"
            "然后再运行这个命令。没有改动任何数据。"
        )
