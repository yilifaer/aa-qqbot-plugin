"""Signal receivers: what they schedule, and that they never raise."""

from contextlib import contextmanager
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.db import connection
from django.test import TestCase

from allianceauth.authentication.models import State, UserProfile
from allianceauth.authentication.signals import state_changed
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from allianceauth.tests.auth_utils import AuthUtils

from .. import signals
from ..core import events
from ..models import AuditLog, Binding, Event
from .utils import (
    add_to_groups,
    basic_access_permission,
    bind,
    create_group,
    create_member,
    member_state,
)

QQ = "12345678"


def keys(callbacks):
    """Keys of our callbacks (AA schedules on_commit callbacks of its own)."""
    found = [getattr(cb, signals._KEY_ATTR, None) for cb in callbacks]
    return [k for k in found if k is not None]


def user_key(user):
    return ("user", user.pk)


RECHECK_ALL = signals._RECHECK_ALL_KEY

# State.permissions points at AA's proxy of Permission.
StatePermission = State.permissions.field.related_model


def other_permission(model=Permission):
    return model.objects.exclude(codename="basic_access").order_by("pk").first()


@contextmanager
def aa_signals_off():
    """AA's own receivers (e.g. services.signals) choke on ``.clear()``
    (pk_set is None) and reassign states on profile saves; switch them off
    where a test only looks at ours."""
    AuthUtils.disconnect_signals()
    try:
        yield
    finally:
        AuthUtils.connect_signals()


def clear(manager):
    def run():
        with aa_signals_off():
            manager.clear()

    return run


def discard_pending():
    """Forget on_commit callbacks scheduled by the test setup (they would
    deduplicate the ones a test wants to see)."""
    connection.run_on_commit.clear()


class ScheduleTestCase(TestCase):
    def scheduled(self, action):
        """Run ``action`` and return the keys of the on_commit callbacks it scheduled."""
        discard_pending()
        with self.captureOnCommitCallbacks() as callbacks:
            action()
        return keys(callbacks)


class UserSignalTests(ScheduleTestCase):
    def setUp(self):
        self.user = create_member("alice")

    def test_state_changed_schedules_refresh(self):
        got = self.scheduled(
            lambda: state_changed.send(sender=UserProfile, user=self.user, state=None)
        )
        self.assertEqual(got, [user_key(self.user)])

    def test_real_state_change_schedules_refresh(self):
        other = State.objects.create(name="Other", priority=5)
        got = self.scheduled(lambda: self.user.profile.assign_state(other))
        self.assertIn(user_key(self.user), got)

    def test_deactivate_schedules_refresh(self):
        def deactivate():
            self.user.is_active = False
            self.user.save()

        got = self.scheduled(deactivate)
        # AA's own receiver also moves the user to the guest state; both
        # paths want the same refresh, which is scheduled once.
        self.assertEqual(got.count(user_key(self.user)), 1)

    def test_reactivate_schedules_refresh(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        user = User.objects.get(pk=self.user.pk)

        def activate():
            user.is_active = True
            user.save(update_fields=["is_active"])

        self.assertIn(user_key(user), self.scheduled(activate))

    def test_save_without_is_active_change_schedules_nothing(self):
        def save():
            self.user.first_name = "x"
            self.user.save()

        self.assertNotIn(user_key(self.user), self.scheduled(save))

    def test_groups_forward(self):
        group = Group.objects.create(name="Some group")
        got = self.scheduled(lambda: self.user.groups.add(group))
        self.assertEqual(got, [user_key(self.user)])
        got = self.scheduled(lambda: self.user.groups.remove(group))
        self.assertEqual(got, [user_key(self.user)])
        self.user.groups.add(group)
        got = self.scheduled(clear(self.user.groups))
        self.assertEqual(got, [user_key(self.user)])

    def test_groups_reverse_relevant_group(self):
        cap = Group.objects.create(name="Cap")
        create_group(200001, kind="role", required=[cap])
        bob = create_member("bob")
        got = self.scheduled(lambda: cap.user_set.add(self.user, bob))
        self.assertCountEqual(got, [user_key(self.user), user_key(bob)])
        got = self.scheduled(lambda: cap.user_set.remove(bob))
        self.assertEqual(got, [user_key(bob)])
        got = self.scheduled(clear(cap.user_set))
        self.assertEqual(got, [user_key(self.user)])

    def test_groups_reverse_irrelevant_group(self):
        group = Group.objects.create(name="Irrelevant")
        self.assertEqual(self.scheduled(lambda: group.user_set.add(self.user)), [])
        self.assertEqual(self.scheduled(clear(group.user_set)), [])

    def test_group_with_basic_access_is_relevant(self):
        group = Group.objects.create(name="Allies")
        group.permissions.add(basic_access_permission())
        got = self.scheduled(lambda: group.user_set.add(self.user))
        self.assertEqual(got, [user_key(self.user)])

    def test_user_permissions_forward(self):
        got = self.scheduled(lambda: self.user.user_permissions.add(basic_access_permission()))
        self.assertEqual(got, [user_key(self.user)])

    def test_user_permissions_reverse(self):
        perm = basic_access_permission()
        got = self.scheduled(lambda: perm.user_set.add(self.user))
        self.assertEqual(got, [user_key(self.user)])
        got = self.scheduled(clear(perm.user_set))
        self.assertEqual(got, [user_key(self.user)])
        other = other_permission()
        self.assertEqual(self.scheduled(lambda: other.user_set.add(self.user)), [])

    def test_main_character_change(self):
        char = EveCharacter.objects.create(
            character_id=91_000_001,
            character_name="New Main",
            corporation_id=2_100_001,
            corporation_name="New Corp",
            corporation_ticker="NEW",
        )
        profile = UserProfile.objects.get(user=self.user)

        def change_main():
            profile.main_character = char
            profile.save()

        self.assertIn(user_key(self.user), self.scheduled(change_main))

    def test_profile_save_without_change(self):
        profile = UserProfile.objects.get(user=self.user)

        def save(**kw):
            def run():
                with aa_signals_off():
                    profile.save(**kw)

            return run

        self.assertNotIn(user_key(self.user), self.scheduled(save(update_fields=["language"])))
        self.assertNotIn(user_key(self.user), self.scheduled(save()))

    def test_main_character_update(self):
        char = self.user.profile.main_character

        def rename():
            char.corporation_ticker = "ABC"
            char.save()

        self.assertIn(user_key(self.user), self.scheduled(rename))

    def test_non_main_character_update(self):
        char = EveCharacter.objects.create(
            character_id=91_000_002,
            character_name="Alt",
            corporation_id=2_100_002,
            corporation_name="Alt Corp",
            corporation_ticker="ALT",
        )

        def rename():
            char.character_name = "Alt 2"
            char.save()

        self.assertEqual(self.scheduled(rename), [])

    def test_dedup_within_transaction(self):
        group = Group.objects.create(name="G")

        def many():
            self.user.groups.add(group)
            self.user.user_permissions.add(basic_access_permission())
            state_changed.send(sender=UserProfile, user=self.user, state=None)

        self.assertEqual(self.scheduled(many), [user_key(self.user)])


class UserDeleteTests(TestCase):
    def test_delete_bound_user_writes_recheck_and_audit(self):
        user = create_member("alice")
        bind(user, QQ)
        with self.captureOnCommitCallbacks(execute=True):
            user.delete()
        self.assertTrue(Event.objects.filter(kind=Event.Kind.RECHECK, qq=QQ).exists())
        log = AuditLog.objects.get(action=AuditLog.Action.USER_DELETED)
        self.assertEqual((log.qq, log.target_name), (QQ, "alice"))
        self.assertFalse(Binding.objects.exists())

    def test_delete_survives_failure(self):
        user = create_member("alice")
        bind(user, QQ)
        with mock.patch.object(
            signals.bindings, "on_user_deleted", side_effect=RuntimeError("boom")
        ), self.assertLogs(signals.logger, "ERROR"):
            user.delete()
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_delete_survives_database_error(self):
        # A failed query inside the receiver must not poison the deleting
        # transaction (the receiver runs in its own savepoint).
        from django.db import connection as conn

        user = create_member("alice")
        bind(user, QQ)

        def broken(_user):
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM qqbot_no_such_table")

        with mock.patch.object(signals.bindings, "on_user_deleted", side_effect=broken), \
                self.assertLogs(signals.logger, "ERROR"):
            user.delete()
        self.assertFalse(User.objects.filter(username="alice").exists())
        self.assertFalse(Binding.objects.exists())


class AAProxyModelTests(ScheduleTestCase):
    """AA's admin works on proxy models (``authentication.User``,
    ``groupmanagement.Group``); AA re-sends their signals for the base
    models, so our receivers must fire exactly once."""

    def test_proxy_user_deactivate(self):
        from allianceauth.authentication.models import User as AAUser

        user = AAUser.objects.get(pk=create_member("alice").pk)

        def deactivate():
            user.is_active = False
            user.save()

        self.assertEqual(self.scheduled(deactivate).count(("user", user.pk)), 1)

    def test_proxy_user_delete_logged_once(self):
        from allianceauth.authentication.models import User as AAUser

        user = create_member("alice")
        bind(user, QQ)
        with self.captureOnCommitCallbacks(execute=True):
            AAUser.objects.get(pk=user.pk).delete()
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.USER_DELETED).count(), 1)
        self.assertEqual(Event.objects.filter(kind=Event.Kind.RECHECK, qq=QQ).count(), 1)

    def test_proxy_group_delete(self):
        from allianceauth.groupmanagement.models import Group as AAGroup

        cap = Group.objects.create(name="Cap")
        create_group(200001, kind="role", required=[cap])
        self.assertIn(RECHECK_ALL, self.scheduled(AAGroup.objects.get(pk=cap.pk).delete))

    def test_proxy_group_permissions(self):
        from allianceauth.groupmanagement.models import Group as AAGroup

        group = AAGroup.objects.create(name="Allies")
        got = self.scheduled(lambda: group.permissions.add(basic_access_permission()))
        self.assertEqual(got, [RECHECK_ALL])


class BulkSignalTests(ScheduleTestCase):
    def test_group_basic_access_permission(self):
        group = Group.objects.create(name="Allies")
        perm = basic_access_permission()
        self.assertEqual(self.scheduled(lambda: group.permissions.add(perm)), [RECHECK_ALL])
        self.assertEqual(self.scheduled(lambda: group.permissions.remove(perm)), [RECHECK_ALL])
        group.permissions.add(perm)
        self.assertEqual(self.scheduled(clear(group.permissions)), [RECHECK_ALL])
        # reverse side
        self.assertEqual(self.scheduled(lambda: perm.group_set.add(group)), [RECHECK_ALL])

    def test_group_other_permission(self):
        group = Group.objects.create(name="Other")
        perm = other_permission()
        self.assertEqual(self.scheduled(lambda: group.permissions.add(perm)), [])
        self.assertEqual(self.scheduled(clear(group.permissions)), [])
        self.assertEqual(self.scheduled(lambda: perm.group_set.add(group)), [])

    def test_state_basic_access_permission(self):
        state = State.objects.create(name="Blue2", priority=55)
        perm = basic_access_permission()
        self.assertEqual(self.scheduled(lambda: state.permissions.add(perm)), [RECHECK_ALL])
        self.assertEqual(self.scheduled(clear(state.permissions)), [RECHECK_ALL])
        proxy = StatePermission.objects.get(pk=perm.pk)
        self.assertEqual(self.scheduled(lambda: proxy.state_set.add(state)), [RECHECK_ALL])
        self.assertEqual(self.scheduled(lambda: state.permissions.add(other_permission(StatePermission))), [])

    def test_state_members_changed(self):
        state = member_state()
        corp = EveCorporationInfo.objects.create(
            corporation_id=2_200_001,
            corporation_name="Member Corp",
            corporation_ticker="MC",
            member_count=1,
        )
        got = self.scheduled(lambda: state.member_corporations.add(corp))
        self.assertIn(RECHECK_ALL, got)
        plain = State.objects.create(name="Plain", priority=5)
        got = self.scheduled(lambda: plain.member_corporations.add(corp))
        self.assertNotIn(RECHECK_ALL, got)

    def test_relevant_group_deleted(self):
        cap = Group.objects.create(name="Cap")
        create_group(200001, kind="role", required=[cap])
        self.assertIn(RECHECK_ALL, self.scheduled(cap.delete))
        other = Group.objects.create(name="Other")
        self.assertNotIn(RECHECK_ALL, self.scheduled(other.delete))

    def test_recheck_all_once_per_transaction(self):
        perm = basic_access_permission()
        g1 = Group.objects.create(name="A")
        g2 = Group.objects.create(name="B")

        def both():
            g1.permissions.add(perm)
            g2.permissions.add(perm)

        discard_pending()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            both()
        self.assertEqual(keys(callbacks), [RECHECK_ALL])
        self.assertEqual(Event.objects.filter(kind=Event.Kind.RECHECK_ALL).count(), 1)


class AfterCommitTests(TestCase):
    """End to end: the change is committed, then the bot gets an event."""

    def setUp(self):
        self.group = create_group(100001)
        self.user = create_member("alice")
        self.binding = bind(self.user, QQ)
        events.refresh_user(self.user)  # baseline fingerprint
        Event.objects.all().delete()
        discard_pending()

    def test_deactivating_user_emits_recheck(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.user.is_active = False
            self.user.save()
        self.assertEqual(
            list(Event.objects.values_list("kind", "qq")), [(Event.Kind.RECHECK, QQ)]
        )

    def test_role_group_membership_emits_recheck(self):
        cap = Group.objects.create(name="Cap")
        create_group(200001, kind="role", required=[cap])
        events.refresh_user(self.user)
        Event.objects.all().delete()
        discard_pending()
        with self.captureOnCommitCallbacks(execute=True):
            add_to_groups(self.user, cap)
        self.assertEqual(
            list(Event.objects.values_list("kind", "qq")), [(Event.Kind.RECHECK, QQ)]
        )

    def test_nothing_changed_no_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            state_changed.send(sender=UserProfile, user=self.user, state=None)
        self.assertFalse(Event.objects.exists())

    def test_refresh_failure_never_propagates(self):
        with mock.patch.object(
            signals.events, "refresh_user", side_effect=RuntimeError("boom")
        ), self.assertLogs(signals.logger, "ERROR"):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.user.is_active = False
                self.user.save()
        self.assertEqual(keys(callbacks), [user_key(self.user)])
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)

    def test_receiver_failure_never_propagates(self):
        with mock.patch.object(
            signals, "schedule_refresh_user", side_effect=RuntimeError("boom")
        ), self.assertLogs(signals.logger, "ERROR") as logs:
            self.user.is_active = False
            self.user.save()
            state_changed.send(sender=UserProfile, user=self.user, state=None)
            self.user.groups.add(Group.objects.create(name="G"))
            char = self.user.profile.main_character
            char.character_name = "Renamed"
            char.save()
        self.assertGreaterEqual(len(logs.records), 4)
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)

    def test_bulk_receiver_failure_never_propagates(self):
        group = Group.objects.create(name="G")
        with mock.patch.object(
            signals, "_basic_access_perm_id", side_effect=RuntimeError("boom")
        ), self.assertLogs(signals.logger, "ERROR"):
            group.permissions.add(basic_access_permission())
        self.assertTrue(group.permissions.exists())
