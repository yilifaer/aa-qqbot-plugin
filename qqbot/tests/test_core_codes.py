import hashlib
import hmac
import re

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from ..core.codes import CODE_ALPHABET, extract_code, generate_code, hash_code, normalize_code


class GenerateCodeTests(SimpleTestCase):
    def test_format(self):
        for _ in range(50):
            code = generate_code()
            self.assertRegex(code, r"^QQ-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}$")

    def test_alphabet_has_no_confusing_characters(self):
        for ch in "01ILO":
            self.assertNotIn(ch, CODE_ALPHABET)

    def test_random(self):
        self.assertGreater(len({generate_code() for _ in range(50)}), 45)


class HashCodeTests(SimpleTestCase):
    def test_matches_hmac(self):
        expected = hmac.new(
            settings.SECRET_KEY.encode(), b"qqbot-code:QQ-7K3F9P", hashlib.sha256
        ).hexdigest()
        self.assertEqual(hash_code("QQ-7K3F9P"), expected)

    def test_normalizes(self):
        h = hash_code("QQ-7K3F9P")
        self.assertEqual(hash_code("qq-7k3f9p"), h)
        self.assertEqual(hash_code("QQ7K3F9P"), h)
        self.assertEqual(hash_code(" qq7k3f9p "), h)
        self.assertNotEqual(hash_code("QQ-7K3F9Q"), h)

    def test_depends_on_secret_key(self):
        h = hash_code("QQ-7K3F9P")
        with override_settings(SECRET_KEY="another-secret-key-" + "y" * 40):
            self.assertNotEqual(hash_code("QQ-7K3F9P"), h)

    def test_normalize_code(self):
        self.assertEqual(normalize_code("qq7k3f9p"), "QQ-7K3F9P")
        self.assertEqual(normalize_code("QQ-7K3F9P"), "QQ-7K3F9P")


class ExtractCodeTests(SimpleTestCase):
    def test_exact(self):
        self.assertEqual(extract_code("QQ-7K3F9P"), "QQ-7K3F9P")

    def test_lowercase_and_missing_hyphen(self):
        self.assertEqual(extract_code("qq-7k3f9p"), "QQ-7K3F9P")
        self.assertEqual(extract_code("qq7k3f9p"), "QQ-7K3F9P")
        self.assertEqual(extract_code("Qq7K3f9p"), "QQ-7K3F9P")

    def test_surrounded_by_text(self):
        self.assertEqual(extract_code("验证码：QQ-7K3F9P，谢谢"), "QQ-7K3F9P")
        self.assertEqual(extract_code("你好qq7k3f9p我是凯拉"), "QQ-7K3F9P")
        self.assertEqual(extract_code("问题：来自哪里\n答案：qq-7k3f9p "), "QQ-7K3F9P")

    def test_first_match(self):
        self.assertEqual(extract_code("QQ-7K3F9P QQ-ABCDEF"), "QQ-7K3F9P")

    def test_prefers_standalone_code_over_qq_number(self):
        self.assertEqual(extract_code("qq23456789 验证码 QQ-7K3F9P"), "QQ-7K3F9P")

    def test_falls_back_to_glued_match(self):
        self.assertEqual(extract_code("codeQQ-7K3F9Pthanks"), "QQ-7K3F9P")

    def test_full_width_and_dash_look_alikes(self):
        # Chinese IMEs in full-width mode, phones and word processors.
        for text in (
            "ＱＱ－７Ｋ３Ｆ９Ｐ",
            "QQ－7K3F9P",
            "验证码：ｑｑ７ｋ３ｆ９ｐ",
            "QQ—7K3F9P",
            "QQ——7K3F9P",
            "QQ–7K3F9P",
            "QQ\u22127K3F9P",
            "QQ\u20107K3F9P",
        ):
            with self.subTest(text=text):
                self.assertEqual(extract_code(text), "QQ-7K3F9P")

    def test_no_code(self):
        for text in [None, "", "hello", "QQ-12345", "QQ-7K3F9", "7K3F9P", "QQ 7K3F9P", 123]:
            with self.subTest(text=text):
                self.assertIsNone(extract_code(text))

    def test_rejects_excluded_characters(self):
        self.assertIsNone(extract_code("QQ-OOOOOO"))
        self.assertIsNone(extract_code("QQ-111111"))

    def test_result_is_normalized(self):
        code = extract_code("xx qq7k3f9p")
        self.assertTrue(re.fullmatch(r"QQ-[A-Z0-9]{6}", code))
