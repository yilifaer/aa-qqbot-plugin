"""Bot API through the real AA URLconf (docs/SPEC.md section 4)."""

import inspect
import json
import secrets
import time
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, include, path, reverse
from django.utils import timezone

from allianceauth.authentication.decorators import decorate_url_patterns, main_character_required

from .. import __version__, auth_hooks
from ..api import signing
from ..api import urls as api_urls
from ..api import views
from ..core import bindings
from ..core import events as core_events
from ..models import BindCode, Event, QQGroup, RosterEntry
from .utils import add_to_groups, bind, create_group, create_member, put_in_roster

KEY = "test"
SECRET = "test-secret-0123456789abcdefghijklmnopqrstuvwxyz"
ENDPOINTS = ["health", "groups", "check", "claim", "events"]


def url(name):
    return f"/qqbot/api/v1/{name}/"


@override_settings(
    QQBOT_API_KEYS={KEY: SECRET},
    QQBOT_API_MAX_SKEW=300,
    QQBOT_API_RATE_LIMIT=100000,
)
class ApiTestCase(TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(views, "logger")
        self.logger = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, payload=None, *, key=KEY, secret=SECRET, ts=None, nonce=None,
             body=None, headers=None, path=None):
        if body is None:
            body = json.dumps({} if payload is None else payload).encode()
        path = path or url(name)
        h = signing.signed_headers(
            key, secret, path, body,
            timestamp=int(time.time()) if ts is None else ts,
            nonce=nonce or secrets.token_urlsafe(18),
        )
        h.update(headers or {})
        return self.client.post(path, data=body, content_type="application/json", headers=h)

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["error"], code)
        self.assertTrue(data["message"])
        return data

    def assertOk(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertIs(data["ok"], True)
        self.assertIn("server_time", data)
        return data


# --------------------------------------------------------------------------
# routing / public-view isolation
# --------------------------------------------------------------------------


class RoutingTests(ApiTestCase):
    def test_reverse(self):
        for name in ENDPOINTS:
            self.assertEqual(reverse(f"qqbot:api_{name}"), url(name))

    def test_lookup_str_matches_public_views(self):
        self.assertEqual(
            [p.lookup_str for p in api_urls.urlpatterns],
            [f"qqbot.api.views.{name}" for name in ENDPOINTS],
        )
        self.assertEqual(
            sorted(p.lookup_str for p in api_urls.urlpatterns), sorted(auth_hooks.PUBLIC_VIEWS)
        )

    def test_resolved_through_aa_urlconf_undecorated(self):
        """In AA's URLconf the API views are the plain (not login-wrapped)
        functions, and they resolve to the PUBLIC_VIEWS names."""
        found = {}

        def walk(patterns):
            for p in patterns:
                if isinstance(p, URLResolver):
                    walk(p.url_patterns)
                elif isinstance(p, URLPattern) and p.lookup_str.startswith("qqbot.api."):
                    found[p.lookup_str] = p.callback

        walk(get_resolver().url_patterns)
        self.assertEqual(sorted(found), sorted(auth_hooks.PUBLIC_VIEWS))
        for name in ENDPOINTS:
            self.assertIs(found[f"qqbot.api.views.{name}"], getattr(views, name))

    def test_anonymous_unsigned_post_is_401_json_not_redirect(self):
        for name in ENDPOINTS:
            r = self.client.post(url(name), data=b"{}", content_type="application/json")
            self.assertError(r, 401, "missing_headers")
        self.assertEqual(self.logger.warning.call_count, len(ENDPOINTS))

    def test_get_is_405_json(self):
        for name in ENDPOINTS:
            r = self.client.get(url(name))
            self.assertError(r, 405, "method_not_allowed")
            self.assertEqual(r["Allow"], "POST")

    def test_other_methods_405(self):
        self.assertError(self.client.put(url("health")), 405, "method_not_allowed")
        self.assertError(self.client.delete(url("health")), 405, "method_not_allowed")

    def test_csrf_exempt(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        body = b"{}"
        h = signing.signed_headers(KEY, SECRET, url("health"), body, nonce=secrets.token_hex(12))
        r = client.post(url("health"), data=body, content_type="application/json", headers=h)
        self.assertEqual(r.status_code, 200, r.content)

    def test_nested_include_isolation(self):
        """AA stops decorating a pattern list at its first excluded view. With
        the API in its own nested include, routes before *and after* it are
        still wrapped with the login decorator."""

        def member_view(request):
            pass

        def after_view(request):
            pass

        patterns = [
            path("", include([path("", member_view, name="m")])),
            path("api/v1/", include([
                path(f"{name}/", getattr(views, name)) for name in ENDPOINTS
            ])),
            path("zzz/", include([path("", after_view, name="z")])),
        ]
        decorate_url_patterns(patterns, main_character_required, auth_hooks.PUBLIC_VIEWS)
        member, api, after = (p.url_patterns for p in patterns)
        self.assertIsNot(member[0].callback, member_view)
        self.assertIs(inspect.unwrap(member[0].callback), member_view)
        self.assertIsNot(after[0].callback, after_view)
        for p, name in zip(api, ENDPOINTS):
            self.assertIs(p.callback, getattr(views, name))

    def test_qqbot_non_api_urls_redirect_anonymous_to_login(self):
        """Every non-API qqbot route in the real URLconf is login-protected."""
        from .. import urls as qqbot_urls

        checked = []

        def walk(patterns, prefix):
            for p in patterns:
                if isinstance(p, URLResolver):
                    walk(p.url_patterns, prefix + str(p.pattern))
                    continue
                if p.lookup_str.startswith("qqbot.api.") or not p.name:
                    continue
                kwargs = {}
                for arg, conv in p.pattern.converters.items():
                    kwargs[arg] = 1 if conv.regex == "[0-9]+" else "x"
                checked.append(reverse(f"qqbot:{p.name}", kwargs=kwargs))

        walk(qqbot_urls.urlpatterns, "")
        if not checked:
            self.skipTest("no member/manage routes registered yet")
        for u in checked:
            for method in (self.client.get, self.client.post):
                r = method(u)
                self.assertEqual(r.status_code, 302, u)
                self.assertIn("login", r["Location"], u)


# --------------------------------------------------------------------------
# authentication failures
# --------------------------------------------------------------------------


class AuthTests(ApiTestCase):
    def test_happy(self):
        self.assertOk(self.call("health"))

    @override_settings(QQBOT_API_KEYS={})
    def test_misconfigured_empty(self):
        self.assertError(self.call("health"), 503, "misconfigured")

    @override_settings(QQBOT_API_KEYS=None)
    def test_misconfigured_none(self):
        self.assertError(self.call("health"), 503, "misconfigured")

    @override_settings(QQBOT_API_KEYS=[("test", SECRET)])
    def test_misconfigured_not_dict(self):
        self.assertError(self.call("health"), 503, "misconfigured")

    def test_missing_each_header(self):
        for header in ("X-QQBot-Key", "X-QQBot-Timestamp", "X-QQBot-Nonce", "X-QQBot-Signature"):
            body = b"{}"
            h = signing.signed_headers(KEY, SECRET, url("health"), body,
                                       nonce=secrets.token_hex(12))
            del h[header]
            r = self.client.post(url("health"), data=body, content_type="application/json",
                                 headers=h)
            self.assertError(r, 401, "missing_headers")

    def test_malformed_headers(self):
        cases = [
            {"X-QQBot-Timestamp": "12.5"},
            {"X-QQBot-Timestamp": "-100"},
            {"X-QQBot-Timestamp": "abc"},
            {"X-QQBot-Nonce": "short-nonce"},  # < 16
            {"X-QQBot-Nonce": "x" * 65},
            {"X-QQBot-Nonce": "bad nonce with spaces"},
            {"X-QQBot-Signature": "A" * 64},  # upper case
            {"X-QQBot-Signature": "0" * 63},
            {"X-QQBot-Key": "has space"},
        ]
        for override in cases:
            self.assertError(self.call("health", headers=override), 401, "missing_headers")

    def test_unknown_key(self):
        self.assertError(self.call("health", key="nope"), 401, "unknown_key")

    def test_stale_timestamp_past(self):
        r = self.call("health", ts=int(time.time()) - 400)
        self.assertError(r, 401, "stale_timestamp")

    def test_stale_timestamp_future(self):
        r = self.call("health", ts=int(time.time()) + 400)
        self.assertError(r, 401, "stale_timestamp")

    def test_timestamp_within_skew(self):
        self.assertOk(self.call("health", ts=int(time.time()) - 250))
        self.assertOk(self.call("health", ts=int(time.time()) + 250))

    @override_settings(QQBOT_API_MAX_SKEW=30)
    def test_skew_setting(self):
        self.assertError(self.call("health", ts=int(time.time()) - 60), 401, "stale_timestamp")

    def test_bad_signature_wrong_secret(self):
        r = self.call("health", secret="wrong-secret-" + "x" * 30)
        self.assertError(r, 401, "bad_signature")

    def test_bad_signature_body_tampered(self):
        body = b'{"after": 0}'
        h = signing.signed_headers(KEY, SECRET, url("events"), body, nonce=secrets.token_hex(12))
        r = self.client.post(url("events"), data=b'{"after": 1}',
                             content_type="application/json", headers=h)
        self.assertError(r, 401, "bad_signature")

    def test_bad_signature_other_path(self):
        """A signature for one endpoint does not work on another."""
        body = b"{}"
        h = signing.signed_headers(KEY, SECRET, url("groups"), body, nonce=secrets.token_hex(12))
        r = self.client.post(url("health"), data=body, content_type="application/json",
                             headers=h)
        self.assertError(r, 401, "bad_signature")

    def test_replayed_nonce(self):
        nonce = secrets.token_urlsafe(18)
        ts = int(time.time())
        self.assertOk(self.call("health", nonce=nonce, ts=ts))
        self.assertError(self.call("health", nonce=nonce, ts=ts), 401, "replayed_nonce")
        # Also on another endpoint (same key).
        self.assertError(self.call("groups", nonce=nonce), 401, "replayed_nonce")

    def test_bad_signature_does_not_burn_nonce(self):
        nonce = secrets.token_urlsafe(18)
        self.assertError(
            self.call("health", nonce=nonce, secret="w" * 40), 401, "bad_signature"
        )
        self.assertOk(self.call("health", nonce=nonce))

    @override_settings(QQBOT_API_MAX_BODY=100)
    def test_body_too_large(self):
        body = json.dumps({"after": 0, "pad": "x" * 200}).encode()
        self.assertError(self.call("events", body=body), 413, "too_large")

    @override_settings(QQBOT_API_MAX_BODY=100)
    def test_body_too_large_needs_valid_signature_first(self):
        body = json.dumps({"pad": "x" * 200}).encode()
        self.assertError(
            self.call("events", body=body, secret="w" * 40), 401, "bad_signature"
        )

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=50)
    def test_body_over_django_limit(self):
        body = json.dumps({"pad": "x" * 200}).encode()
        self.assertError(self.call("health", body=body), 413, "too_large")

    def test_rate_limited(self):
        key = "rl-" + secrets.token_hex(6)
        with override_settings(QQBOT_API_KEYS={key: SECRET}, QQBOT_API_RATE_LIMIT=3):
            window = int(time.time() // 60)
            for w in (window, window + 1):
                cache.delete(f"qqbot:rate:{key}:{w}")
            codes = [self.call("health", key=key).status_code for _ in range(4)]
            if int(time.time() // 60) == window:  # did not cross a minute boundary
                self.assertEqual(codes, [200, 200, 200, 429])
                r = self.call("health", key=key)
                self.assertError(r, 429, "rate_limited")
                self.assertTrue(1 <= int(r["Retry-After"]) <= 60)

    def test_rate_limit_is_per_key(self):
        k1, k2 = "rl-" + secrets.token_hex(6), "rl-" + secrets.token_hex(6)
        with override_settings(QQBOT_API_KEYS={k1: SECRET, k2: SECRET}, QQBOT_API_RATE_LIMIT=1):
            window = int(time.time() // 60)
            self.assertOk(self.call("health", key=k1))
            self.assertOk(self.call("health", key=k2))
            if int(time.time() // 60) == window:
                self.assertError(self.call("health", key=k1), 429, "rate_limited")

    def test_key_rotation_two_keys(self):
        keys = {"old": "o" * 40, "new": "n" * 40}
        with override_settings(QQBOT_API_KEYS=keys):
            self.assertOk(self.call("health", key="old", secret="o" * 40))
            self.assertOk(self.call("health", key="new", secret="n" * 40))
            self.assertError(self.call("health", key="old", secret="n" * 40), 401,
                             "bad_signature")
            self.assertError(self.call("health", key=KEY), 401, "unknown_key")

    def test_error_body_shape(self):
        r = self.client.post(url("health"))
        data = self.assertError(r, 401, "missing_headers")
        self.assertEqual(set(data), {"ok", "error", "message"})
        self.assertEqual(r["Cache-Control"], "no-store")


# --------------------------------------------------------------------------
# request body parsing and internal errors
# --------------------------------------------------------------------------


class BodyAndErrorTests(ApiTestCase):
    def test_empty_body_is_empty_object(self):
        self.assertOk(self.call("health", body=b""))

    def test_invalid_json(self):
        self.assertError(self.call("health", body=b"{nope"), 400, "bad_request")

    def test_not_utf8(self):
        self.assertError(self.call("health", body=b'"\xff\xfe"'), 400, "bad_request")

    def test_not_an_object(self):
        for body in (b"[]", b"1", b'"x"', b"null"):
            self.assertError(self.call("health", body=body), 400, "bad_request")

    def test_internal_exception_is_json_500(self):
        group = create_group(700001)
        with mock.patch.object(bindings, "claim", side_effect=RuntimeError("boom")):
            r = self.call("claim", {"qq": "12345678", "text": "QQ-ABCDEF",
                                    "group_id": group.group_id})
        data = self.assertError(r, 500, "internal_error")
        self.assertNotIn("boom", json.dumps(data))
        self.logger.exception.assert_called_once()

    def test_exception_in_every_endpoint(self):
        with mock.patch.object(views, "_ok", side_effect=ValueError("x")):
            for name in ("health", "groups", "events"):
                payload = {"after": 0} if name == "events" else {}
                self.assertError(self.call(name, payload), 500, "internal_error")

    def test_exception_during_auth_is_json_500(self):
        with mock.patch.object(signing, "authenticate", side_effect=ConnectionError("redis")):
            self.assertError(self.call("health"), 500, "internal_error")


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


class HealthTests(ApiTestCase):
    def test_health(self):
        data = self.assertOk(self.call("health"))
        self.assertEqual(data["version"], __version__)
        self.assertEqual(data["problems"], [])
        self.assertIs(data["config_ok"], True)

    def test_health_reports_short_secret(self):
        short = "short-secret"
        with override_settings(QQBOT_API_KEYS={KEY: short}):
            data = self.assertOk(self.call("health", secret=short))
        self.assertIn("qqbot.E003", data["problems"])
        self.assertIs(data["config_ok"], False)

    def test_health_uses_system_check_problems(self):
        # The health endpoint and the Django system checks share one source.
        with mock.patch("qqbot.checks.problems", return_value=["qqbot.W001"]):
            data = self.assertOk(self.call("health"))
        self.assertEqual(data["problems"], ["qqbot.W001"])
        self.assertIs(data["config_ok"], True)
        with mock.patch("qqbot.checks.problems", return_value=["qqbot.E001", "qqbot.W001"]):
            data = self.assertOk(self.call("health"))
        self.assertIs(data["config_ok"], False)


class GroupsTests(ApiTestCase):
    def test_active_groups_only(self):
        create_group(700001, name="聊天群")
        create_group(700002, kind="role", required=["Capital"], name="旗舰群")
        create_group(700003, name="停用群", is_active=False)
        data = self.assertOk(self.call("groups"))
        self.assertEqual(
            sorted(data["groups"], key=lambda g: g["group_id"]),
            [
                {"group_id": "700001", "name": "聊天群", "kind": "fixed"},
                {"group_id": "700002", "name": "旗舰群", "kind": "role"},
            ],
        )

    def test_empty(self):
        self.assertEqual(self.assertOk(self.call("groups"))["groups"], [])


class CheckTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.group = create_group(700001)
        self.role_group = create_group(700002, kind="role", required=["Capital"])
        self.alice = create_member("alice", corp_ticker="IGC", character_name="Alice A")
        bind(self.alice, "11111111", nickname="艾丽")
        self.bob = create_member("bob", state_perm=False)
        bind(self.bob, "22222222")

    def test_results_in_request_order(self):
        qqs = ["33333333", "11111111", "abc", 22222222, "0123456", 123, "11111111"]
        data = self.assertOk(self.call("check", {"group_id": "700001", "qqs": qqs}))
        self.assertEqual(data["group_id"], "700001")
        self.assertIsNone(data["roster"])
        res = data["results"]
        self.assertEqual(
            [(r["qq"], r["decision"], r["reason"]) for r in res],
            [
                ("33333333", "deny", "NOT_BOUND"),
                ("11111111", "allow", "OK"),
                ("abc", "review", "BAD_QQ"),
                ("22222222", "deny", "NO_ACCESS"),
                ("0123456", "review", "BAD_QQ"),
                (123, "review", "BAD_QQ"),
                ("11111111", "allow", "OK"),
            ],
        )
        self.assertEqual(res[1]["card"], "[IGC] Alice A - 艾丽")
        self.assertIsNone(res[0]["card"])
        for r in res:
            self.assertEqual(set(r), {"qq", "decision", "reason", "card"})

    def test_group_id_as_int_and_fullwidth_qq(self):
        data = self.assertOk(self.call("check", {"group_id": 700001, "qqs": ["１１１１１１１１"]}))
        self.assertEqual(data["results"][0]["qq"], "11111111")
        self.assertEqual(data["results"][0]["decision"], "allow")

    def test_role_group(self):
        data = self.assertOk(self.call("check", {"group_id": "700002", "qqs": ["11111111"]}))
        self.assertEqual(data["results"][0]["reason"], "GROUP_ROLE_MISSING")
        add_to_groups(self.alice, "Capital")
        data = self.assertOk(self.call("check", {"group_id": "700002", "qqs": ["11111111"]}))
        self.assertEqual(data["results"][0]["decision"], "allow")

    def test_conflict_is_review(self):
        carol = create_member("carol")
        dave = create_member("dave")
        bind(carol, "44444444", status="trusted")
        bind(dave, "44444444", status="trusted")
        data = self.assertOk(self.call("check", {"group_id": "700001", "qqs": ["44444444"]}))
        self.assertEqual(data["results"][0]["decision"], "review")
        self.assertEqual(data["results"][0]["reason"], "CONFLICT")

    def test_empty_qqs(self):
        data = self.assertOk(self.call("check", {"group_id": "700001", "qqs": []}))
        self.assertEqual(data["results"], [])

    def test_unknown_group(self):
        r = self.call("check", {"group_id": "799999", "qqs": ["11111111"]})
        self.assertError(r, 404, "unknown_group")

    def test_inactive_group(self):
        QQGroup.objects.filter(pk=self.group.pk).update(is_active=False)
        r = self.call("check", {"group_id": "700001", "qqs": ["11111111"]})
        self.assertError(r, 404, "unknown_group")

    def test_validation_errors(self):
        cases = [
            {"qqs": []},
            {"group_id": "abc", "qqs": []},
            {"group_id": None, "qqs": []},
            {"group_id": True, "qqs": []},
            {"group_id": "700001"},
            {"group_id": "700001", "qqs": "11111111"},
            {"group_id": "700001", "qqs": [1.5]},
            {"group_id": "700001", "qqs": [True]},
            {"group_id": "700001", "qqs": [None]},
            {"group_id": "700001", "qqs": [["11111111"]]},
            {"group_id": "700001", "qqs": [], "full_roster": "yes"},
            {"group_id": "700001", "qqs": [], "full_roster": 1},
            {"group_id": "700001", "qqs": [str(10000000 + i) for i in range(3001)]},
        ]
        for payload in cases:
            self.assertError(self.call("check", payload), 400, "bad_request")

    def test_max_qqs(self):
        qqs = [str(10000000 + i) for i in range(3000)]
        data = self.assertOk(self.call("check", {"group_id": "700001", "qqs": qqs}))
        self.assertEqual(len(data["results"]), 3000)

    def test_full_roster_replaces_roster(self):
        put_in_roster(self.group, ["55555555", "66666666"])
        put_in_roster(self.role_group, ["55555555"])
        payload = {"group_id": "700001", "qqs": ["11111111", "66666666", "bad", "77777777"],
                   "full_roster": True}
        data = self.assertOk(self.call("check", payload))
        self.assertEqual(data["roster"], {"added": 2, "removed": 1, "total": 3})
        self.assertEqual(
            set(RosterEntry.objects.filter(group=self.group).values_list("qq", flat=True)),
            {"11111111", "66666666", "77777777"},
        )
        # Other groups untouched.
        self.assertEqual(
            list(RosterEntry.objects.filter(group=self.role_group).values_list("qq", flat=True)),
            ["55555555"],
        )
        self.group.refresh_from_db()
        self.assertIsNotNone(self.group.last_roster_at)
        self.assertLess(timezone.now() - self.group.last_roster_at, timedelta(minutes=1))
        self.assertEqual(data["results"][2]["reason"], "BAD_QQ")

    def test_without_full_roster_roster_untouched(self):
        put_in_roster(self.group, ["55555555"])
        self.assertOk(self.call("check", {"group_id": "700001", "qqs": ["11111111"]}))
        self.assertEqual(
            list(RosterEntry.objects.filter(group=self.group).values_list("qq", flat=True)),
            ["55555555"],
        )

    def test_full_roster_empty_rejected(self):
        put_in_roster(self.group, ["55555555"])
        r = self.call("check", {"group_id": "700001", "qqs": ["bad"], "full_roster": True})
        self.assertError(r, 400, "bad_request")
        self.assertTrue(RosterEntry.objects.filter(group=self.group).exists())

    def test_full_roster_enables_old_member_binding(self):
        """A full roster makes "老成员免验证" work for members in the group."""
        self.assertOk(self.call("check", {"group_id": "700001", "qqs": ["88888888"],
                                          "full_roster": True}))
        erin = create_member("erin")
        result = bindings.submit(erin, "88888888", "小E")
        self.assertEqual(result.outcome, "trusted")

    def test_no_internal_ids_in_response(self):
        data = self.assertOk(self.call("check", {"group_id": "700001", "qqs": ["11111111"]}))
        text = json.dumps(data)
        self.assertNotIn('"user', text)
        self.assertNotIn('"pk"', text)
        self.assertNotIn('"id"', text)


class ClaimTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.group = create_group(700001)
        self.user = create_member("alice", corp_ticker="IGC", character_name="Alice A")
        cache.delete(f"qqbot:codes:{self.user.pk}")

    def new_code(self, qq="12345678", nickname="艾丽", now=None):
        result = bindings.submit(self.user, qq, nickname, now=now)
        self.assertEqual(result.outcome, "pending", result.message)
        return result.code

    def test_claimed_with_group(self):
        code = self.new_code()
        data = self.assertOk(self.call("claim", {
            "qq": "12345678", "text": f"我是艾丽，验证码 {code.lower()} 谢谢", "group_id": "700001",
        }))
        self.assertIs(data["claimed"], True)
        self.assertEqual(data["outcome"], "claimed")
        self.assertTrue(data["message"])
        self.assertEqual(
            data["result"],
            {"qq": "12345678", "decision": "allow", "reason": "OK",
             "card": "[IGC] Alice A - 艾丽"},
        )
        self.assertEqual(self.user.qqbot_binding.status, "verified")

    def test_claimed_without_group(self):
        code = self.new_code()
        data = self.assertOk(self.call("claim", {"qq": 12345678, "text": code}))
        self.assertIs(data["claimed"], True)
        self.assertIsNone(data["result"])

    def test_claimed_with_null_group(self):
        code = self.new_code()
        data = self.assertOk(self.call("claim", {"qq": "12345678", "text": code,
                                                 "group_id": None}))
        self.assertIs(data["claimed"], True)
        self.assertIsNone(data["result"])

    def test_no_code(self):
        data = self.assertOk(self.call("claim", {"qq": "12345678", "text": "你好",
                                                 "group_id": "700001"}))
        self.assertEqual((data["claimed"], data["outcome"]), (False, "no_code"))
        self.assertEqual(data["result"]["decision"], "deny")
        self.assertEqual(data["result"]["reason"], "NOT_BOUND")

    def test_pending_decision_when_code_wrong(self):
        self.new_code()
        data = self.assertOk(self.call("claim", {"qq": "12345678", "text": "QQ-ZZZZZZ",
                                                 "group_id": "700001"}))
        self.assertEqual(data["outcome"], "code_invalid")
        self.assertEqual(data["result"]["reason"], "PENDING_VERIFY")

    def test_qq_mismatch(self):
        code = self.new_code()
        data = self.assertOk(self.call("claim", {"qq": "99999999", "text": code}))
        self.assertEqual((data["claimed"], data["outcome"]), (False, "qq_mismatch"))
        self.assertIsNotNone(BindCode.objects.get(user=self.user).invalidated_at)

    def test_code_used(self):
        code = self.new_code()
        self.assertTrue(self.assertOk(self.call("claim", {"qq": "12345678", "text": code}))[
            "claimed"])
        data = self.assertOk(self.call("claim", {"qq": "12345678", "text": code}))
        self.assertEqual((data["claimed"], data["outcome"]), (False, "code_used"))

    def test_code_expired(self):
        code = self.new_code(now=timezone.now() - timedelta(hours=2))
        data = self.assertOk(self.call("claim", {"qq": "12345678", "text": code}))
        self.assertEqual((data["claimed"], data["outcome"]), (False, "code_expired"))

    def test_unknown_group_does_not_consume_code(self):
        code = self.new_code()
        r = self.call("claim", {"qq": "12345678", "text": code, "group_id": "799999"})
        self.assertError(r, 404, "unknown_group")
        self.assertIsNone(BindCode.objects.get(user=self.user).used_at)

    def test_validation_errors(self):
        cases = [
            {"text": "QQ-ABCDEF"},
            {"qq": "abc", "text": "QQ-ABCDEF"},
            {"qq": True, "text": "QQ-ABCDEF"},
            {"qq": "12345678"},
            {"qq": "12345678", "text": 5},
            {"qq": "12345678", "text": "x" * 2001},
            {"qq": "12345678", "text": "", "group_id": "bad"},
        ]
        for payload in cases:
            self.assertError(self.call("claim", payload), 400, "bad_request")


class EventsTests(ApiTestCase):
    def make_events(self, n, age=timedelta(minutes=1)):
        for i in range(n):
            core_events.emit(Event.Kind.RECHECK, str(10000000 + i))
        Event.objects.update(created_at=timezone.now() - age)
        return list(Event.objects.order_by("id").values_list("id", flat=True))

    def test_paging(self):
        ids = self.make_events(5)
        data = self.assertOk(self.call("events", {"after": 0, "limit": 2}))
        self.assertEqual([e["id"] for e in data["events"]], ids[:2])
        self.assertEqual(data["last_id"], ids[1])
        self.assertIs(data["has_more"], True)
        first = data["events"][0]
        self.assertEqual(set(first), {"id", "kind", "qq", "created_at"})
        self.assertEqual((first["kind"], first["qq"]), ("recheck", "10000000"))

        data = self.assertOk(self.call("events", {"after": ids[1], "limit": 2}))
        self.assertEqual([e["id"] for e in data["events"]], ids[2:4])
        data = self.assertOk(self.call("events", {"after": ids[3], "limit": 2}))
        self.assertEqual([e["id"] for e in data["events"]], ids[4:])
        self.assertIs(data["has_more"], False)
        data = self.assertOk(self.call("events", {"after": ids[4]}))
        self.assertEqual(data["events"], [])
        self.assertEqual(data["last_id"], ids[4])
        self.assertIs(data["has_more"], False)

    def test_default_limit(self):
        self.make_events(3)
        data = self.assertOk(self.call("events", {"after": 0}))
        self.assertEqual(len(data["events"]), 3)

    def test_recent_events_are_delayed(self):
        ids = self.make_events(2)
        core_events.emit(Event.Kind.CARD, "10000009")  # brand new: not visible yet
        data = self.assertOk(self.call("events", {"after": 0}))
        self.assertEqual([e["id"] for e in data["events"]], ids)
        self.assertEqual(data["last_id"], ids[-1])

    def test_validation_errors(self):
        cases = [
            {},
            {"after": -1},
            {"after": "0"},
            {"after": 1.0},
            {"after": True},
            {"after": 0, "limit": 0},
            {"after": 0, "limit": 501},
            {"after": 0, "limit": "10"},
            {"after": 0, "limit": None},
        ]
        for payload in cases:
            self.assertError(self.call("events", payload), 400, "bad_request")

    def test_limit_bounds_ok(self):
        self.assertOk(self.call("events", {"after": 0, "limit": 1}))
        self.assertOk(self.call("events", {"after": 0, "limit": 500}))
