# --- Example local settings (do NOT use in production as-is) / 示例本地配置 ---
from .base import *
import os

DEBUG = True

# Site URL (no trailing slash) / 站点 URL（末尾不要斜杠）
SITE_URL = "http://127.0.0.1:8000"
CSRF_TRUSTED_ORIGINS = [SITE_URL]

# !!! IMPORTANT: change this in your own copy / 重要：在自己的 local.py 里改成随机字符串
SECRET_KEY = "CHANGE_ME"

# App registration / 这里保持空即可（按需填写）
ESI_SSO_CLIENT_ID = ""
ESI_SSO_CLIENT_SECRET = ""
ESI_SSO_CALLBACK_URL = f"{SITE_URL}/sso/callback"
ESI_USER_CONTACT_EMAIL = ""

# Database (default: sqlite3) / 数据库（默认 sqlite3）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

# Enable qqbot app / 启用 qqbot 插件
INSTALLED_APPS += ["qqbot"]

# Optional: relax Redis in dev / 开发环境忽略 Redis 失败
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
        },
        "KEY_PREFIX": "aa_dev",
    }
}

# (Optional) mute auth notification app / 可选：临时关闭通知模块
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "allianceauth.notifications"]
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

# --- QQ bot settings (fill in your own) / QQ 插件配置（使用者填写） ---
QQBOT_GROUP_CHAT = "YOUR_QQ_GROUP_ID"      # ← 在自己的 local.py 里填群号
QQBOT_PING_GROUP = "YOUR_QQ_PING_GROUP_ID" # ← 在自己的 local.py 里填群号
