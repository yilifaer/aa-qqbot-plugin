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
    """Mask a QQ number for logs and the services card. 9 or more digits keep
    the first and last two (``123456789`` -> ``12*****89``), 5-8 digits keep
    only the first and last one (``12345678`` -> ``1******8``), and 4 or fewer
    are fully masked.

    给 QQ 号打码，用于日志和服务页卡片。9 位及以上保留前 2 位和后 2 位
    （``123456789`` -> ``12*****89``），5–8 位只保留第一位和最后一位
    （``12345678`` -> ``1******8``），4 位及以下全部打码。
    """
    if qq is None:
        return ""
    qq = str(qq)
    if not qq:
        return ""
    if len(qq) <= 4:
        return "*" * len(qq)
    keep = 2 if len(qq) >= 9 else 1
    return qq[:keep] + "*" * (len(qq) - 2 * keep) + qq[-keep:]


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
