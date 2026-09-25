from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import Permission
from django.test import TestCase

mig = import_module("qqbot.migrations.0002_bilingual_permission_names")


class BilingualPermissionNamesTests(TestCase):
    def _perm(self, codename):
        return Permission.objects.get(content_type__app_label="qqbot", codename=codename)

    def test_model_names_match_migration(self):
        for codename, name in mig.NEW_NAMES.items():
            self.assertEqual(self._perm(codename).name, name)

    def test_forwards_renames_existing_rows(self):
        # A site that ran 1.0.0b1/b2 still has the old Chinese-only names.
        for codename, name in mig.OLD_NAMES.items():
            Permission.objects.filter(pk=self._perm(codename).pk).update(name=name)
        mig.forwards(apps, None)
        for codename, name in mig.NEW_NAMES.items():
            self.assertEqual(self._perm(codename).name, name)
            self.assertTrue(name.startswith("QQ binding: "))

    def test_backwards_restores_old_names(self):
        mig.backwards(apps, None)
        for codename, name in mig.OLD_NAMES.items():
            self.assertEqual(self._perm(codename).name, name)
