"""Configuration checks (system checks + the pure ``problems()``)."""

from django.core import checks as django_checks
from django.test import SimpleTestCase, override_settings

from .. import checks

GOOD_KEYS = {"bot": "s" * 48}


# The test settings' cache is django_redis (a shared cache).
@override_settings(APPS_WITH_PUBLIC_VIEWS=["other", "qqbot"], QQBOT_API_KEYS=GOOD_KEYS)
class ProblemsTests(SimpleTestCase):
    def ids(self):
        return [m.id for m in checks.qqbot_config_check()]

    def test_all_good(self):
        self.assertEqual(checks.problems(), [])
        self.assertEqual(self.ids(), [])

    def test_e001_missing_public_views(self):
        for value in ([], ["other"], None, "qqbot"):
            with self.subTest(value=value), override_settings(APPS_WITH_PUBLIC_VIEWS=value):
                self.assertEqual(checks.problems(), ["qqbot.E001"])
                self.assertEqual(self.ids(), ["qqbot.E001"])
                self.assertIn("+=", checks.qqbot_config_check()[0].hint)

    def test_e002_no_keys(self):
        for value in ({}, None, [("bot", "s" * 48)], "secret"):
            with self.subTest(value=value), override_settings(QQBOT_API_KEYS=value):
                self.assertEqual(checks.problems(), ["qqbot.E002"])
                self.assertEqual(self.ids(), ["qqbot.E002"])

    def test_e002_setting_absent(self):
        from django.conf import settings

        with override_settings():
            del settings.QQBOT_API_KEYS
            self.assertEqual(checks.problems(), ["qqbot.E002"])

    def test_e003_short_secret(self):
        short = "s" * (checks.app_settings.QQBOT_MIN_SECRET_LENGTH - 1)
        for value in ({"bot": short}, {"a": "s" * 48, "b": short}, {"bot": None}):
            with self.subTest(value=value), override_settings(QQBOT_API_KEYS=value):
                self.assertEqual(checks.problems(), ["qqbot.E003"])
                self.assertEqual(self.ids(), ["qqbot.E003"])
        exact = {"bot": "s" * checks.app_settings.QQBOT_MIN_SECRET_LENGTH}
        with override_settings(QQBOT_API_KEYS=exact):
            self.assertEqual(checks.problems(), [])

    def test_e004_unusable_key_id(self):
        # Key ids the bot cannot send in X-QQBot-Key (every request would get
        # missing_headers) must be reported by `manage.py check`.
        for key_id in ("koishi bot", "机器人1", "k" * 65, "", "a\n"):
            value = {key_id: "s" * 48}
            with self.subTest(key_id=key_id), override_settings(QQBOT_API_KEYS=value):
                self.assertEqual(checks.problems(), ["qqbot.E004"])
                self.assertEqual(self.ids(), ["qqbot.E004"])
        for key_id in ("koishi-1", "k" * 64, "bot.2026_a"):
            with self.subTest(key_id=key_id), override_settings(
                QQBOT_API_KEYS={key_id: "s" * 48}
            ):
                self.assertEqual(checks.problems(), [])
        with override_settings(QQBOT_API_KEYS={"a b": "short"}):
            self.assertEqual(checks.problems(), ["qqbot.E003", "qqbot.E004"])

    def test_w001_unshared_cache(self):
        for backend in (
            "django.core.cache.backends.locmem.LocMemCache",
            "django.core.cache.backends.dummy.DummyCache",
            "django.core.cache.backends.filebased.FileBasedCache",
        ):
            caches = {"default": {"BACKEND": backend, "LOCATION": "/tmp/x"}}
            with self.subTest(backend=backend), override_settings(CACHES=caches):
                self.assertEqual(checks.problems(), ["qqbot.W001"])
                [msg] = checks.qqbot_config_check()
                self.assertEqual(msg.id, "qqbot.W001")
                self.assertEqual(msg.level, django_checks.WARNING)

    def test_shared_caches_ok(self):
        for backend in (
            "django.core.cache.backends.redis.RedisCache",
            "django.core.cache.backends.memcached.PyMemcacheCache",
            "django.core.cache.backends.db.DatabaseCache",
        ):
            with self.subTest(backend=backend), override_settings(
                CACHES={"default": {"BACKEND": backend}}
            ):
                self.assertEqual(checks.problems(), [])

    def test_errors_are_errors(self):
        with override_settings(APPS_WITH_PUBLIC_VIEWS=[], QQBOT_API_KEYS={}):
            msgs = checks.qqbot_config_check()
        self.assertEqual([m.id for m in msgs], ["qqbot.E001", "qqbot.E002"])
        self.assertTrue(all(m.level == django_checks.ERROR for m in msgs))

    def test_registered_with_django(self):
        with override_settings(QQBOT_API_KEYS={}):
            found = django_checks.run_checks()
        self.assertIn("qqbot.E002", [m.id for m in found])



class TestSettingsTests(SimpleTestCase):
    def test_test_settings_are_clean(self):
        # testauth/settings.py is a valid configuration.
        self.assertEqual(checks.problems(), [])
