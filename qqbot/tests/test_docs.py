"""Commands in the docs that change data: they must stay correct and touch
only qqbot's own rows.

文档里会改数据的命令：必须一直正确，而且只动 qqbot 自己的数据。
"""

import contextlib
import io
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

ROOT = Path(__file__).resolve().parents[2]

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
        for name in ("README.md", "README.zh-CN.md", "docs/GUIDE.md"):
            with self.subTest(doc=name):
                self.assertIn(UNINSTALL_CODE, (ROOT / name).read_text(encoding="utf-8"))
        self.assertIn(CLEANUP_0X_CODE, (ROOT / "docs/GUIDE.md").read_text(encoding="utf-8"))

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
