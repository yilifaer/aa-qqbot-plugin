"""Commands in the docs that change data: they must stay correct and touch
only qqbot's own rows. The version, the system checks and the options of
``qqbot_erase`` named in the docs must match the code.

文档里会改数据的命令：必须一直正确，而且只动 qqbot 自己的数据。文档里写的
版本号、系统检查和 ``qqbot_erase`` 的选项必须和代码一致。
"""

import contextlib
import io
import re
import shlex
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command, load_command_class
from django.test import SimpleTestCase, TestCase

from .. import __version__, checks
from ..core import audit
from ..models import AuditLog

ROOT = Path(__file__).resolve().parents[2]

# The docs that carry install / upgrade commands.
# 带有安装、升级命令的文档。
INSTALL_DOCS = ("README.md", "README.zh-CN.md", "docs/GUIDE.md", "docs/GUIDE.en.md")
# The docs that list the data protection facts.
# 写了数据保护内容的文档。
DATA_DOCS = ("README.md", "README.zh-CN.md", "docs/GUIDE.md", "docs/GUIDE.en.md")
# The two languages of the step-by-step guide.
# 分步说明的两个语言版本。
GUIDES = ("docs/GUIDE.md", "docs/GUIDE.en.md")
# The options every Django management command has.
# 每个 Django 管理命令都有的选项。
DJANGO_OPTION_DESTS = {
    "help", "version", "verbosity", "settings", "pythonpath", "traceback",
    "no_color", "force_color", "skip_checks",
}

# Verbatim the Python code inside `python manage.py shell -c "..."` in the docs.
# 与文档里 `python manage.py shell -c "..."` 引号中的 Python 代码逐字相同。
UNINSTALL_CODE = (
    "from django.contrib.contenttypes.models import ContentType; "
    "print(ContentType.objects.filter(app_label='qqbot').delete())"
)
CLEANUP_0X_CODE = (
    "from django.apps import apps; "
    "from django.contrib.contenttypes.models import ContentType; "
    "live = {m._meta.model_name for m in apps.get_app_config('qqbot').get_models()}; "
    "print(ContentType.objects.filter(app_label='qqbot').exclude(model__in=live).delete())"
)


def run(code):
    with contextlib.redirect_stdout(io.StringIO()):
        exec(code, {})


class UninstallCommandTests(TestCase):
    def setUp(self):
        # Leftovers of another, already uninstalled plugin.
        # 另一个已卸载插件留下的残留。
        self.other = ContentType.objects.create(app_label="srp", model="srpfleetmain")
        Permission.objects.create(codename="access_srp", name="x", content_type=self.other)

    def test_docs_carry_the_commands(self):
        for name in ("README.md", "README.zh-CN.md", *GUIDES):
            with self.subTest(doc=name):
                self.assertIn(UNINSTALL_CODE, (ROOT / name).read_text(encoding="utf-8"))
        for name in GUIDES:
            with self.subTest(doc=name):
                self.assertIn(CLEANUP_0X_CODE, (ROOT / name).read_text(encoding="utf-8"))

    def test_uninstall_deletes_only_qqbot(self):
        types_before = ContentType.objects.exclude(app_label="qqbot").count()
        perms_before = Permission.objects.exclude(content_type__app_label="qqbot").count()
        self.assertTrue(ContentType.objects.filter(app_label="qqbot").exists())
        run(UNINSTALL_CODE)
        self.assertFalse(ContentType.objects.filter(app_label="qqbot").exists())
        self.assertFalse(Permission.objects.filter(content_type__app_label="qqbot").exists())
        self.assertEqual(ContentType.objects.exclude(app_label="qqbot").count(), types_before)
        self.assertEqual(
            Permission.objects.exclude(content_type__app_label="qqbot").count(), perms_before
        )
        self.assertTrue(Permission.objects.filter(content_type=self.other).exists())

    def test_0x_cleanup_deletes_only_stale_qqbot_types(self):
        ContentType.objects.create(app_label="qqbot", model="qqbinding")
        live = len(list(apps.get_app_config("qqbot").get_models()))
        run(CLEANUP_0X_CODE)
        self.assertFalse(ContentType.objects.filter(app_label="qqbot", model="qqbinding").exists())
        self.assertEqual(ContentType.objects.filter(app_label="qqbot").count(), live)
        self.assertTrue(ContentType.objects.filter(pk=self.other.pk).exists())


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


class VersionDocsTests(SimpleTestCase):
    """Every install command, the guide's current version and the newest
    CHANGELOG entry name the version in ``qqbot.__version__``.

    每条安装命令、GUIDE 里的当前版本和 CHANGELOG 最新一段，写的都是
    ``qqbot.__version__`` 里的版本。
    """

    def test_install_commands_use_the_current_tag(self):
        git = re.compile(r"aa-qqbot-plugin\.git@v([0-9A-Za-z.]+)")
        zip_ = re.compile(r"aa-qqbot-plugin/archive/refs/tags/v([0-9A-Za-z.]+?)\.zip")
        for name in INSTALL_DOCS:
            text = read(name)
            with self.subTest(doc=name):
                tags = git.findall(text) + zip_.findall(text)
                self.assertTrue(tags)
                self.assertEqual(set(tags), {__version__})

    def test_guide_names_the_current_version(self):
        self.assertIn(f"当前版本：**{__version__}**", read("docs/GUIDE.md"))
        self.assertIn(f"Current version: **{__version__}**", read("docs/GUIDE.en.md"))
        for name in GUIDES:
            with self.subTest(doc=name):
                self.assertIn(f"Successfully installed aa-qqbot-{__version__}", read(name))

    def test_changelog_starts_with_the_current_version(self):
        first = re.search(r"^## \[([^\]]+)\]", read("CHANGELOG.md"), re.MULTILINE)
        self.assertEqual(first.group(1), __version__)


class SystemChecksDocsTests(SimpleTestCase):
    """Every system check code is explained in the guide and the spec.

    每个系统检查代码，GUIDE 和 SPEC 里都有说明。
    """

    def test_every_check_code_is_documented(self):
        codes = [
            value
            for name, value in vars(checks).items()
            if re.fullmatch(r"[EW]\d{3}", name) and isinstance(value, str)
        ]
        self.assertIn(checks.W003, codes)
        for name in (*GUIDES, "docs/SPEC.md"):
            text = read(name)
            for code in codes:
                with self.subTest(doc=name, code=code):
                    self.assertIn(f"`{code}`", text)


class DataProtectionDocsTests(TestCase):
    """The data protection sections name the optional setting and the options
    of ``qqbot_erase``, and the example commands in them run.

    数据保护一节写了可选设置和 ``qqbot_erase`` 的选项，而且里面的示例命令能运行。
    """

    ERASE_RE = re.compile(r"^\s*python manage\.py qqbot_erase (.+)$", re.MULTILINE)

    def test_docs_name_the_setting_and_the_check(self):
        for name in DATA_DOCS:
            with self.subTest(doc=name):
                self.assertIn("QQBOT_AUDIT_RETENTION_DAYS", read(name))
                self.assertIn("qqbot.W003", read(name))
        self.assertIn("QQBOT_AUDIT_RETENTION_DAYS", read("docs/SPEC.md"))

    def test_docs_name_every_option_of_the_command(self):
        parser = load_command_class("qqbot", "qqbot_erase").create_parser("manage.py", "qqbot_erase")
        # Only the options this command adds, not Django's own (--settings ...).
        # 只看这个命令自己加的选项，不看 Django 自带的（--settings 等）。
        own = {
            option
            for action in parser._actions
            if action.dest not in DJANGO_OPTION_DESTS
            for option in action.option_strings
        }
        self.assertEqual(own, {"--qq", "--user", "--dry-run", "--noinput", "--no-input"})
        for name in DATA_DOCS:
            text = read(name)
            for option in ("--qq", "--user", "--dry-run", "--noinput"):
                with self.subTest(doc=name, option=option):
                    self.assertIn(option, text)

    def test_example_commands_run_and_touch_only_that_person(self):
        examples = [
            (name, args)
            for name in DATA_DOCS
            for args in self.ERASE_RE.findall(read(name))
        ]
        self.assertTrue(examples)
        for name, args in examples:
            qq = re.search(r"--qq (\d+)", args).group(1)
            with self.subTest(doc=name, args=args):
                mine = audit.log(AuditLog.Action.GROUP, qq=qq, op="example")
                other = audit.log(AuditLog.Action.GROUP, qq="87654321", op="example")
                call_command(
                    "qqbot_erase", *shlex.split(args), "--noinput", stdout=io.StringIO()
                )
                mine.refresh_from_db()
                other.refresh_from_db()
                if "--dry-run" in args:
                    self.assertEqual(mine.qq, qq)
                else:
                    self.assertEqual(mine.qq, "")
                    self.assertEqual(mine.detail, {"erased": True})
                self.assertEqual(other.qq, "87654321")
                self.assertEqual(other.detail, {"op": "example"})


class GuideTranslationTests(SimpleTestCase):
    """The English guide has the same outline as the Chinese one, keeps every
    command that has no Chinese in it word for word, and the guides and the
    READMEs link to each other.

    英文版说明和中文版的结构一致，凡是不含中文的命令都逐字相同，
    两份说明和两份 README 之间互相有链接。
    """

    FENCE_RE = re.compile(r"^[ ]*```[a-z]*\n(?P<body>.*?)^[ ]*```[ ]*\n", re.MULTILINE | re.DOTALL)
    HEADING_RE = re.compile(r"^(#+) ", re.MULTILINE)

    def test_same_outline_and_same_commands(self):
        zh, en = read("docs/GUIDE.md"), read("docs/GUIDE.en.md")
        zh_blocks, en_blocks = self.FENCE_RE.findall(zh), self.FENCE_RE.findall(en)
        self.assertTrue(zh_blocks)
        self.assertEqual(len(zh_blocks), len(en_blocks))
        # Headings outside the code blocks (comments in a block also start with #).
        # 代码块之外的标题（代码块里的注释也以 # 开头）。
        self.assertEqual(
            self.HEADING_RE.findall(self.FENCE_RE.sub("", zh)),
            self.HEADING_RE.findall(self.FENCE_RE.sub("", en)),
        )
        # A block with Chinese comments or placeholders is translated; the others must match.
        # 带中文注释或占位符的代码块是翻译过的；其余的必须一模一样。
        pure = [block for block in zh_blocks if block.isascii()]
        self.assertTrue(pure)
        for block in pure:
            with self.subTest(block=block.splitlines()[0]):
                self.assertIn(block, en)

    def test_the_guides_and_readmes_link_to_each_other(self):
        self.assertIn("(GUIDE.en.md)", read("docs/GUIDE.md"))
        self.assertIn("(GUIDE.md)", read("docs/GUIDE.en.md"))
        for name in ("README.md", "README.zh-CN.md"):
            with self.subTest(doc=name):
                self.assertIn("(docs/GUIDE.en.md)", read(name))
                self.assertIn("(docs/GUIDE.md)", read(name))
