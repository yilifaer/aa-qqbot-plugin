"""Request signing for the bot API.

Signature (lower-case hex)::

    HMAC-SHA256(secret, "POST\\n" + path + "\\n" + timestamp + "\\n"
                        + nonce + "\\n" + hex(sha256(raw_body)))

This module holds the pure crypto helpers plus the header / skew / nonce /
rate-limit checks. The order of the checks is fixed by docs/SPEC.md section 4
and documented in API.md; :func:`authenticate` implements it.
"""

import hashlib
import hmac
import re
import time

from django.core.cache import cache

from .. import app_settings

HEADER_KEY = "X-QQBot-Key"
HEADER_TIMESTAMP = "X-QQBot-Timestamp"
HEADER_NONCE = "X-QQBot-Nonce"
HEADER_SIGNATURE = "X-QQBot-Signature"

KEY_ID_RE = re.compile(r"^[\x21-\x7e]{1,64}$")  # printable ASCII, no spaces
# Up to 16 digits so that a millisecond timestamp (13 digits, a common bug)
# passes the format check and is reported as ``stale_timestamp``, whose
# message says the unit must be seconds.
TIMESTAMP_RE = re.compile(r"^[0-9]{1,16}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")

NONCE_CACHE_PREFIX = "qqbot:nonce:"
RATE_CACHE_PREFIX = "qqbot:rate:"


class ApiError(Exception):
    """An error answered as ``{"ok": false, "error": code, "message": ...}``."""

    def __init__(self, status: int, code: str, message: str, headers=None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


# Chinese messages that tell the operator what to do. Kept in one place so
# API.md and the code agree.
MESSAGES = {
    "method_not_allowed": "这个接口只接受 POST 请求。请检查机器人插件的请求方法。",
    "misconfigured": (
        "AA 上还没有配置机器人通信密钥（QQBOT_API_KEYS）。请联系 IT 在 local.py 中配置后重启 AA。"
        "在修好之前，机器人应把所有结果当作「无法判断」，不要处置任何人。"
    ),
    "missing_headers": (
        "缺少签名请求头，或格式不正确（需要 X-QQBot-Key、X-QQBot-Timestamp、X-QQBot-Nonce、"
        "X-QQBot-Signature）。请对照 API.md 检查机器人插件的签名代码。"
    ),
    "unknown_key": (
        "密钥编号不存在。请确认机器人配置的密钥编号与 AA 的 QQBOT_API_KEYS 一致"
        "（换密钥时两边都要改）。"
    ),
    "stale_timestamp": (
        "请求的时间戳与 AA 服务器时间相差太大。请确认时间戳单位是秒（不是毫秒），"
        "并校准机器人所在电脑的时间（开启自动对时 / NTP）。"
    ),
    "bad_signature": (
        "签名不正确。请确认密钥与 AA 上配置的一致，并用 API.md 里的签名测试样例核对签名算法；"
        "签名用的路径和请求体必须与实际发送的完全一致。"
    ),
    "too_large": "请求体太大。请减少一次发送的内容（check 每次最多 3000 个 QQ）。",
    "replayed_nonce": (
        "这个随机数（nonce）已经用过了。每个请求都要生成新的随机数；重试时也要重新生成并重新签名。"
    ),
    "rate_limited": "请求太频繁，请稍后再试。请按 API.md 里建议的间隔调用。",
    "bad_request": "请求内容不正确。",
    "unknown_group": (
        "这个群不在 AA 的受管群列表里，或者已被停用。请先调用 groups 接口刷新群列表。"
    ),
    "internal_error": (
        "AA 内部出错，已记入日志。请联系 IT 查看 AA 日志。机器人应把结果当作「无法判断」，稍后再试。"
    ),
}


def error(status: int, code: str, message: str | None = None, headers=None) -> ApiError:
    return ApiError(status, code, message or MESSAGES[code], headers)


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def string_to_sign(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, str(timestamp), nonce, body_hash(body)])


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Lower-case hex HMAC-SHA256 signature of a request."""
    return hmac.new(
        secret.encode("utf-8"),
        string_to_sign(method, path, timestamp, nonce, body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signed_headers(key_id: str, secret: str, path: str, body: bytes, *, timestamp=None,
                   nonce: str) -> dict:
    """The four request headers for ``body`` (used by tests and tooling)."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        HEADER_KEY: key_id,
        HEADER_TIMESTAMP: ts,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: sign(secret, "POST", path, ts, nonce, body),
    }


def configured_keys() -> dict | None:
    """``{key_id: secret}`` with usable string secrets, or None when the
    setting is missing, empty or not a dict."""
    keys = app_settings.api_keys()
    if not isinstance(keys, dict) or not keys:
        return None
    usable = {str(k): v for k, v in keys.items() if isinstance(v, str) and v}
    return usable or None


# --------------------------------------------------------------------------
# request authentication
# --------------------------------------------------------------------------


def _header(request, name: str) -> str:
    return request.headers.get(name, "") or ""


def authenticate(request, body: bytes, now: float | None = None) -> str:
    """Check the request's signature and limits; return the key id.

    Raises :class:`ApiError` in the order given by docs/SPEC.md section 4.
    The nonce is only recorded once the signature is known to be good, so
    unauthenticated requests cannot burn nonces or rate-limit budget.
    """
    keys = configured_keys()
    if keys is None:
        raise error(503, "misconfigured")

    key_id = _header(request, HEADER_KEY)
    ts = _header(request, HEADER_TIMESTAMP)
    nonce = _header(request, HEADER_NONCE)
    signature = _header(request, HEADER_SIGNATURE)
    if not (
        KEY_ID_RE.fullmatch(key_id)
        and TIMESTAMP_RE.fullmatch(ts)
        and NONCE_RE.fullmatch(nonce)
        and SIGNATURE_RE.fullmatch(signature)
    ):
        raise error(401, "missing_headers")

    secret = keys.get(key_id)
    if secret is None:
        raise error(401, "unknown_key")

    skew = app_settings.api_max_skew()
    now = time.time() if now is None else now
    if abs(now - int(ts)) > skew:
        raise error(401, "stale_timestamp")

    expected = sign(secret, request.method, request.path, ts, nonce, body)
    if not hmac.compare_digest(expected, signature):
        raise error(401, "bad_signature")

    if len(body) > app_settings.api_max_body():
        raise error(413, "too_large")

    if not cache.add(f"{NONCE_CACHE_PREFIX}{key_id}:{nonce}", 1, 2 * skew + 60):
        raise error(401, "replayed_nonce")

    check_rate_limit(key_id, now)
    return key_id


def check_rate_limit(key_id: str, now: float | None = None) -> None:
    """Fixed one-minute window per key id; raises 429 ``rate_limited``."""
    limit = app_settings.api_rate_limit()
    if limit <= 0:
        return
    now = time.time() if now is None else now
    window = int(now // 60)
    cache_key = f"{RATE_CACHE_PREFIX}{key_id}:{window}"
    cache.add(cache_key, 0, 120)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add() and incr()
        cache.set(cache_key, 1, 120)
        count = 1
    if count > limit:
        retry_after = max(1, 60 - int(now % 60))
        raise error(429, "rate_limited", headers={"Retry-After": str(retry_after)})
