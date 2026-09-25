from django.contrib.auth.models import AnonymousUser, Group
from django.test import TestCase

from allianceauth.tests.auth_utils import AuthUtils

from ..core.access import base_access_user_ids, has_base_access, user_group_ids
from .utils import (
    add_to_groups,
    basic_access_permission,
    create_member,
    create_user,
    no_access_state,
)


class HasBaseAccessTests(TestCase):
    def test_permission_via_state(self):
        user = create_member("alice")
        self.assertTrue(has_base_access(user))

    def test_no_permission(self):
        user = create_member("bob", state_perm=False)
        self.assertFalse(has_base_access(user))

    def test_permission_via_group(self):
        user = create_member("bob", state_perm=False)
        group = Group.objects.create(name="QQ Members")
        group.permissions.add(basic_access_permission())
        add_to_groups(user, group)
        self.assertTrue(has_base_access(user))

    def test_permission_via_user(self):
        user = create_member("bob", state_perm=False)
        AuthUtils.add_permissions_to_user([basic_access_permission()], user)
        self.assertTrue(has_base_access(user))

    def test_other_permission_does_not_count(self):
        user = create_member("bob", state_perm=False)
        AuthUtils.add_permission_to_user_by_name("qqbot.manage", user)
        self.assertFalse(has_base_access(user))

    def test_superuser_without_explicit_permission(self):
        user = create_user("root", state=no_access_state(), superuser=True)
        self.assertTrue(user.has_perm("qqbot.basic_access"))  # the trap
        self.assertFalse(has_base_access(user))

    def test_superuser_with_explicit_permission(self):
        user = create_user("root", state=no_access_state(), superuser=True)
        AuthUtils.add_permissions_to_user([basic_access_permission()], user)
        self.assertTrue(has_base_access(user))

    def test_inactive(self):
        user = create_member("alice", active=False)
        self.assertFalse(has_base_access(user))

    def test_no_main_character(self):
        from .utils import member_state

        user = create_user("alice", main=False, state=member_state())
        self.assertFalse(has_base_access(user))

    def test_anonymous_and_none(self):
        self.assertFalse(has_base_access(AnonymousUser()))
        self.assertFalse(has_base_access(None))


class BatchTests(TestCase):
    def test_base_access_user_ids(self):
        a = create_member("a")
        b = create_member("b", state_perm=False)
        c = create_member("c", state_perm=False)
        d = create_member("d", active=False)  # batch version ignores is_active
        group = Group.objects.create(name="G")
        group.permissions.add(basic_access_permission())
        add_to_groups(c, group)
        self.assertEqual(base_access_user_ids([a.pk, b.pk, c.pk, d.pk]), {a.pk, c.pk, d.pk})
        self.assertEqual(base_access_user_ids([]), set())

    def test_constant_queries(self):
        create_member("a")
        with self.assertNumQueries(1):
            base_access_user_ids(range(1, 11))
        with self.assertNumQueries(1):
            base_access_user_ids(range(1, 501))
        with self.assertNumQueries(2):  # chunks of 500
            base_access_user_ids(range(1, 502))

    def test_user_group_ids(self):
        a = create_member("a")
        b = create_member("b")
        g1 = Group.objects.create(name="G1")
        g2 = Group.objects.create(name="G2")
        add_to_groups(a, g1, g2)
        with self.assertNumQueries(1):
            result = user_group_ids([a.pk, b.pk])
        self.assertEqual(result, {a.pk: {g1.pk, g2.pk}, b.pk: set()})
