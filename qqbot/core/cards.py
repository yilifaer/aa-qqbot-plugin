"""Group card ("群名片") rendering."""

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
_FORMAT_ERRORS = (
    ValueError, KeyError, IndexError, AttributeError, TypeError, MemoryError, OverflowError,
)


def clean_card(s: str) -> str:
    """Strip and collapse whitespace (newlines and tabs become one space)."""
    return _WS_RE.sub(" ", s or "").strip()


def truncate_bytes(s: str, max_bytes: int = CARD_MAX_BYTES) -> str:
    """Cut ``s`` to at most ``max_bytes`` UTF-8 bytes without splitting a character."""
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
    then cut the whole string."""
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
    CARD_MAX_BYTES (not for overrides, which are always stored fitting)."""
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
    """The group card this binding's QQ should have."""
    return _card_for(binding.user, binding.nickname, binding.card_override, config)


def preview_card(user, nickname, config=None) -> str:
    """Card preview for the services card; no binding needed."""
    return _card_for(user, nickname or "", "", config)


def full_card(user, nickname, config=None) -> str:
    """The automatic card as formatted, before it is fitted into
    CARD_MAX_BYTES (for the services card's "your card was shortened" hint)."""
    return _card_for(user, nickname or "", "", config, fit=False)


def is_shortened(binding: Binding, config=None) -> bool:
    """True when this binding's automatic card had to be shortened
    (DESIGN.md 6: the page must say so). Manager overrides never count."""
    if binding.card_override and clean_card(binding.card_override):
        return False
    return byte_length(full_card(binding.user, binding.nickname, config)) > CARD_MAX_BYTES
