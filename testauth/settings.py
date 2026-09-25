"""Settings for running the test suite (not for production)."""

import os

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "test.sqlite3"),
        "TEST": {"NAME": ":memory:"},
    }
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
