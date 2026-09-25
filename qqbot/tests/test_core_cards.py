from unittest import mock

from django.test import TestCase

from ..core import cards
from ..core.cards import CARD_MAX_BYTES, preview_card, render_card
from ..models import Config
from .utils import bind, create_member, create_user, member_state


def nbytes(s):
    return len(s.encode("utf-8"))


class RenderCardTests(TestCase):
    def setUp(self):
        self.config = Config.get_solo()
        self.user = create_member("alice", character_name="Kaela Voss", corp_ticker="IGC",
                                  alliance_ticker="ALLY")
        self.binding = bind(self.user, "12345678", nickname="凯拉")

    def set_format(self, fmt):
        self.config.card_format = fmt
        self.config.save()

    def test_constant(self):
        self.assertEqual(CARD_MAX_BYTES, 60)

    def test_default_format(self):
        self.assertEqual(render_card(self.binding), "[IGC] Kaela Voss - 凯拉")

    def test_all_placeholders(self):
        self.set_format("{alliance_ticker}/{corp_ticker} {character_name} ({nickname})")
        self.assertEqual(render_card(self.binding), "ALLY/IGC Kaela Voss (凯拉)")

    def test_config_argument(self):
        cfg = Config(card_format="{nickname}")
        self.assertEqual(render_card(self.binding, cfg), "凯拉")

    def test_override(self):
        self.binding.card_override = "  管理员 指定  "
        self.assertEqual(render_card(self.binding), "管理员 指定")

    def test_whitespace_collapsed(self):
        self.set_format("  [{corp_ticker}]    {character_name}\t-  {nickname}  ")
        self.assertEqual(render_card(self.binding), "[IGC] Kaela Voss - 凯拉")

    def test_empty_values_collapse(self):
        self.set_format("{alliance_ticker} {corp_ticker} {nickname}")
        self.binding.user.profile.main_character.alliance_ticker = ""
        self.assertEqual(render_card(self.binding), "IGC 凯拉")

    def test_bad_formats_fall_back(self):
        for fmt in ["{unknown}", "{corp_ticker", "corp}", "{}", "{0}", "{nickname.__class__}",
                    "{nickname[0]}", "{nickname:{corp_ticker}}", "{nickname:d}"]:
            with self.subTest(fmt=fmt):
                self.set_format(fmt)
                with self.assertLogs(cards.logger, "WARNING"):
                    self.assertEqual(render_card(self.binding), "[IGC] Kaela Voss - 凯拉")

    def test_format_spec_and_conversion_rejected(self):
        """A width spec could make every render allocate megabytes (or raise
        MemoryError past the fallback); no spec or conversion is accepted."""
        for fmt in ["{nickname:>999999999999}", "{nickname:x>9999999999999}",
                    "[{corp_ticker}] {character_name} - {nickname:x>10000000}",
                    "{nickname:>5}", "{nickname:.3}", "{nickname!r}", "{nickname!s}"]:
            with self.subTest(fmt=fmt):
                self.assertFalse(cards.format_is_valid(fmt))
                self.set_format(fmt)
                with self.assertLogs(cards.logger, "WARNING"):
                    self.assertEqual(render_card(self.binding), "[IGC] Kaela Voss - 凯拉")

    def test_memory_error_falls_back(self):
        real = cards._render
        calls = []

        def boom(fmt, values):
            calls.append(fmt)
            if len(calls) == 1:
                raise MemoryError
            return real(fmt, values)

        self.set_format("{nickname}")
        with mock.patch.object(cards, "_render", boom), self.assertLogs(cards.logger, "WARNING"):
            self.assertEqual(render_card(self.binding), "[IGC] Kaela Voss - 凯拉")
        self.assertEqual(calls, ["{nickname}", Config.DEFAULT_CARD_FORMAT])

    def test_format_is_valid(self):
        self.assertTrue(cards.format_is_valid(Config.DEFAULT_CARD_FORMAT))
        self.assertTrue(cards.format_is_valid("plain text"))
        self.assertTrue(cards.format_is_valid("{{literal}} {nickname}"))
        self.assertFalse(cards.format_is_valid("{foo}"))

    def test_no_main_character(self):
        user = create_user("nomain", main=False, state=member_state())
        binding = bind(user, "22345678", nickname="n")
        self.assertEqual(render_card(binding), "[] - n")

    def test_long_character_name_is_shortened_first(self):
        user = create_member("long", character_name="A" * 37, corp_ticker="ABCDE")
        binding = bind(user, "22345678", nickname="一二三四五六七八九十一二")  # 36 bytes
        card = render_card(binding)
        self.assertLessEqual(nbytes(card), CARD_MAX_BYTES)
        self.assertTrue(card.startswith("[ABCDE] A"))
        self.assertTrue(card.endswith(" - 一二三四五六七八九十一二"))
        # "[ABCDE] " (8) + name + " - " (3) + 36 = 60 -> 13 chars of name
        self.assertEqual(card, "[ABCDE] " + "A" * 13 + " - 一二三四五六七八九十一二")

    def test_multibyte_character_name_is_not_split(self):
        user = create_member("mb", character_name="长" * 20)  # 60 bytes
        binding = bind(user, "22345678", nickname="凯拉")
        card = render_card(binding)
        self.assertLessEqual(nbytes(card), CARD_MAX_BYTES)
        card.encode("utf-8").decode("utf-8")  # still valid
        self.assertTrue(card.startswith("[IGC] 长"))
        self.assertTrue(card.endswith(" - 凯拉"))
        name = card[len("[IGC] "):-len(" - 凯拉")]
        self.assertEqual(set(name), {"长"})
        # 6 + 3n + 9 <= 60 -> n = 15
        self.assertEqual(len(name), 15)

    def test_whole_string_truncated_without_splitting(self):
        # 1 + 25*3 = 76 bytes of literal text, no character name to shorten.
        self.set_format("a" + "中" * 25 + "{nickname}")
        card = render_card(self.binding)
        self.assertEqual(card, "a" + "中" * 19)
        self.assertEqual(nbytes(card), 58)

    def test_override_is_limited_too(self):
        self.binding.card_override = "中" * 25
        card = render_card(self.binding)
        self.assertEqual(card, "中" * 20)

    def test_short_card_untouched(self):
        self.assertLessEqual(nbytes(render_card(self.binding)), CARD_MAX_BYTES)


class PreviewCardTests(TestCase):
    def test_preview_without_binding(self):
        user = create_member("alice", character_name="Kaela Voss")
        self.assertEqual(preview_card(user, "凯拉"), "[IGC] Kaela Voss - 凯拉")
        self.assertEqual(preview_card(user, ""), "[IGC] Kaela Voss -")

    def test_preview_uses_config(self):
        user = create_member("alice", character_name="Kaela Voss")
        self.assertEqual(preview_card(user, "x", Config(card_format="{character_name}|{nickname}")),
                         "Kaela Voss|x")
