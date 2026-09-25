"""Configuration checks (docs/SPEC.md section 7).

:func:`problems` is the single source of truth: the Django system checks
below and the bot API's ``health`` endpoint both use it. Codes are the
system check ids (``qqbot.E001`` ...).

配置检查（见 docs/SPEC.md 第 7 节）。

``problems`` 是唯一的判断依据：下面的 Django 系统检查和机器人接口的
``health`` 接口都调用它。问题代码就是系统检查的 id（``qqbot.E001`` 等）。
"""

from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning

from . import app_settings

E001 = "qqbot.E001"
E002 = "qqbot.E002"
E003 = "qqbot.E003"
E004 = "qqbot.E004"
W001 = "qqbot.W001"
W002 = "qqbot.W002"

# The daily reconciliation task that local.py must schedule (README).
# local.py 里必须安排的每日对账任务（见 README）。
RECONCILE_TASK = "qqbot.tasks.reconcile"

# Cache backends that are not shared between processes (gunicorn workers),
# so the API's replay protection (nonces) and rate limits would not work.
# 不在多个进程（gunicorn worker）之间共享的缓存后端；用这些后端时，
# 接口的防重放（nonce）和限速都不起作用。
_UNSHARED_CACHE_MARKERS = ("locmem", "dummy", "filebased")


def _cache_backend() -> str:
    caches = getattr(settings, "CACHES", None) or {}
    default = caches.get("default") or {}
    return str(default.get("BACKEND", ""))


def _reconcile_scheduled() -> bool:
    """Whether ``CELERYBEAT_SCHEDULE`` has an entry for the daily reconcile.

    ``CELERYBEAT_SCHEDULE`` 里是否有每日对账任务。
    """
    schedule = getattr(settings, "CELERYBEAT_SCHEDULE", None)
    if not isinstance(schedule, dict):
        return False
    return any(
        isinstance(entry, dict) and entry.get("task") == RECONCILE_TASK
        for entry in schedule.values()
    )


def _usable_key_id(key_id) -> bool:
    """A key id the bot can actually send in ``X-QQBot-Key`` (same ``str()``
    conversion as ``signing.configured_keys``).

    这个密钥编号能否真正放进 ``X-QQBot-Key`` 请求头发送（与
    ``signing.configured_keys`` 一样，先用 ``str()`` 转换）。
    """
    from .api.signing import KEY_ID_RE

    return bool(KEY_ID_RE.fullmatch(str(key_id)))


def problems() -> list[str]:
    """Short codes of configuration problems, e.g. ``["qqbot.E002"]``.

    Pure: reads settings only, never raises.

    返回配置问题的简短代码，例如 ``["qqbot.E002"]``。

    纯函数：只读取设置，从不抛出异常。
    """
    found = []
    public = getattr(settings, "APPS_WITH_PUBLIC_VIEWS", None)
    if not isinstance(public, (list, tuple, set)) or "qqbot" not in public:
        found.append(E001)
    keys = getattr(settings, "QQBOT_API_KEYS", None)
    if not isinstance(keys, dict) or not keys:
        found.append(E002)
    else:
        if any(
            not isinstance(secret, str) or len(secret) < app_settings.QQBOT_MIN_SECRET_LENGTH
            for secret in keys.values()
        ):
            found.append(E003)
        if any(not _usable_key_id(key_id) for key_id in keys):
            found.append(E004)
    backend = _cache_backend().lower()
    if not backend or any(marker in backend for marker in _UNSHARED_CACHE_MARKERS):
        found.append(W001)
    if not _reconcile_scheduled():
        found.append(W002)
    return found


def _messages() -> dict:
    min_len = app_settings.QQBOT_MIN_SECRET_LENGTH
    return {
        E001: Error(
            "APPS_WITH_PUBLIC_VIEWS 里没有 \"qqbot\"，机器人接口会被重定向到登录页，机器人无法工作。",
            hint=(
                '在 local.py 里写 APPS_WITH_PUBLIC_VIEWS += ["qqbot"]。'
                "请用 += 追加，不要用 = 覆盖，否则别的插件的公开页面会失效。"
            ),
            id=E001,
        ),
        E002: Error(
            "QQBOT_API_KEYS 没有配置（或者不是字典），机器人接口会一直返回 503。",
            hint=(
                '在 local.py 里写 QQBOT_API_KEYS = {"koishi-1": "<密钥>"}。'
                '密钥这样生成：python -c "import secrets; print(secrets.token_urlsafe(48))"'
            ),
            id=E002,
        ),
        E003: Error(
            f"QQBOT_API_KEYS 里有密钥太短（少于 {min_len} 个字符）。",
            hint=(
                "请重新生成密钥：python -c \"import secrets; print(secrets.token_urlsafe(48))\"，"
                "然后在机器人那边同步修改。"
            ),
            id=E003,
        ),
        E004: Error(
            "QQBOT_API_KEYS 里有密钥编号（字典的键）格式不对，机器人用它发的请求会一直被拒绝（missing_headers）。",
            hint=(
                "密钥编号只能用英文字母、数字和 - _ . 等符号，不能有空格或中文，最长 64 个字符，"
                '例如 "koishi-1"。改好后在机器人那边同步修改。'
            ),
            id=E004,
        ),
        W001: CheckWarning(
            "默认缓存不是 Redis 这类多进程共享的缓存，机器人接口的防重放和限速在多个进程之间不起作用。",
            hint="Alliance Auth 默认使用 Redis 缓存，请检查 local.py 里是否覆盖了 CACHES。",
            id=W001,
        ),
        W002: CheckWarning(
            "CELERYBEAT_SCHEDULE 里没有每日对账任务 qqbot.tasks.reconcile，"
            "资格和群名片的定期复查、旧数据清理都不会自动运行。",
            hint=(
                'local.py 里加上 CELERYBEAT_SCHEDULE["qqbot_reconcile"] = '
                '{"task": "qqbot.tasks.reconcile", "schedule": crontab(minute="17", hour="4")}（见 README）。'
                "如果是在后台「Periodic tasks」里手动添加的，可以忽略这条提示。"
            ),
            id=W002,
        ),
    }


@register()
def qqbot_config_check(app_configs=None, **kwargs):
    found = problems()
    if not found:
        return []
    messages = _messages()
    return [messages[code] for code in found]
