"""``python manage.py qqbot_erase [--qq <QQ>] [--user <username>] [--dry-run]
[--noinput]`` (at least one of ``--qq`` / ``--user``): erase one person's
personal data (for example on a deletion request).

Refused while a binding exists for that QQ or that AA user, also another
account's binding of the same QQ: remove it first (unbind, force-unbind or
delete the AA user). Audit log entries about the person are anonymised,
entries they made show ``[erased]`` as the actor, and their username / QQ is
removed from the fields of other entries that name them. Entries on the QQ
that belong to other accounts are kept (see ``core/privacy.py``). Their
verification codes, events and member list entries are deleted; if events
were deleted, one "recheck all" event tells the bot to check every group
again. Afterwards the rebind cooldown and the force-unbind restriction (a
code is needed to bind a QQ that a manager force-unbound from that user) no
longer know about this person, because both read the audit log.

``python manage.py qqbot_erase [--qq <QQ>] [--user <用户名>] [--dry-run]
[--noinput]``（``--qq`` 和 ``--user`` 至少给一个）：删除某一个人的个人数据
（例如收到删除请求时）。

只要这个 QQ 或这个 AA 用户还有绑定（包括别的账号对同一个 QQ 的绑定），就拒绝
执行：请先去掉绑定（解绑、强制解绑或者删除这个 AA 用户）。关于这个人的审计日志
会被匿名化，他操作的记录里操作者显示为 ``[erased]``，其他记录里指向他的字段也会
去掉他的用户名 / QQ。这个 QQ 上属于其他账号的记录会保留（见 ``core/privacy.py``）。
他的验证码、事件和群成员名单记录会被删除；如果删了事件，会再发一条“全部重查”
事件，让机器人把所有群重新检查一遍。之后换绑冷却和强制解绑限制（管理员从这个
用户手里强制解绑过的 QQ，再绑时必须用验证码）都不再知道这个人，因为这两项都是
读审计日志的。
"""

from django.core.management.base import BaseCommand, CommandError

from ...core import privacy
from ...models import normalize_qq

# Bilingual plain strings (English first), as in qqbot_reconcile.
# 中英双语的普通字符串（英文在前），和 qqbot_reconcile 一样。
_COUNTS_EN = (
    "{audit_about} audit log entries about the person (anonymised), {audit_actor} entries "
    "they made (actor shown as [erased]), {audit_mentions} other entries that name them "
    "(name replaced by [erased]), {audit_kept} entries on this QQ about other accounts "
    "(kept); {codes} verification codes, {events} events and {roster} member list entries "
    "(deleted)."
)
_COUNTS_ZH = (
    "关于此人的审计日志 {audit_about} 条（匿名化），此人操作的记录 {audit_actor} 条"
    "（操作者显示为 [erased]），指向此人的其他记录 {audit_mentions} 条（名字替换为 [erased]），"
    "这个 QQ 上关于其他账号的记录 {audit_kept} 条（保留）；"
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
        others = privacy.other_accounts(person)
        if others:
            names = ", ".join(others)
            self.stdout.write(
                f"Kept: entries on this QQ about other accounts: {names}. If one of them is "
                "this same person, run the command again with --user as well.\n"
                f"保留：这个 QQ 上关于其他账号的记录：{names}。如果其中有一个就是这个人，"
                "请再加上 --user 运行一次。"
            )
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
        if counts["events"]:
            self.stdout.write(
                'A "recheck all" event was added, so the bot checks every group again soon.\n'
                "已添加一条“全部重查”事件，机器人很快会把所有群重新检查一遍。"
            )
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
            f"Still bound: {bound}. Remove these bindings first (unbind, force-unbind or "
            "delete the Alliance Auth user), then run this command again. Nothing was "
            "changed.\n"
            f"仍有绑定：{bound}。请先去掉这些绑定（解绑、强制解绑或者删除这个 Alliance Auth "
            "用户），然后再运行这个命令。没有改动任何数据。"
        )
