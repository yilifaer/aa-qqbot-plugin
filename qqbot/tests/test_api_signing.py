"""Signature helpers and the API.md test vectors."""

import hashlib
import secrets
import time

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from ..api import signing

# Must match the "签名测试样例" section of API.md exactly.
VECTOR_SECRET = "koishi-test-vector-secret-0123456789ABCDEFGHIJ"
VECTOR_KEY = "koishi-1"
VECTOR_TIMESTAMP = "1767225600"

VECTOR_1 = {
    "path": "/qqbot/api/v1/check/",
    "nonce": "Zq3vN8xK2mP5tR7w",
    "body": b'{"group_id":"123456789","qqs":["10001","20002"],"full_roster":false}',
    "body_sha256": "e5eaf15236bbfb41e46f96a6413ab601de021f05391bb63bbd331da3057315cd",
    "signature": "4babcb45e2477890050b37e838f2efc239b550fccae9c8b5cb7127ddf36767e4",
}
VECTOR_2 = {
    "path": "/qqbot/api/v1/claim/",
    "nonce": "Nonce_For-Vector-2",
    "body": '{"qq":"10001","text":"我是凯拉 QQ-ABC234"}'.encode(),
    "body_hex": (
        "7b227171223a223130303031222c2274657874223a22e68891e698afe587afe68b89"
        "2051512d414243323334227d"
    ),
    "body_sha256": "0420addccba85a2add4a1260989df656a91b169a103a3ce56dc05e6c5e1e6e68",
    "signature": "8ac11ec05b15b17b7449676b2f2bb3c7395bd2578ac396389e4af3854af11be2",
}
EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class SignatureVectorTests(SimpleTestCase):
    def test_vector_1_string_to_sign(self):
        v = VECTOR_1
        self.assertEqual(signing.body_hash(v["body"]), v["body_sha256"])
        self.assertEqual(
            signing.string_to_sign("POST", v["path"], VECTOR_TIMESTAMP, v["nonce"], v["body"]),
            "POST\n/qqbot/api/v1/check/\n1767225600\nZq3vN8xK2mP5tR7w\n" + v["body_sha256"],
        )

    def test_vector_1_signature(self):
        v = VECTOR_1
        self.assertEqual(
            signing.sign(VECTOR_SECRET, "POST", v["path"], VECTOR_TIMESTAMP, v["nonce"], v["body"]),
            v["signature"],
        )

    def test_vector_2_utf8_body(self):
        v = VECTOR_2
        self.assertEqual(v["body"].hex(), v["body_hex"])
        self.assertEqual(signing.body_hash(v["body"]), v["body_sha256"])
        self.assertEqual(
            signing.sign(VECTOR_SECRET, "POST", v["path"], VECTOR_TIMESTAMP, v["nonce"], v["body"]),
            v["signature"],
        )

    def test_empty_body_hash(self):
        self.assertEqual(signing.body_hash(b""), EMPTY_BODY_SHA256)
        self.assertEqual(hashlib.sha256(b"").hexdigest(), EMPTY_BODY_SHA256)

    def test_signature_is_lower_hex_64(self):
        sig = signing.sign("s" * 32, "POST", "/p/", "1", "n" * 16, b"{}")
        self.assertRegex(sig, r"^[0-9a-f]{64}$")

    def test_every_part_matters(self):
        base = ("s" * 32, "POST", "/qqbot/api/v1/check/", "100", "n" * 16, b"{}")
        sig = signing.sign(*base)
        for i, changed in enumerate(
            ["t" * 32, "GET", "/qqbot/api/v1/claim/", "101", "m" * 16, b"{ }"]
        ):
            args = list(base)
            args[i] = changed
            self.assertNotEqual(signing.sign(*args), sig, i)

    def test_signed_headers(self):
        h = signing.signed_headers(
            VECTOR_KEY, VECTOR_SECRET, VECTOR_1["path"], VECTOR_1["body"],
            timestamp=VECTOR_TIMESTAMP, nonce=VECTOR_1["nonce"],
        )
        self.assertEqual(
            h,
            {
                "X-QQBot-Key": VECTOR_KEY,
                "X-QQBot-Timestamp": VECTOR_TIMESTAMP,
                "X-QQBot-Nonce": VECTOR_1["nonce"],
                "X-QQBot-Signature": VECTOR_1["signature"],
            },
        )


class ConfiguredKeysTests(SimpleTestCase):
    @override_settings(QQBOT_API_KEYS={})
    def test_empty(self):
        self.assertIsNone(signing.configured_keys())

    @override_settings(QQBOT_API_KEYS=["a", "b"])
    def test_not_a_dict(self):
        self.assertIsNone(signing.configured_keys())

    @override_settings(QQBOT_API_KEYS={"a": "", "b": None})
    def test_no_usable_secret(self):
        self.assertIsNone(signing.configured_keys())

    @override_settings(QQBOT_API_KEYS={"a": "x" * 32, "b": None})
    def test_skips_unusable(self):
        self.assertEqual(signing.configured_keys(), {"a": "x" * 32})


@override_settings(
    QQBOT_API_KEYS={"k1": "a" * 40},
    QQBOT_API_MAX_SKEW=300,
    QQBOT_API_RATE_LIMIT=100000,
)
class AuthenticateUnitTests(TestCase):
    """``authenticate`` on its own (the view tests cover the HTTP side)."""

    path = "/qqbot/api/v1/health/"

    def request(self, body=b"{}", *, now=None, nonce=None, **header_overrides):
        now = time.time() if now is None else now
        headers = signing.signed_headers(
            "k1", "a" * 40, self.path, body, timestamp=int(now),
            nonce=nonce or secrets.token_hex(12),
        )
        headers.update(header_overrides)
        meta = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items()}
        return RequestFactory().post(self.path, data=body, content_type="application/json", **meta)

    def assertCode(self, code, request, body=b"{}", now=None):
        with self.assertRaises(signing.ApiError) as cm:
            signing.authenticate(request, body, now=now)
        self.assertEqual(cm.exception.code, code)
        return cm.exception

    def test_ok(self):
        self.assertEqual(signing.authenticate(self.request(), b"{}"), "k1")

    def test_skew_boundary(self):
        now = time.time()
        req = self.request(now=now - 300)
        self.assertEqual(signing.authenticate(req, b"{}", now=int(now)), "k1")
        self.assertCode("stale_timestamp", self.request(now=now - 302), now=now)
        self.assertCode("stale_timestamp", self.request(now=now + 302), now=now)

    def test_nonce_not_recorded_before_signature(self):
        nonce = "same-nonce-" + secrets.token_hex(8)
        bad = self.request(nonce=nonce, **{"X-QQBot-Signature": "0" * 64})
        self.assertCode("bad_signature", bad)
        self.assertEqual(signing.authenticate(self.request(nonce=nonce), b"{}"), "k1")
        self.assertCode("replayed_nonce", self.request(nonce=nonce))

    def test_nonce_cache_key_and_ttl(self):
        nonce = "ttl-nonce-" + secrets.token_hex(8)
        signing.authenticate(self.request(nonce=nonce), b"{}")
        self.assertIsNotNone(cache.get(f"qqbot:nonce:k1:{nonce}"))
        ttl = cache.ttl(f"qqbot:nonce:k1:{nonce}")  # django-redis
        self.assertTrue(600 < ttl <= 660, ttl)

    @override_settings(QQBOT_API_RATE_LIMIT=2)
    def test_rate_limit_window(self):
        key = "k1"
        now = 1_000_000 * 60 + 5.0  # a fixed window no other test uses
        cache.delete(f"qqbot:rate:{key}:{int(now // 60)}")
        signing.check_rate_limit(key, now)
        signing.check_rate_limit(key, now)
        with self.assertRaises(signing.ApiError) as cm:
            signing.check_rate_limit(key, now)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.headers["Retry-After"], "55")

    @override_settings(QQBOT_API_RATE_LIMIT=0)
    def test_rate_limit_disabled(self):
        for _ in range(3):
            signing.check_rate_limit("k1")
