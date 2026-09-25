"""The UI follows the user's AA language: English for English, else Simplified Chinese.

English msgids in code and templates, the Chinese texts in
``qqbot/locale/zh_Hans``. The test settings use ``LANGUAGE_CODE = "zh-hans"``
(most other tests assert the Chinese texts); here the language is chosen per
request with AA's language cookie, like AA's language menu does.

界面跟随用户在 AA 里选的语言：英文或简体中文。代码和模板里写英文原文，
中文在 ``qqbot/locale/zh_Hans`` 里。
"""

import ast
import gettext as gettext_module
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import get_language_info, trans_real

from .. import checks, i18n
from ..auth_hooks import QQBotMenuItem
from ..core import bindings
from ..models import Binding, Config, QQGroup
from .test_manage_security import create_manager
from .utils import bind, create_group, create_member, put_in_roster

PACKAGE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = PACKAGE_DIR / "locale"
PO_FILE = LOCALE_DIR / "zh_Hans" / "LC_MESSAGES" / "django.po"
MO_FILE = LOCALE_DIR / "zh_Hans" / "LC_MESSAGES" / "django.mo"

# Han ideographs plus CJK / full-width punctuation: anything a Chinese UI
# text would contain.
CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
# Han ideographs only (Python scan: the full-width digit and dash tables in
# the normalisation code are data, not UI text).
HAN_RE = re.compile(r"[㐀-䶿一-鿿]")

SERVICES = reverse("services:services")
SUBMIT = reverse("qqbot:member_submit")

QQ = "12345678"
QQ_OTHER = "23456789"
QQ_CONFLICT = "34567890"


def set_language(client, code):
    """What AA's language menu does: Django's language cookie."""
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = code


# --------------------------------------------------------------------------
# .po parsing (stdlib only; polib is not a dependency)
# --------------------------------------------------------------------------


def _unquote(s: str) -> str:
    return ast.literal_eval(s)


def parse_po(path) -> list[dict]:
    """A small .po parser: a list of entries with ``msgctxt``, ``msgid``,
    ``msgid_plural``, ``msgstr`` (dict index -> text) and ``flags``."""
    entries = []
    entry = None
    field = None

    def finish():
        if entry is not None and "msgid" in entry:
            entries.append(entry)

    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#,"):
            if entry is not None and "msgid" in entry:
                finish()
                entry = None
            entry = entry or {"flags": set(), "msgstr": {}}
            entry["flags"] |= {f.strip() for f in line[2:].split(",")}
            continue
        if line.startswith("#"):
            continue
        m = re.match(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(\".*\")$", line)
        if m:
            key, idx, value = m.group(1), m.group(2), _unquote(m.group(3))
            if key in ("msgctxt", "msgid") and entry is not None and (
                "msgid" in entry or (key == "msgctxt" and "msgctxt" in entry)
            ):
                finish()
                entry = None
            entry = entry or {"flags": set(), "msgstr": {}}
            if key.startswith("msgstr"):
                field = ("msgstr", int(idx) if idx is not None else 0)
                entry["msgstr"][field[1]] = value
            else:
                field = (key, None)
                entry[key] = value
            continue
        if line.startswith('"'):
            value = _unquote(line)
            if field[0] == "msgstr":
                entry["msgstr"][field[1]] += value
            else:
                entry[field[0]] += value
            continue
        raise AssertionError(f"unexpected line in {path}: {raw!r}")
    finish()
    return entries


class CatalogTests(SimpleTestCase):
    def setUp(self):
        self.entries = parse_po(PO_FILE)
        self.header = next(e for e in self.entries if e["msgid"] == "" and "msgctxt" not in e)
        self.messages = [e for e in self.entries if e is not self.header]

    def test_catalog_complete(self):
        self.assertGreater(len(self.messages), 100)
        for e in self.messages:
            with self.subTest(msgid=e["msgid"]):
                self.assertNotIn("fuzzy", e["flags"])
                self.assertTrue(e["msgstr"], "no msgstr")
                expected = [0]  # zh has one plural form
                self.assertEqual(sorted(e["msgstr"]), expected)
                # English words in the msgid (not placeholders or HTML) mean
                # the msgstr must be Chinese; "%(label)s: %(value)s" or ", "
                # only change punctuation.
                words = re.sub(r"%\(\w+\)[sd]|<[^>]*>", "", e["msgid"])
                for text in e["msgstr"].values():
                    self.assertTrue(text.strip(), "empty msgstr")
                    if re.search(r"[A-Za-z]{2,}", words):
                        self.assertRegex(text, HAN_RE, "msgstr is not Chinese")
                    # Same named placeholders as the English text.
                    placeholders = set(re.findall(r"%\((\w+)\)", e["msgid"]))
                    self.assertEqual(set(re.findall(r"%\((\w+)\)", text)), placeholders)

    def test_header(self):
        self.assertNotIn("fuzzy", self.header["flags"])
        header = self.header["msgstr"][0]
        self.assertIn("Language: zh_Hans\n", header)
        self.assertIn("Plural-Forms: nplurals=1; plural=0;\n", header)
        self.assertIn("charset=UTF-8", header)

    def test_mo_matches_po(self):
        """The shipped .mo is compiled from the current .po."""
        with open(MO_FILE, "rb") as fp:
            catalog = gettext_module.GNUTranslations(fp)._catalog
        expected = {}
        for e in self.entries:
            key = e["msgid"] if "msgctxt" not in e else f"{e['msgctxt']}\x04{e['msgid']}"
            if "msgid_plural" in e:
                for i, text in e["msgstr"].items():
                    expected[(key, i)] = text
            else:
                expected[key] = e["msgstr"][0]
        # msgfmt drops POT-Creation-Date from the header; compare the rest.
        mo_header = catalog.pop("")
        po_header = expected.pop("")
        self.assertEqual(
            mo_header, re.sub(r"POT-Creation-Date: [^\n]*\n", "", po_header)
        )
        self.assertEqual(catalog, expected)

    @skipUnless(shutil.which("xgettext") and shutil.which("msgmerge"), "needs GNU gettext")
    def test_catalog_up_to_date(self):
        """Every string marked in the code is in the catalog, and nothing
        stale is left (``makemessages`` finds exactly the same msgids)."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "qqbot"
            shutil.copytree(
                PACKAGE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__", "*.mo")
            )
            cwd = os.getcwd()
            try:
                os.chdir(copy)
                with override_settings(LOCALE_PATHS=[str(copy / "locale")]):
                    call_command(
                        "makemessages", locale=["zh_Hans"], no_location=True, verbosity=0
                    )
            finally:
                os.chdir(cwd)
            fresh = parse_po(copy / "locale" / "zh_Hans" / "LC_MESSAGES" / "django.po")

        def keys(entries):
            return {
                (e.get("msgctxt"), e["msgid"], e.get("msgid_plural"))
                for e in entries
                if e["msgid"]
            }

        self.assertEqual(keys(fresh), keys(self.entries))


# --------------------------------------------------------------------------
# rendered pages
# --------------------------------------------------------------------------


class _TextCollector(HTMLParser):
    """Collects text nodes and user-visible attribute values (no scripts/styles)."""

    VISIBLE_ATTRS = {"title", "placeholder", "alt", "aria-label", "value", "data-bs-title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for name, value in attrs:
            if name in self.VISIBLE_ATTRS and value:
                self.texts.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.texts.append(data)


def visible_texts(html: str) -> list[str]:
    parser = _TextCollector()
    parser.feed(html)
    parser.close()
    return parser.texts


def data_strings() -> list[str]:
    """Chinese texts on the pages that are data, not our UI: the default
    group rules, the permission names, and AA's language menu (each
    language in its own name)."""
    strings = [line for line in Config.DEFAULT_RULES.splitlines() if line.strip()]
    strings += list(
        Permission.objects.filter(content_type__app_label="qqbot").values_list("name", flat=True)
    )
    strings += [get_language_info(code)["name_local"] for code, _name in settings.LANGUAGES]
    return sorted(strings, key=len, reverse=True)


class PageLanguageTestCase(TestCase):
    """Members in every card state and a manager with data on every page.

    All data (names, nicknames, groups) is ASCII, so any Chinese on an
    English page comes from our templates or code.
    """

    def setUp(self):
        cache.clear()
        self.fixed = create_group("100001", name="Main chat", description="Everyday chat")
        self.role = create_group(
            "100002", kind=QQGroup.Kind.ROLE, required=["Cap"], name="Capitals"
        )
        put_in_roster(self.fixed, [QQ_OTHER])
        # Members: unbound, pending, bound (verified), trusted, conflict.
        self.unbound = create_member("unbound", character_name="Una Bound")
        self.pending = create_member("pending", character_name="Penny Ding")
        self.verified = create_member("verified", character_name="Vera Fied")
        bind(self.verified, QQ, nickname="Vera")
        self.trusted = create_member("trusted", character_name="Tru Sted")
        bindings.submit(self.trusted, QQ_OTHER, "Tru")
        self.conflict_a = create_member("conflicta", character_name="Con A")
        self.conflict_b = create_member("conflictb", character_name="Con B")
        bind(self.conflict_a, QQ_CONFLICT, status=Binding.Status.TRUSTED, nickname="ConA")
        bind(self.conflict_b, QQ_CONFLICT, status=Binding.Status.TRUSTED, nickname="ConB")
        self.manager = create_manager("boss")
        self.data = data_strings()

    # -- helpers --

    def cjk_left(self, html: str) -> list[str]:
        found = []
        for text in visible_texts(html):
            for s in self.data:
                text = text.replace(s, "")
            if CJK_RE.search(text):
                found.append(text.strip())
        return found

    def services(self, user, lang, pending_submit=False):
        self.client.force_login(user)
        set_language(self.client, lang)
        if pending_submit:
            r = self.client.post(SUBMIT, {"qq": "45678901", "nickname": "Penny"})
            self.assertEqual(r.status_code, 302)
        r = self.client.get(SERVICES)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def member_pages(self, lang):
        return {
            "unbound": self.services(self.unbound, lang),
            "pending": self.services(self.pending, lang, pending_submit=True),
            "verified": self.services(self.verified, lang),
            "trusted": self.services(self.trusted, lang),
            "conflict": self.services(self.conflict_a, lang),
        }

    def manage_urls(self):
        conflict = Binding.objects.get(user=self.conflict_a)
        verified = Binding.objects.get(user=self.verified)
        return {
            "index": reverse("qqbot:manage_index"),
            "groups": reverse("qqbot:manage_groups"),
            "group_create": reverse("qqbot:manage_group_create"),
            "group_edit": reverse("qqbot:manage_group_edit", args=[self.role.pk]),
            "group_delete": reverse("qqbot:manage_group_delete", args=[self.role.pk]),
            "bindings": reverse("qqbot:manage_bindings"),
            "bindings_conflict": reverse("qqbot:manage_bindings") + "?conflict=on",
            "binding_verified": reverse("qqbot:manage_binding", args=[verified.pk]),
            "binding_conflict": reverse("qqbot:manage_binding", args=[conflict.pk]),
            "binding_unbind": reverse("qqbot:manage_binding_unbind", args=[verified.pk]),
            "pending": reverse("qqbot:manage_pending"),
            "settings": reverse("qqbot:manage_settings"),
            "audit": reverse("qqbot:manage_audit"),
        }

    def manager_actions(self) -> dict:
        """Some manager actions, so that the audit log has content; returns
        the pages shown after each action (with its flash message)."""
        binding = Binding.objects.get(user=self.verified)
        config = Config.get_solo()
        posts = {
            "after_group_create": (
                reverse("qqbot:manage_group_create"),
                {"name": "Fleet ops", "group_id": "200001", "kind": "fixed",
                 "sort_order": 5, "is_active": "on"},
            ),
            "after_card": (
                reverse("qqbot:manage_binding_card", args=[binding.pk]),
                {"card": "Vera card", "qq": QQ},
            ),
            "after_settings": (
                reverse("qqbot:manage_settings"),
                {"rules_text": "Be nice.", "card_format": config.card_format,
                 "code_ttl_minutes": 15, "roster_max_age_days": config.roster_max_age_days,
                 "trusted_window_days": config.trusted_window_days,
                 "rebind_cooldown_hours": config.rebind_cooldown_hours},
            ),
        }
        pages = {}
        for name, (url, data) in posts.items():
            r = self.client.post(url, data, follow=True)
            self.assertEqual(r.status_code, 200, name)
            pages[name] = r.content.decode()
        return pages

    def manage_pages(self, lang):
        self.client.force_login(self.manager)
        set_language(self.client, lang)
        pages = self.manager_actions()
        for name, url in self.manage_urls().items():
            r = self.client.get(url, follow=True)
            self.assertEqual(r.status_code, 200, name)
            pages[name] = r.content.decode()
        # A form with errors.
        r = self.client.post(
            reverse("qqbot:manage_group_create"),
            {"name": "Broken", "group_id": "0123", "kind": "role", "sort_order": 1},
        )
        self.assertEqual(r.status_code, 200)
        pages["group_create_errors"] = r.content.decode()
        return pages


class EnglishPageTests(PageLanguageTestCase):
    def test_member_card_in_english(self):
        for state, html in self.member_pages("en").items():
            with self.subTest(state=state):
                self.assertIn("QQ binding", html)
                self.assertEqual(self.cjk_left(html), [])

    def test_member_card_english_labels(self):
        pages = self.member_pages("en")
        self.assertIn("Bind your QQ to join the alliance QQ groups.", pages["unbound"])
        self.assertIn("Group rules", pages["unbound"])
        self.assertIn(">Pending", pages["pending"])
        self.assertIn(">Enabled", pages["verified"])
        self.assertIn("Trusted (already in group)", pages["trusted"])
        self.assertIn("Conflict - contact a QQ admin", pages["conflict"])

    def test_member_messages_in_english(self):
        self.client.force_login(self.unbound)
        set_language(self.client, "en")
        self.client.post(SUBMIT, {"qq": "0123", "nickname": "Una"})
        html = self.client.get(SERVICES).content.decode()
        self.assertIn("Enter a valid QQ number", html)
        self.assertEqual(self.cjk_left(html), [])

    def test_manage_pages_in_english(self):
        for name, html in self.manage_pages("en").items():
            with self.subTest(page=name):
                self.assertIn("QQ Admin", html)
                self.assertEqual(self.cjk_left(html), [])

    def test_manage_english_labels(self):
        pages = self.manage_pages("en")
        self.assertIn("Fixed group", pages["groups"])
        self.assertIn("Role group", pages["groups"])
        self.assertIn("Audit log", pages["audit"])
        self.assertIn("Settings saved.", pages["after_settings"])
        self.assertIn("Group nickname set.", pages["after_card"])
        self.assertIn("Added group &quot;Fleet ops&quot;.", pages["after_group_create"])
        self.assertIn("Change group nickname", pages["audit"])
        self.assertIn("Change settings", pages["audit"])
        self.assertIn("Invalid group number: Enter 5–11 digits", pages["group_create_errors"])
        self.assertIn("A role group needs at least one AA group", pages["group_create_errors"])


class ChinesePageTests(PageLanguageTestCase):
    def test_member_card_in_english(self):
        pages = self.member_pages("zh-hans")
        for html in pages.values():
            self.assertIn("QQ 绑定", html)
        self.assertIn("绑定后可以加入联盟 QQ 群。", pages["unbound"])
        self.assertIn("入群须知", pages["unbound"])
        self.assertIn('text-bg-primary">待验证', pages["pending"])
        self.assertIn('text-bg-success">已启用', pages["verified"])
        self.assertIn("老成员免验证", pages["trusted"])
        self.assertIn("冲突 - 请联系 QQ 管理员", pages["conflict"])
        for state, html in pages.items():
            with self.subTest(state=state):
                self.assertNotEqual(self.cjk_left(html), [])

    def test_manage_pages_in_english(self):
        pages = self.manage_pages("zh-hans")
        for html in pages.values():
            self.assertIn("QQ 管理", html)
        self.assertIn("固定群", pages["groups"])
        self.assertIn("身份组小群", pages["groups"])
        self.assertIn("操作记录", pages["audit"])
        self.assertIn("设置已保存。", pages["after_settings"])
        self.assertIn("群名片已设置。", pages["after_card"])
        self.assertIn("已添加群「Fleet ops」。", pages["after_group_create"])
        self.assertIn("群号格式不正确：请输入 5–11 位数字", pages["group_create_errors"])
        self.assertIn("身份组小群至少要选择一个 AA 组", pages["group_create_errors"])
        # The English check above really looks at these texts.
        for name, html in pages.items():
            with self.subTest(page=name):
                self.assertNotEqual(self.cjk_left(html), [])

    def test_chinese_browser_gets_chinese(self):
        """No language chosen in AA: a Chinese browser (zh-CN) gets Chinese."""
        self.client.force_login(self.unbound)
        with override_settings(LANGUAGE_CODE="en-us"):
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="zh-CN,zh;q=0.9").content.decode()
            self.assertIn("绑定后可以加入联盟 QQ 群。", html)
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9").content.decode()
            self.assertIn("Bind your QQ to join the alliance QQ groups.", html)


class EnglishCopyTests(PageLanguageTestCase):
    """English wording fixes that keep the Chinese output unchanged."""

    def test_trusted_state_without_nested_brackets(self):
        html = self.services(self.trusted, "en")
        self.assertIn("· Trusted (already in group)", html)
        self.assertNotIn("(Trusted (already in group))", html)
        html = self.services(self.trusted, "zh-hans")
        self.assertIn("（老成员免验证）", html)

    def test_nickname_action_uses_one_verb(self):
        html = self.services(self.verified, "en")
        self.assertIn('title="Change the nickname part of your group nickname"', html)
        self.assertNotIn("Change nickname", html)
        self.assertEqual(html.count("Edit nickname"), 2)  # button and panel title
        html = self.services(self.verified, "zh-hans")
        self.assertIn('title="修改群名片上的昵称"', html)
        self.assertIn("修改昵称</div>", html)
        self.assertIn("改昵称\n", html)

    def test_pending_page_wording(self):
        self.client.force_login(self.manager)
        set_language(self.client, "en")
        html = self.client.get(reverse("qqbot:manage_pending")).content.decode()
        self.assertIn(
            "for existing members the binding takes effect as soon as they enter their QQ, "
            "with no verification code.",
            html,
        )
        self.assertNotIn("existing members take effect", html)
        set_language(self.client, "zh-hans")
        html = self.client.get(reverse("qqbot:manage_pending")).content.decode()
        self.assertIn("老成员填写 QQ 后会直接生效，不需要验证码。", html)


class UILanguageTests(SimpleTestCase):
    def test_chinese_stays_chinese_everything_else_is_english(self):
        for language, expected in (
            ("zh-hans", "zh-hans"),
            ("zh-hant", "zh-hans"),
            ("zh-CN", "zh-hans"),
            ("zh_TW", "zh-hans"),
            ("zh", "zh-hans"),
            ("en", "en"),
            ("en-us", "en"),
            ("en-GB", "en"),
            ("de", "en"),
            ("ko-kr", "en"),
            ("ru", "en"),
            ("zho", "en"),
            ("", "en"),
        ):
            with self.subTest(language=language):
                self.assertEqual(i18n.ui_language(language), expected)
                self.assertEqual(i18n.ui_language(i18n.ui_language(language)), expected)

    def test_default_is_the_active_language(self):
        with translation.override("de"):
            self.assertEqual(i18n.ui_language(), "en")
            with i18n.override():
                self.assertEqual(translation.get_language(), "en")
            self.assertEqual(translation.get_language(), "de")
        with translation.override("zh-hant"):
            self.assertEqual(i18n.ui_language(), "zh-hans")


# Languages AA offers besides English and Simplified Chinese, for which qqbot
# has no catalog.
OTHER_LANGUAGES = ("de", "ko-kr", "ru")


def foreign_words(language: str) -> set[str]:
    """Texts that another app's catalog would give our msgids in
    ``language`` (e.g. "Suche" for "Search"): none may appear on our pages."""
    words = set()
    with translation.override(language):
        for e in parse_po(PO_FILE):
            if not e["msgid"] or "msgid_plural" in e:
                continue
            if "msgctxt" in e:
                text = translation.pgettext(e["msgctxt"], e["msgid"])
            else:
                text = translation.gettext(e["msgid"])
            if text not in (e["msgid"], e["msgstr"][0]):
                words.add(text)
    return words


@contextmanager
def site_language(code):
    """``override_settings(LANGUAGE_CODE=code)`` that also drops Django's
    cached catalogs: a cached catalog keeps the fallback language (the
    ``LANGUAGE_CODE``) it was built with."""
    trans_real._translations = {}
    try:
        with override_settings(LANGUAGE_CODE=code):
            yield
    finally:
        trans_real._translations = {}


class OtherLanguageTests(PageLanguageTestCase):
    """Any AA language other than Chinese shows qqbot in plain English,
    whatever the site's ``LANGUAGE_CODE`` is, without words from other
    catalogs mixed in."""

    def assert_no_foreign_words(self, html, language, chrome):
        texts = [t.strip() for t in visible_texts(html)]
        for word in foreign_words(language) - chrome:
            self.assertNotIn(word, texts)

    def chrome_texts(self, language) -> set[str]:
        """AA's own menus in ``language`` (they may use the same words)."""
        self.client.force_login(self.manager)
        set_language(self.client, language)
        html = self.client.get(reverse("authentication:dashboard")).content.decode()
        return {t.strip() for t in visible_texts(html)}

    def test_leak_check_sees_foreign_words(self):
        """Guard: without qqbot's switch these languages do translate some
        of our msgids from other catalogs."""
        for language in OTHER_LANGUAGES:
            with self.subTest(language=language):
                self.assertTrue(foreign_words(language))

    def test_member_card_in_english(self):
        for site in ("en-us", "zh-hans"):
            with site_language(site):
                for language in OTHER_LANGUAGES:
                    with self.subTest(site=site, language=language):
                        chrome = self.chrome_texts(language)
                        pages = self.member_pages(language)
                        for html in pages.values():
                            self.assertIn("QQ binding", html)
                            self.assertNotIn("QQ 绑定", html)
                            self.assert_no_foreign_words(html, language, chrome)
                        self.assertIn("Bind your QQ to join the alliance QQ groups.", pages["unbound"])
                        self.assertIn("Trusted (already in group)", pages["trusted"])
                        self.assertIn("Conflict - contact a QQ admin", pages["conflict"])

    def test_member_messages_in_english(self):
        with site_language("en-us"):
            self.client.force_login(self.unbound)
            set_language(self.client, "de")
            self.client.post(SUBMIT, {"qq": "0123", "nickname": "Una"})
            html = self.client.get(SERVICES).content.decode()
            self.assertIn("Enter a valid QQ number", html)
            self.assertNotIn("请输入正确的 QQ 号", html)

    def test_manage_pages_in_english(self):
        for site in ("en-us", "zh-hans"):
            with site_language(site):
                for language in OTHER_LANGUAGES:
                    with self.subTest(site=site, language=language):
                        chrome = self.chrome_texts(language)
                        pages = self.manage_pages(language)
                        for name, html in pages.items():
                            self.assertIn("QQ Admin", html, name)
                            self.assertNotIn("QQ 管理", html, name)
                            self.assert_no_foreign_words(html, language, chrome)
                        self.assertIn("Audit log", pages["audit"])
                        # Flash messages (the same actions repeat on each round, so
                        # later rounds get "nothing to change" / "already added").
                        self.assertRegex(pages["after_settings"], r"Settings saved\.|Nothing to change\.")
                        self.assertIn("group nickname", pages["after_card"].lower())
                        self.assertIn("Invalid group number", pages["group_create_errors"])

    def test_aa_menus_stay_in_the_users_language(self):
        """Only qqbot's part of the page switches; AA's own sidebar does not."""
        aa_text = "Services"  # AA's menu entry (a variable: not one of our msgids)
        with translation.override("de"):
            services = translation.gettext(aa_text)
        self.assertNotEqual(services, aa_text)
        self.client.force_login(self.manager)
        set_language(self.client, "de")
        html = self.client.get(reverse("qqbot:manage_bindings")).content.decode()
        self.assertIn(services, [t.strip() for t in visible_texts(html)])
        self.assertIn("QQ Admin", html)


class BrowserLanguageTests(PageLanguageTestCase):
    """What docs/GUIDE.md section 5 says about members who see English."""

    def test_english_browser_wins_over_language_code(self):
        self.client.force_login(self.unbound)
        with site_language("zh-hans"):
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9").content.decode()
            self.assertIn("Bind your QQ to join the alliance QQ groups.", html)
            # Choosing 简体中文 in AA's menu (the cookie) is what helps.
            set_language(self.client, "zh-hans")
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9").content.decode()
            self.assertIn("绑定后可以加入联盟 QQ 群。", html)

    def test_other_browser_language_gets_english(self):
        self.client.force_login(self.unbound)
        with site_language("zh-hans"):
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9").content.decode()
            self.assertIn("Bind your QQ to join the alliance QQ groups.", html)

    def test_traditional_chinese_browser_gets_chinese(self):
        self.client.force_login(self.unbound)
        with site_language("en-us"):
            html = self.client.get(SERVICES, HTTP_ACCEPT_LANGUAGE="zh-TW,zh;q=0.9").content.decode()
            self.assertIn("绑定后可以加入联盟 QQ 群。", html)


# --------------------------------------------------------------------------
# menu, system checks
# --------------------------------------------------------------------------


class MenuLanguageTests(TestCase):
    def test_menu_item_follows_language(self):
        cache.clear()
        user = create_manager("menuboss")
        request = RequestFactory().get("/dashboard/")
        request.user = user
        with translation.override("en"):
            html = QQBotMenuItem().render(request)
            self.assertIn("QQ Admin", html)
            self.assertIsNone(CJK_RE.search(html))
        with translation.override("zh-hans"):
            self.assertIn("QQ 管理", QQBotMenuItem().render(request))
        # Other languages: English.
        with site_language("zh-hans"), translation.override("de"):
            self.assertIn("QQ Admin", QQBotMenuItem().render(request))


class CheckMessageTests(SimpleTestCase):
    def test_messages_are_english_then_chinese(self):
        """``manage.py check`` has no user language: each message and hint is
        English first, then the same in Chinese on the next line."""
        for check_id, message in checks._messages().items():
            for part, text in (("msg", message.msg), ("hint", message.hint)):
                with self.subTest(check=check_id, part=part):
                    english, chinese = text.split("\n")
                    self.assertIsNone(CJK_RE.search(english), english)
                    self.assertRegex(english, r"[A-Za-z]+ [a-z]+ [a-z]+")
                    self.assertRegex(chinese, HAN_RE)

    def test_same_in_any_language(self):
        with translation.override("zh-hans"):
            zh = {k: (m.msg, m.hint) for k, m in checks._messages().items()}
        with translation.override("en"):
            en = {k: (m.msg, m.hint) for k, m in checks._messages().items()}
        self.assertEqual(zh, en)


# --------------------------------------------------------------------------
# source scan: every visible Chinese text comes from the catalog
# --------------------------------------------------------------------------

# Python files (relative to qqbot/) that stay Chinese or bilingual on purpose.
PY_EXCLUDED_PREFIXES = (
    "api/",  # bot API JSON messages: no user language, read by ops in Chinese
    "management/",  # bilingual command output
    "migrations/",
    "tests/",
)
PY_EXCLUDED_FILES = {"checks.py"}  # bilingual system check messages
# (file, dotted name of the enclosing class/function/assignment) allowed to
# contain Chinese literals.
PY_EXCLUDED_SCOPES = {
    ("core/bindings.py", "claim"),  # bot API messages (see api/)
    ("core/bindings.py", "_claim"),
    ("core/util.py", "_NICKNAME_RE"),  # character ranges of the nickname regex
    ("models.py", "Config.DEFAULT_RULES"),  # default rules text (data)
    ("models.py", "General"),  # bilingual permission names
}


def _chinese_literals(path: Path, rel: str) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node, scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = scope + [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names:
                scope = scope + [names[0]]
        for i in range(1, len(scope) + 1):
            if (rel, ".".join(scope[:i])) in PY_EXCLUDED_SCOPES:
                return
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return  # docstring or bare string (a comment)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if HAN_RE.search(node.value):
                found.append((node.lineno, ".".join(scope) + ": " + node.value[:60]))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, [])
    return found


_TEMPLATE_IGNORED = [
    re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.S),
    re.compile(r"{#.*?#}", re.S),
    re.compile(r"{%\s*blocktrans(?:late)?\b.*?{%\s*endblocktrans(?:late)?\s*%}", re.S),
    re.compile(r"{%\s*trans(?:late)?\s.*?%}", re.S),
]


class SourceScanTests(SimpleTestCase):
    def test_no_chinese_literals_in_python(self):
        problems = []
        for path in sorted(PACKAGE_DIR.rglob("*.py")):
            rel = path.relative_to(PACKAGE_DIR).as_posix()
            if rel.startswith(PY_EXCLUDED_PREFIXES) or rel in PY_EXCLUDED_FILES:
                continue
            problems += [f"{rel}:{line} {text}" for line, text in _chinese_literals(path, rel)]
        self.assertEqual(problems, [], "Chinese UI text must come from the catalog")

    def test_scan_finds_chinese(self):
        """The scanner itself works (guards against a scan that sees nothing)."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write('"""文档。"""\n# 注释\nX = "中文"\ndef f():\n    """说明"""\n    return "好"\n')
        try:
            found = _chinese_literals(Path(f.name), "x.py")
        finally:
            os.unlink(f.name)
        self.assertEqual([line for line, _ in found], [3, 6])

    def test_no_chinese_outside_translation_tags_in_templates(self):
        problems = []
        for path in sorted((PACKAGE_DIR / "templates").rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for pattern in _TEMPLATE_IGNORED:
                text = pattern.sub("", text)
            for n, line in enumerate(text.splitlines(), 1):
                if CJK_RE.search(line):
                    problems.append(f"{path.relative_to(PACKAGE_DIR)}: {line.strip()}")
        self.assertEqual(problems, [], "Chinese UI text must come from the catalog")

    def test_templates_load_i18n(self):
        """Every template that translates loads the i18n library."""
        for path in sorted((PACKAGE_DIR / "templates").rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"{%\s*(translate|trans|blocktranslate|blocktrans)\b", text):
                with self.subTest(template=path.name):
                    self.assertRegex(text, r"{%\s*load\s+[^%]*\bi18n\b")
