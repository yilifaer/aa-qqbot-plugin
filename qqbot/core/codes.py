"""One-time verification codes (``QQ-XXXXXX``).

Only an HMAC of a code is stored in the database (``BindCode.code_hash``).
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
_STRICT_RE = re.compile(
    rf"(?<![0-9A-Za-z])QQ-?({_BODY})(?![0-9A-Za-z])", re.IGNORECASE | re.ASCII
)
# Fallback: the plain pattern from the spec, anywhere in the text.
_LOOSE_RE = re.compile(rf"QQ-?({_BODY})", re.IGNORECASE | re.ASCII)


# Dash look-alikes that IMEs and phones produce instead of "-". NFKC already
# maps the full-width hyphen-minus (U+FF0D) and the small one (U+FE63).
_DASHES = str.maketrans({
    c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\uff70"
})

_DASH_RUN_RE = re.compile(r"-{2,}")


def _normalize_text(text: str) -> str:
    """Fold full-width letters/digits/hyphens (Chinese IME full-width mode)
    and common dash look-alikes to ASCII before matching."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    # A Chinese IME's "-" key often gives a double dash ("——").
    return _DASH_RUN_RE.sub("-", text)


def generate_code() -> str:
    """Return a new random code such as ``QQ-7K3F9P``."""
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    """Upper-case the code and insert the hyphen after ``QQ`` when missing."""
    code = (code or "").strip().upper()
    if code.startswith("QQ") and not code.startswith(CODE_PREFIX):
        code = CODE_PREFIX + code[2:]
    return code


def hash_code(code: str) -> str:
    """HMAC-SHA256 (keyed with ``SECRET_KEY``) of the normalized code, hex."""
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
    """
    if not text or not isinstance(text, str):
        return None
    text = _normalize_text(text)
    match = _STRICT_RE.search(text) or _LOOSE_RE.search(text)
    if not match:
        return None
    return CODE_PREFIX + match.group(1).upper()
