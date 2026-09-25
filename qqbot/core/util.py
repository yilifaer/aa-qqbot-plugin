"""Small helpers shared by the core modules.

core 各模块共用的小工具函数。
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext

NICKNAME_MAX_LENGTH = 12

# CJK unified ideographs (basic block, extension A and the supplementary
# extensions B-H), ASCII letters, digits, space and ``_-.·()``.
# 允许的字符：CJK 统一汉字（基本区、扩展 A 区和补充平面的扩展 B-H 区）、
# ASCII 英文字母、数字、空格以及 ``_-.·()``。
_NICKNAME_RE = re.compile(
    r"^[㐀-䶿一-鿿\U00020000-\U0003134F"
    r"A-Za-z0-9 _\-.·()]+$"
)


def mask_qq(qq) -> str:
    """Mask a QQ number for logs and the services card: ``12345678`` -> ``12****78``.

    给 QQ 号打码，用于日志和服务页卡片：``12345678`` -> ``12****78``。
    """
    if qq is None:
        return ""
    qq = str(qq)
    if not qq:
        return ""
    if len(qq) <= 4:
        return "*" * len(qq)
    return qq[:2] + "*" * (len(qq) - 4) + qq[-2:]


def validate_nickname(s) -> str:
    """Return the cleaned nickname or raise :class:`ValidationError`.

    返回清理后的昵称；昵称不合法时抛出 ``ValidationError``。
    """
    if s is None:
        s = ""
    if not isinstance(s, str):
        raise ValidationError(gettext("Invalid nickname."))
    s = s.strip()
    if not s:
        raise ValidationError(gettext("Please enter a nickname."))
    if len(s) > NICKNAME_MAX_LENGTH:
        raise ValidationError(
            gettext("Nickname can be at most %(max)d characters.") % {"max": NICKNAME_MAX_LENGTH}
        )
    if "  " in s:
        raise ValidationError(gettext("Nickname cannot contain two spaces in a row."))
    if not _NICKNAME_RE.match(s):
        raise ValidationError(
            gettext(
                "Nickname can only contain Chinese characters, letters, digits, spaces "
                "and _ - . · ( )."
            )
        )
    return s
