"""Settings read from the site's local.py.

Only things that must NOT be editable from the web UI live here (secrets,
transport limits and the audit log retention). Everything else is stored in
:class:`qqbot.models.Config` and edited by QQ managers on the front-end
manage pages.

从站点的 local.py 读取的设置。

这里只放“不能”在网页上修改的内容（密钥、传输限制和审计日志的保留天数）。
其他设置都存在 ``qqbot.models.Config`` 里，由 QQ 管理员在前台管理页面上修改。
"""

from django.conf import settings

# Secrets shorter than this are rejected by the system check.
# 比这个长度短的密钥会被系统检查判为错误。
QQBOT_MIN_SECRET_LENGTH = 32

# Audit log entries are kept at least this many days when a retention is set:
# the rebind cooldown (at most 720 hours = 30 days) reads the audit log.
# 设置了审计日志保留天数时，至少保留这么多天：换绑冷却（最长 720 小时 =
# 30 天）要读取审计日志。
QQBOT_AUDIT_RETENTION_MIN_DAYS = 30

# All values are read at call time so tests can override them.
# QQBOT_API_KEYS: {key_id: secret}; several keys may be active for rotation.
# QQBOT_API_MAX_SKEW: accepted clock skew in seconds (both directions).
# QQBOT_API_MAX_BODY: max request body in bytes.
# QQBOT_API_RATE_LIMIT: requests per minute per key.
# QQBOT_AUDIT_RETENTION_DAYS: the daily reconcile deletes audit log entries
# older than this many days; 0 (the default) keeps them forever.
# 所有值都在调用时才读取，方便测试里覆盖。
# QQBOT_API_KEYS：{密钥编号: 密钥}；可以同时启用多个密钥，方便轮换。
# QQBOT_API_MAX_SKEW：允许的时钟误差，单位秒（快、慢两个方向都算）。
# QQBOT_API_MAX_BODY：请求体的最大字节数。
# QQBOT_API_RATE_LIMIT：每个密钥每分钟允许的请求数。
# QQBOT_AUDIT_RETENTION_DAYS：每日对账时删除早于这么多天的审计日志；
# 0（默认）表示永久保留。


def api_keys() -> dict:
    return getattr(settings, "QQBOT_API_KEYS", {}) or {}


def api_max_skew() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_SKEW", 300))


def api_max_body() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_BODY", 256 * 1024))


def api_rate_limit() -> int:
    return int(getattr(settings, "QQBOT_API_RATE_LIMIT", 120))


def audit_retention_days() -> int:
    """Days to keep audit log entries; 0 means keep them forever.

    Values 1-29 are raised to ``QQBOT_AUDIT_RETENTION_MIN_DAYS``. A value
    that is not an int >= 0 is ignored (0); the system check ``qqbot.W003``
    reports both cases.

    审计日志保留的天数；0 表示永久保留。

    1–29 会被提高到 ``QQBOT_AUDIT_RETENTION_MIN_DAYS``。不是 ≥ 0 的整数时
    忽略这个设置（按 0 处理）；这两种情况系统检查 ``qqbot.W003`` 都会报告。
    """
    value = getattr(settings, "QQBOT_AUDIT_RETENTION_DAYS", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return max(value, QQBOT_AUDIT_RETENTION_MIN_DAYS)
