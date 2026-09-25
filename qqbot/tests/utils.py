"""Shared test helpers (docs/SPEC.md 3.10)."""

from django.contrib.auth.models import Group
from django.utils import timezone

from allianceauth.authentication.models import State
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from ..models import Binding, QQGroup, RosterEntry

BASIC_ACCESS = "qqbot.basic_access"
MANAGE = "qqbot.manage"

_CHARACTER_ID_BASE = 90_000_000


def basic_access_permission():
    return AuthUtils.get_permission_by_name(BASIC_ACCESS)


def member_state() -> State:
    """AA's Member state, with basic_access attached (as in production)."""
    state = AuthUtils.get_member_state()
    AuthUtils.disconnect_signals()
    try:
        state.permissions.add(basic_access_permission())
    finally:
        AuthUtils.connect_signals()
    return state


def no_access_state() -> State:
    """A state without basic_access."""
    try:
        return State.objects.get(name="NoQQ")
    except State.DoesNotExist:
        return AuthUtils.create_state("NoQQ", 10, disconnect_signals=True)


def create_user(username, *, main=True, corp_ticker="IGC", character_name=None,
                alliance_ticker="", state=None, active=True, superuser=False):
    """A user with (optionally) a main character, in ``state``; AA's signals
    are disconnected so the state is not recomputed."""
    user = AuthUtils.create_user(username, disconnect_signals=True)
    AuthUtils.disconnect_signals()
    try:
        if main:
            char = EveCharacter.objects.create(
                character_id=_CHARACTER_ID_BASE + user.pk,
                character_name=character_name or username,
                corporation_id=2000_000 + user.pk,
                corporation_name=f"{corp_ticker} Corp",
                corporation_ticker=corp_ticker,
                alliance_ticker=alliance_ticker,
            )
            user.profile.main_character = char
        if state is not None:
            user.profile.state = state
        user.profile.save()
        changed = []
        if not active:
            user.is_active = False
            changed.append("is_active")
        if superuser:
            user.is_superuser = True
            user.is_staff = True
            changed += ["is_superuser", "is_staff"]
        if changed:
            user.save(update_fields=changed)
    finally:
        AuthUtils.connect_signals()
    return type(user).objects.select_related("profile__main_character").get(pk=user.pk)


def create_member(username, *, corp_ticker="IGC", character_name=None, state_perm=True,
                  active=True, alliance_ticker=""):
    """A member with a main character. With ``state_perm`` the user is in the
    Member state carrying ``basic_access``; otherwise in a state without it."""
    state = member_state() if state_perm else no_access_state()
    return create_user(
        username,
        corp_ticker=corp_ticker,
        character_name=character_name,
        alliance_ticker=alliance_ticker,
        state=state,
        active=active,
    )


def add_to_groups(user, *groups):
    AuthUtils.disconnect_signals()
    try:
        for g in groups:
            if isinstance(g, str):
                g, _ = Group.objects.get_or_create(name=g)
            user.groups.add(g)
    finally:
        AuthUtils.connect_signals()


def create_group(group_id, kind="fixed", required=(), **kw):
    """A managed QQ group. ``required`` may hold ``Group`` objects or names."""
    kw.setdefault("name", f"群{group_id}")
    group = QQGroup.objects.create(group_id=str(group_id), kind=kind, **kw)
    for g in required:
        if isinstance(g, str):
            g, _ = Group.objects.get_or_create(name=g)
        group.required_groups.add(g)
    return group


def bind(user, qq, status="verified", nickname="n", **kw):
    """Create a binding directly (no events, no audit)."""
    if status == Binding.Status.VERIFIED:
        kw.setdefault("verified_via", Binding.VerifiedVia.CODE)
        kw.setdefault("verified_at", timezone.now())
    return Binding.objects.create(user=user, qq=str(qq), status=status, nickname=nickname, **kw)


def put_in_roster(group, qqs, now=None):
    """Add ``qqs`` to the group's roster (additively) and mark the roster fresh."""
    now = now or timezone.now()
    RosterEntry.objects.bulk_create(
        [RosterEntry(group=group, qq=str(qq), seen_at=now) for qq in qqs],
        ignore_conflicts=True,
    )
    QQGroup.objects.filter(pk=group.pk).update(last_roster_at=now)
    group.last_roster_at = now
    return group
