from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from ..core.util import mask_qq, validate_nickname


class MaskQQTests(SimpleTestCase):
    def test_masks_middle(self):
        self.assertEqual(mask_qq("12345678"), "12****78")
        self.assertEqual(mask_qq("12345"), "12*45")
        self.assertEqual(mask_qq("12345678901"), "12*******01")

    def test_empty(self):
        self.assertEqual(mask_qq(""), "")
        self.assertEqual(mask_qq(None), "")

    def test_int_and_short(self):
        self.assertEqual(mask_qq(12345678), "12****78")
        self.assertEqual(mask_qq("123"), "***")


class ValidateNicknameTests(SimpleTestCase):
    def test_valid(self):
        for s in ["凯拉", "Kaela", "abc 123", "a_b-c.d", "张·三", "(tag)", "x", "一二三四五六七八九十一二"]:
            with self.subTest(s=s):
                self.assertEqual(validate_nickname(s), s)

    def test_strips(self):
        self.assertEqual(validate_nickname("  凯拉  "), "凯拉")

    def test_length(self):
        self.assertEqual(validate_nickname("a" * 12), "a" * 12)
        with self.assertRaises(ValidationError):
            validate_nickname("a" * 13)
        with self.assertRaises(ValidationError):
            validate_nickname("一" * 13)

    def test_empty(self):
        for s in ["", "   ", None]:
            with self.subTest(s=s), self.assertRaises(ValidationError):
                validate_nickname(s)

    def test_consecutive_spaces(self):
        with self.assertRaises(ValidationError):
            validate_nickname("a  b")

    def test_bad_characters(self):
        for s in ["<b>", "a&b", "😀", "カタ", "a\nb", "a\tb", "a/b", "ａｂ", "a'b"]:
            with self.subTest(s=s), self.assertRaises(ValidationError):
                validate_nickname(s)

    def test_message_is_chinese(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_nickname("<>")
        self.assertIn("昵称", ctx.exception.messages[0])
