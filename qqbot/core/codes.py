"""One-time verification codes (``QQ-XXXXXX``).

Only an HMAC of a code is stored in the database (``BindCode.code_hash``).

一次性验证码（``QQ-XXXXXX``）。

数据库里只存验证码的 HMAC（``BindCode.code_hash``）。
"""

import hashlib
import hmac
import re
import secrets
import unicodedata

from django.conf import settings

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 6
CODE_PREFIX = "QQ-"

_BODY = f"[{CODE_ALPHABET}]{{{CODE_LENGTH}}}"
# Preferred: a code that is not glued to other ASCII letters/digits.
# 优先：前后没有紧贴其他 ASCII 字母 / 数字的验证码。
_STRICT_RE = re.compile(
    rf"(?<![0-9A-Za-z])QQ-?({_BODY})(?![0-9A-Za-z])", re.IGNORECASE | re.ASCII
)
# Fallback: the plain pattern from the spec, anywhere in the text.
# 兜底：规范里的普通模式，可以出现在文本的任何位置。
_LOOSE_RE = re.compile(rf"QQ-?({_BODY})", re.IGNORECASE | re.ASCII)


# Dash look-alikes that IMEs and phones produce instead of "-". NFKC already
# maps the full-width hyphen-minus (U+FF0D) and the small one (U+FE63).
# 输入法和手机常常打出来代替 "-" 的各种类似横线的字符。全角连字符（U+FF0D）
# 和小号连字符（U+FE63）NFKC 本身已经会转换。
_DASHES = str.maketrans({
    c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\uff70"
})

_DASH_RUN_RE = re.compile(r"-{2,}")


def _normalize_text(text: str) -> str:
    """Fold full-width letters/digits/hyphens (Chinese IME full-width mode)
    and common dash look-alikes to ASCII before matching.

    匹配之前，先把全角字母 / 数字 / 连字符（中文输入法全角模式）和常见的
    类似横线的字符都转换成 ASCII。
    """
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    # A Chinese IME's "-" key often gives a double dash ("——").
    # 中文输入法按 "-" 键经常打出双破折号（"——"）。
    return _DASH_RUN_RE.sub("-", text)


def generate_code() -> str:
    """Return a new random code such as ``QQ-7K3F9P``.

    返回一个新的随机验证码，例如 ``QQ-7K3F9P``。
    """
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    """Upper-case the code and insert the hyphen after ``QQ`` when missing.

    把验证码转成大写，如果 ``QQ`` 后面缺少连字符就补上。
    """
    code = (code or "").strip().upper()
    if code.startswith("QQ") and not code.startswith(CODE_PREFIX):
        code = CODE_PREFIX + code[2:]
    return code


def hash_code(code: str) -> str:
    """HMAC-SHA256 (keyed with ``SECRET_KEY``) of the normalized code, hex.

    规范化后的验证码的 HMAC-SHA256（密钥为 ``SECRET_KEY``），十六进制字符串。
    """
    message = ("qqbot-code:" + normalize_code(code)).encode("utf-8")
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def extract_code(text) -> str | None:
    """Find the first code in free text (e.g. a join-request comment).

    Case-insensitive, the hyphen is optional and other text may surround the
    code. A match that is not glued to other ASCII letters/digits is preferred
    (so ``qq23456789 QQ-7K3F9P`` yields the real code); otherwise the first
    plain match is used. Full-width input (``ＱＱ－７Ｋ３Ｆ９Ｐ``) and dash
    look-alikes (``QQ—7K3F9P``) are accepted too.

    在任意文本（例如加群申请的附言）里找出第一个验证码。

    不区分大小写，连字符可有可无，验证码前后可以有其他文字。优先使用前后
    没有紧贴其他 ASCII 字母 / 数字的匹配（所以 ``qq23456789 QQ-7K3F9P``
    得到的是真正的验证码）；找不到再用第一个普通匹配。全角输入
    （``ＱＱ－７Ｋ３Ｆ９Ｐ``）和类似横线的字符（``QQ—7K3F9P``）也能识别。
    """
    if not text or not isinstance(text, str):
        return None
    text = _normalize_text(text)
    match = _STRICT_RE.search(text) or _LOOSE_RE.search(text)
    if not match:
        return None
    return CODE_PREFIX + match.group(1).upper()
