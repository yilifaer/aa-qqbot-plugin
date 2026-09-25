"""Small helpers shared by the core modules."""

import re

from django.core.exceptions import ValidationError

NICKNAME_MAX_LENGTH = 12

# CJK unified ideographs (basic block, extension A and the supplementary
# extensions B-H), ASCII letters, digits, space and ``_-.·()``.
_NICKNAME_RE = re.compile(
    r"^[㐀-䶿一-鿿\U00020000-\U0003134F"
    r"A-Za-z0-9 _\-.·()]+$"
)


def mask_qq(qq) -> str:
    """Mask a QQ number for logs and the services card: ``12345678`` -> ``12****78``."""
    if qq is None:
        return ""
    qq = str(qq)
    if not qq:
        return ""
    if len(qq) <= 4:
        return "*" * len(qq)
    return qq[:2] + "*" * (len(qq) - 4) + qq[-2:]


def validate_nickname(s) -> str:
    """Return the cleaned nickname or raise :class:`ValidationError`."""
    if s is None:
        s = ""
    if not isinstance(s, str):
        raise ValidationError("昵称格式不正确。")
    s = s.strip()
    if not s:
        raise ValidationError("请填写昵称。")
    if len(s) > NICKNAME_MAX_LENGTH:
        raise ValidationError(f"昵称最长 {NICKNAME_MAX_LENGTH} 个字。")
    if "  " in s:
        raise ValidationError("昵称中不能有连续的空格。")
    if not _NICKNAME_RE.match(s):
        raise ValidationError("昵称只能包含中文、英文字母、数字、空格和 _ - . · ( )。")
    return s
