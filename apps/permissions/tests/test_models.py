"""Pruebas de modelos del dominio de permisos."""
import uuid

import pytest
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.permissions.models import (
    ApprovalPolicy,
    AssignmentSource,
    PermissionAssignment,
    PermissionRestriction,
)

pytestmark = pytest.mark.django_db


def _make_user(identification):
    return User.objects.create(
        first_name="Ana",
        last_name="Pérez",
        identification=identification,
        password_hash=make_password("Secure1!"),
    )


def _make_membership(identification="12345678", role=FarmRole.ADMINISTRADOR):
    user = _make_user(identification)
    farm = Farm.objects.create(name="Finca")
    membership = FarmMembership.objects.create(farm=farm, user=user, role=role)
    return user, farm, membership


# --- PermissionAssignment ---


def test_assignment_creation():
    admin, _farm, membership = _make_membership()
    assignment = PermissionAssignment.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        source=AssignmentSource.ADMIN,
        granted_by=admin,
    )
    assert assignment.pk is not None
    assert assignment.membership_id == membership.pk
    assert assignment.module == "inventario"
    assert assignment.action == "crear"
    assert assignment.source == AssignmentSource.ADMIN
    assert assignment.migration_batch is None


def test_assignment_unique_membership_module_action():
    _admin, _farm, membership = _make_membership()
    PermissionAssignment.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        source=AssignmentSource.ADMIN,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PermissionAssignment.objects.create(
                membership=membership,
                module="inventario",
                action="crear",
                source=AssignmentSource.ADMIN,
            )


def test_assignment_source_values():
    assert set(AssignmentSource.values) == {
        "initialization",
        "admin",
        "invitation",
        "migration",
    }


def test_assignment_migration_batch_nullable():
    _admin, _farm, membership = _make_membership()
    batch = uuid.uuid4()
    assignment = PermissionAssignment.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        source=AssignmentSource.MIGRATION,
        migration_batch=batch,
    )
    assert assignment.migration_batch == batch


def test_assignment_cascade_on_membership_delete():
    _admin, _farm, membership = _make_membership()
    PermissionAssignment.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        source=AssignmentSource.ADMIN,
    )
    membership.delete()
    assert not PermissionAssignment.objects.filter(module="inventario").exists()


def test_assignment_granted_by_set_null_on_user_delete():
    admin = _make_user("11111111")
    member = _make_user("22222222")
    farm = Farm.objects.create(name="Finca")
    membership = FarmMembership.objects.create(
        farm=farm, user=member, role=FarmRole.OPERADOR
    )
    assignment = PermissionAssignment.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        source=AssignmentSource.ADMIN,
        granted_by=admin,
    )
    admin.delete()
    assignment.refresh_from_db()
    assert assignment.granted_by is None


# --- PermissionRestriction ---


def test_restriction_creation():
    admin, _farm, membership = _make_membership()
    restriction = PermissionRestriction.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    assert restriction.pk is not None
    assert restriction.membership_id == membership.pk


def test_restriction_unique_membership_module_action():
    admin, _farm, membership = _make_membership()
    PermissionRestriction.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PermissionRestriction.objects.create(
                membership=membership,
                module="inventario",
                action="crear",
                restricted_by=admin,
            )


def test_restriction_delete_and_recreate():
    admin, _farm, membership = _make_membership()
    restriction = PermissionRestriction.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    restriction.delete()
    recreated = PermissionRestriction.objects.create(
        membership=membership,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    assert recreated.pk is not None


def test_restriction_delete_does_not_affect_other_memberships():
    admin, _farm_a, membership_a = _make_membership("11111111")
    _other, _farm_b, membership_b = _make_membership("22222222")
    PermissionRestriction.objects.create(
        membership=membership_a,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    other = PermissionRestriction.objects.create(
        membership=membership_b,
        module="inventario",
        action="crear",
        restricted_by=admin,
    )
    PermissionRestriction.objects.filter(membership=membership_a).delete()
    assert PermissionRestriction.objects.filter(pk=other.pk).exists()


# --- ApprovalPolicy ---


def test_policy_creation_and_defaults():
    _user, farm, _membership = _make_membership()
    policy = ApprovalPolicy.objects.create(farm=farm, module="finanzas")
    assert policy.required is False
    assert policy.self_approval_allowed is False
    assert policy.updated_by is None


def test_policy_unique_farm_module():
    _user, farm, _membership = _make_membership()
    ApprovalPolicy.objects.create(farm=farm, module="finanzas")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ApprovalPolicy.objects.create(farm=farm, module="finanzas")


def test_policy_cascade_on_farm_delete():
    _user, farm, _membership = _make_membership()
    ApprovalPolicy.objects.create(farm=farm, module="finanzas")
    farm.delete()
    assert not ApprovalPolicy.objects.filter(module="finanzas").exists()
