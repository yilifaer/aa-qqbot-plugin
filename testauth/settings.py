"""Settings for running the test suite (not for production)."""

import os

from celery.schedules import crontab

from allianceauth.project_template.project_name.settings.base import *  # noqa: F401,F403

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "testauth-not-a-secret-" + "x" * 40
DEBUG = False
ROOT_URLCONF = "allianceauth.urls"
SITE_URL = "http://testserver"
CSRF_TRUSTED_ORIGINS = [SITE_URL]

INSTALLED_APPS += ["qqbot"]  # noqa: F405
APPS_WITH_PUBLIC_VIEWS = ["qqbot"]

QQBOT_API_KEYS = {"test": "test-secret-0123456789abcdefghijklmnopqrstuvwxyz"}

# Same entry as README's local.py block (checked by qqbot.W002).
CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {  # noqa: F405
    "task": "qqbot.tasks.reconcile",
    "schedule": crontab(minute="17", hour="4"),
}

# Database: SQLite by default. CI also runs the suite on MariaDB and
# PostgreSQL (QQBOT_TEST_DB=mysql|postgres) so that the row-locking tests in
# qqbot/tests/test_core_concurrency.py actually run. Connection details come
# from QQBOT_DB_HOST / QQBOT_DB_PORT / QQBOT_DB_USER / QQBOT_DB_PASSWORD /
# QQBOT_DB_NAME; the test runner creates and drops "test_<name>".
_TEST_DB = os.environ.get("QQBOT_TEST_DB", "sqlite").strip().lower()


def _server_db(engine, default_port, default_user, **extra):
    return {
        "ENGINE": engine,
        "NAME": os.environ.get("QQBOT_DB_NAME", "qqbot"),
        "USER": os.environ.get("QQBOT_DB_USER", default_user),
        "PASSWORD": os.environ.get("QQBOT_DB_PASSWORD", ""),
        "HOST": os.environ.get("QQBOT_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("QQBOT_DB_PORT", default_port),
        **extra,
    }


if _TEST_DB in ("mysql", "mariadb"):
    DATABASES = {
        "default": _server_db(
            "django.db.backends.mysql",
            "3306",
            "root",
            OPTIONS={"charset": "utf8mb4"},
            TEST={"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        )
    }
elif _TEST_DB in ("postgres", "postgresql"):
    DATABASES = {"default": _server_db("django.db.backends.postgresql", "5432", "postgres")}
elif _TEST_DB == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "test.sqlite3"),
            "TEST": {"NAME": ":memory:"},
        }
    }
else:
    raise ValueError(f"QQBOT_TEST_DB must be sqlite, mysql or postgres, not {_TEST_DB!r}")

# AA's manifest static storage needs `collectstatic`; tests render full AA
# pages (base-bs5.html) without it.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# AA itself needs Redis (django_redis). Tests use a separate Redis DB.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("QQBOT_TEST_REDIS", "redis://127.0.0.1:6379/15"),
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.db"

CELERY_ALWAYS_EAGER = True
CELERY_TASK_ALWAYS_EAGER = True
CELERY_EAGER_PROPAGATES_EXCEPTIONS = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

ESI_SSO_CLIENT_ID = "dummy"
ESI_SSO_CLIENT_SECRET = "dummy"
ESI_SSO_CALLBACK_URL = "http://testserver/sso/callback"
ESI_USER_CONTACT_EMAIL = "test@example.com"

LOGGING = None  # keep test output quiet and avoid writing log files
