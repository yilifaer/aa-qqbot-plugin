"""URL-table security sweep (docs/SPEC.md section 8, rules 1 and 2).

Walks the *real* resolved URLconf, so every ``qqbot:*`` route is covered,
including routes added later:

* the bot API views (``auth_hooks.PUBLIC_VIEWS``) answer anonymous POSTs
  with 401 JSON, never a login redirect;
* every other route redirects anonymous users to the login page and answers
  403 to a logged-in user with a main character but without the required
  permission (``qqbot.manage`` under ``/qqbot/manage/``, otherwise
  ``qqbot.basic_access``);
* the view functions carry their own login / permission decorators, i.e.
  they stay protected without AA's ``main_character_required`` URL wrapper.
"""

import importlib
import json
import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.shortcuts import resolve_url
from django.test import RequestFactory, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse

from allianceauth.tests.auth_utils import AuthUtils

from ..auth_hooks import PUBLIC_VIEWS
from .utils import BASIC_ACCESS, MANAGE, create_member, create_user

NAMESPACE = "qqbot"
MANAGE_PREFIX = "/qqbot/manage/"

# Dummy values for path converters.
_DUMMY = {"int": 1, "str": "x", "slug": "x", "uuid": "00000000-0000-0000-0000-000000000000",
          "path": "x"}


def _converter_kind(converter) -> str:
    name = type(converter).__name__.lower()
    for kind in _DUMMY:
        if name.startswith(kind):
            return kind
    return "str"


def qqbot_routes():
    """``(url_name, URLPattern, converters)`` for every route in the qqbot
    namespace of the live URLconf (after AA's URL decoration)."""
    found = []

    def walk(patterns, namespaces, converters):
        for p in patterns:
            conv = {**converters, **getattr(p.pattern, "converters", {})}
            if isinstance(p, URLResolver):
                ns = namespaces + ([p.namespace] if p.namespace else [])
                walk(p.url_patterns, ns, conv)
            elif isinstance(p, URLPattern) and namespaces and namespaces[-1] == NAMESPACE:
                found.append((p.name, p, conv))

    walk(get_resolver().url_patterns, [], {})
    return found


def route_url(name, converters):
    kwargs = {k: _DUMMY[_converter_kind(c)] for k, c in converters.items()}
    return reverse(f"{NAMESPACE}:{name}", kwargs=kwargs or None)


def is_api(pattern) -> bool:
    return pattern.lookup_str in PUBLIC_VIEWS


def required_perm(path) -> str:
    return MANAGE if path.startswith(MANAGE_PREFIX) else BASIC_ACCESS


def unwrapped_view(pattern):
    """The view as defined in its module, i.e. without AA's URL decoration."""
    module_name, _, attr = pattern.lookup_str.rpartition(".")
    view = getattr(importlib.import_module(module_name), attr)
    if isinstance(view, type):  # class-based view
        view = view.as_view()
    return view


class RouteDiscoveryTests(TestCase):
    def test_routes_found(self):
        routes = qqbot_routes()
        names = {name for name, _p, _c in routes}
        self.assertIn("my_qq", names)
        self.assertTrue(any(is_api(p) for _n, p, _c in routes))
        for name, pattern, _conv in routes:
            with self.subTest(route=pattern.lookup_str):
                self.assertTrue(name, f"{pattern.lookup_str} has no URL name")

    def test_every_public_view_is_routed(self):
        routed = {p.lookup_str for _n, p, _c in qqbot_routes()}
        self.assertEqual(set(PUBLIC_VIEWS) - routed, set())


class UrlSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.routes = [(route_url(n, c), p) for n, p, c in qqbot_routes()]
        self.login_url = resolve_url(settings.LOGIN_URL)

    def assert_login_redirect(self, response, msg):
        self.assertEqual(response.status_code, 302, msg)
        self.assertTrue(response["Location"].startswith(self.login_url), msg)

    def test_api_anonymous_gets_401_json(self):
        api = [(u, p) for u, p in self.routes if is_api(p)]
        self.assertEqual(len(api), len(PUBLIC_VIEWS))
        logging.disable(logging.WARNING)  # the API logs every refusal
        self.addCleanup(logging.disable, logging.NOTSET)
        for url, _p in api:
            with self.subTest(url=url):
                r = self.client.post(url, data="{}", content_type="application/json")
                self.assertEqual(r.status_code, 401, r.content)
                self.assertTrue(r["Content-Type"].startswith("application/json"))
                body = json.loads(r.content)
                self.assertIs(body["ok"], False)
                self.assertIn("error", body)

    def test_anonymous_redirected_to_login(self):
        for url, pattern in self.routes:
            if is_api(pattern):
                continue
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    r = getattr(self.client, method)(url)
                    self.assert_login_redirect(r, f"{method.upper()} {url}")

    def test_logged_in_without_permission_forbidden(self):
        # A user with a main character and every qqbot permission except the
        # one the route needs.
        users = {
            BASIC_ACCESS: create_user("has_manage_only"),
            MANAGE: create_member("member_only"),  # basic_access via state
        }
        AuthUtils.add_permissions_to_user_by_name([MANAGE], users[BASIC_ACCESS])
        checked = 0
        for url, pattern in self.routes:
            if is_api(pattern):
                continue
            user = users[required_perm(url)]
            self.client.force_login(user)
            for method in ("get", "post"):
                with self.subTest(url=url, method=method, user=user.username):
                    r = getattr(self.client, method)(url)
                    self.assertEqual(r.status_code, 403, f"{method.upper()} {url}")
                    checked += 1
        self.assertGreater(checked, 0)

    def request(self, method, url, user):
        request = getattr(RequestFactory(), method)(url)
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = user
        return request

    def test_views_protected_without_aa_decoration(self):
        member_only = create_member("member_only")
        manage_only = create_user("manage_only")
        AuthUtils.add_permissions_to_user_by_name([MANAGE], manage_only)
        no_perm = {BASIC_ACCESS: manage_only, MANAGE: member_only}
        for url, pattern in self.routes:
            if is_api(pattern):
                continue
            view = unwrapped_view(pattern)
            match_kwargs = resolve(url).kwargs
            for method in ("get", "post"):
                with self.subTest(view=pattern.lookup_str, method=method):
                    r = view(self.request(method, url, AnonymousUser()), **match_kwargs)
                    self.assert_login_redirect(r, f"anonymous {method.upper()} {url}")
                with self.subTest(view=pattern.lookup_str, method=method, user="no-perm"):
                    request = self.request(method, url, no_perm[required_perm(url)])
                    try:
                        r = view(request, **match_kwargs)
                    except PermissionDenied:
                        continue
                    self.assertEqual(r.status_code, 403, f"no-perm {method.upper()} {url}")
