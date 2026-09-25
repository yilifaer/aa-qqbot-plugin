"""Settings read from the site's local.py.

Only things that must NOT be editable from the web UI live here (secrets and
transport limits). Everything else is stored in :class:`qqbot.models.Config`
and edited by QQ managers on the front-end manage pages.

从站点的 local.py 读取的设置。

这里只放“不能”在网页上修改的内容（密钥和传输限制）。其他设置都存在
``qqbot.models.Config`` 里，由 QQ 管理员在前台管理页面上修改。
"""

from django.conf import settings

# Secrets shorter than this are rejected by the system check.
# 比这个长度短的密钥会被系统检查判为错误。
QQBOT_MIN_SECRET_LENGTH = 32

# All values are read at call time so tests can override them.
# QQBOT_API_KEYS: {key_id: secret}; several keys may be active for rotation.
# QQBOT_API_MAX_SKEW: accepted clock skew in seconds (both directions).
# QQBOT_API_MAX_BODY: max request body in bytes.
# QQBOT_API_RATE_LIMIT: requests per minute per key.
# 所有值都在调用时才读取，方便测试里覆盖。
# QQBOT_API_KEYS：{密钥编号: 密钥}；可以同时启用多个密钥，方便轮换。
# QQBOT_API_MAX_SKEW：允许的时钟误差，单位秒（快、慢两个方向都算）。
# QQBOT_API_MAX_BODY：请求体的最大字节数。
# QQBOT_API_RATE_LIMIT：每个密钥每分钟允许的请求数。


def api_keys() -> dict:
    return getattr(settings, "QQBOT_API_KEYS", {}) or {}


def api_max_skew() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_SKEW", 300))


def api_max_body() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_BODY", 256 * 1024))


def api_rate_limit() -> int:
    return int(getattr(settings, "QQBOT_API_RATE_LIMIT", 120))
