"""Group card ("群名片") rendering.

群名片的生成。
"""

import re
import string

from allianceauth.authentication.models import UserProfile
from allianceauth.services.hooks import get_extension_logger

from ..models import Binding, Config

logger = get_extension_logger(__name__)

CARD_MAX_BYTES = 60
PLACEHOLDERS = ("corp_ticker", "alliance_ticker", "character_name", "nickname")

_WS_RE = re.compile(r"\s+")

# Anything that can go wrong while formatting a (manager-controlled) format
# string. MemoryError / OverflowError cover absurd widths, should one ever get
# past format_is_valid().
# 格式化（由管理员设置的）格式字符串时可能出现的所有错误。万一有离谱的
# 宽度设置绕过了 format_is_valid()，MemoryError / OverflowError 也能兜住。
_FORMAT_ERRORS = (
    ValueError, KeyError, IndexError, AttributeError, TypeError, MemoryError, OverflowError,
)


def clean_card(s: str) -> str:
    """Strip and collapse whitespace (newlines and tabs become one space).

    去掉首尾空白并合并连续空白（换行和制表符都变成一个空格）。
    """
    return _WS_RE.sub(" ", s or "").strip()


def truncate_bytes(s: str, max_bytes: int = CARD_MAX_BYTES) -> str:
    """Cut ``s`` to at most ``max_bytes`` UTF-8 bytes without splitting a character.

    把 ``s`` 截到最多 ``max_bytes`` 个 UTF-8 字节，不会把一个字符截成两半。
    """
    raw = s.encode("utf-8")
    if len(raw) <= max_bytes:
        return s
    return raw[:max_bytes].decode("utf-8", "ignore")


def byte_length(s: str) -> int:
    return len((s or "").encode("utf-8"))


def format_is_valid(fmt: str) -> bool:
    """True when ``fmt`` only uses the known placeholders, as bare
    ``{name}`` fields, and formats without error.

    Rejected: unknown or positional fields, attribute / index access, and any
    conversion (``!r``) or format spec (``:>20``). A width spec lets a
    manager-set format blow every card up to megabytes or raise MemoryError,
    and the card is cut to 60 bytes anyway, so no spec is useful.

    只有当 ``fmt`` 只用到已知的占位符、都写成简单的 ``{name}`` 形式、
    并且能正常格式化时，才返回 True。

    会被拒绝的写法：未知字段或按位置的字段、属性 / 下标访问，以及任何转换
    （``!r``）或格式说明（``:>20``）。宽度说明可能让管理员设置的格式把每张
    群名片撑到几 MB，或者直接抛出 MemoryError；而群名片反正会被截到 60 字节，
    所以格式说明没有任何用处。
    """
    try:
        for _literal, field, spec, conv in string.Formatter().parse(fmt):
            if field is None:
                continue
            if field not in PLACEHOLDERS or spec or conv:
                return False
        fmt.format(**{name: "x" for name in PLACEHOLDERS})
    except _FORMAT_ERRORS:
        return False
    return True


def _main_character(user):
    if user is None:
        return None
    try:
        return user.profile.main_character
    except UserProfile.DoesNotExist:
        return None


def _values(user, nickname: str) -> dict:
    main = _main_character(user)
    return {
        "corp_ticker": (getattr(main, "corporation_ticker", "") or "") if main else "",
        "alliance_ticker": (getattr(main, "alliance_ticker", "") or "") if main else "",
        "character_name": (main.character_name or "") if main else "",
        "nickname": nickname or "",
    }


def _render(fmt: str, values: dict) -> str:
    """Format and fit into CARD_MAX_BYTES: shorten the character name first,
    then cut the whole string.

    格式化并压缩到 CARD_MAX_BYTES 以内：先缩短角色名，还不够再截断整个字符串。
    """
    card = clean_card(fmt.format(**values))
    if byte_length(card) <= CARD_MAX_BYTES:
        return card
    name = values["character_name"]
    while name:
        name = name[:-1]
        card = clean_card(fmt.format(**{**values, "character_name": name.rstrip()}))
        if byte_length(card) <= CARD_MAX_BYTES:
            return card
    return truncate_bytes(card).strip()


def _card_for(user, nickname: str, card_override: str, config, *, fit=True) -> str:
    """The card; with ``fit=False`` the formatted card *before* it is cut to
    CARD_MAX_BYTES (not for overrides, which are always stored fitting).

    返回群名片；``fit=False`` 时返回截到 CARD_MAX_BYTES *之前* 的格式化结果
    （对手动覆盖的群名片不起作用，它们保存时就已经符合长度）。
    """
    if card_override and clean_card(card_override):
        return truncate_bytes(clean_card(card_override)).strip()
    if config is None:
        config = Config.get_solo()
    fmt = config.card_format or ""
    values = _values(user, nickname)
    render = _render if fit else (lambda f, v: clean_card(f.format(**v)))
    if fmt and format_is_valid(fmt):
        try:
            return render(fmt, values)
        except _FORMAT_ERRORS:
            pass
    logger.warning("qqbot: invalid card format %r, falling back to the default", fmt)
    return render(Config.DEFAULT_CARD_FORMAT, values)


def render_card(binding: Binding, config=None) -> str:
    """The group card this binding's QQ should have.

    这条绑定的 QQ 应该使用的群名片。
    """
    return _card_for(binding.user, binding.nickname, binding.card_override, config)


def preview_card(user, nickname, config=None) -> str:
    """Card preview for the services card; no binding needed.

    服务页面卡片上显示的群名片预览；不需要有绑定。
    """
    return _card_for(user, nickname or "", "", config)


def full_card(user, nickname, config=None) -> str:
    """The automatic card as formatted, before it is fitted into
    CARD_MAX_BYTES (for the services card's "your card was shortened" hint).

    按格式生成、但还没压缩到 CARD_MAX_BYTES 的自动群名片
    （用于服务页面卡片上“你的群名片已被缩短”的提示）。
    """
    return _card_for(user, nickname or "", "", config, fit=False)


def is_shortened(binding: Binding, config=None) -> bool:
    """True when this binding's automatic card had to be shortened
    (DESIGN.md 6: the page must say so). Manager overrides never count.

    当这条绑定的自动群名片不得不被缩短时返回 True（DESIGN.md 6：页面上
    必须提示）。管理员手动设置的群名片永远不算。
    """
    if binding.card_override and clean_card(binding.card_override):
        return False
    return byte_length(full_card(binding.user, binding.nickname, config)) > CARD_MAX_BYTES
