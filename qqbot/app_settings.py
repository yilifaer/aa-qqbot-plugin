"""Settings read from the site's local.py.

Only things that must NOT be editable from the web UI live here (secrets and
transport limits). Everything else is stored in :class:`qqbot.models.Config`
and edited by QQ managers on the front-end manage pages.
"""

from django.conf import settings

# Secrets shorter than this are rejected by the system check.
QQBOT_MIN_SECRET_LENGTH = 32

# All values are read at call time so tests can override them.
# QQBOT_API_KEYS: {key_id: secret}; several keys may be active for rotation.
# QQBOT_API_MAX_SKEW: accepted clock skew in seconds (both directions).
# QQBOT_API_MAX_BODY: max request body in bytes.
# QQBOT_API_RATE_LIMIT: requests per minute per key.


def api_keys() -> dict:
    return getattr(settings, "QQBOT_API_KEYS", {}) or {}


def api_max_skew() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_SKEW", 300))


def api_max_body() -> int:
    return int(getattr(settings, "QQBOT_API_MAX_BODY", 256 * 1024))


def api_rate_limit() -> int:
    return int(getattr(settings, "QQBOT_API_RATE_LIMIT", 120))
