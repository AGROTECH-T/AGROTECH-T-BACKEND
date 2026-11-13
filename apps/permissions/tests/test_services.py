"""Pruebas de los servicios centrales de autorización."""
import pytest
from django.contrib.auth.hashers import make_password

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms.models import Farm, FarmMembership, FarmRole, MembershipStatus
from apps.permissions import services as permission_services
from apps.permissions.catalog import Permission
from apps.permissions.models import (
    AssignmentSource,
    PermissionAssignment,
    PermissionRestriction,
)
from apps.permissions.services import assigned, effective, has, restricted
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _make_user(identification, active=True):
    return User.objects.create(
        first_name="Ana",
        last_name="Pérez",
        identification=identification,
        password_hash=make_password("Secure1!"),
        is_active=active,
    )


def _make_membership(identification="12345678", role=FarmRole.ADMINISTRADOR):
    user = _make_user(identification)
    farm = Farm.objects.create(name="Finca")
    membership = FarmMembership.objects.create(farm=farm, user=user, role=role)
    return user, farm, membership


def _grant(membership, module, action):
    return PermissionAssignment.objects.create(
        membership=membership,
        module=module,
        action=action,
        source=AssignmentSource.ADMIN,
    )


def _restrict(membership, module, action):
    return PermissionRestriction.objects.create(
        membership=membership, module=module, action=action
    )


# --- Cálculo ---


def test_assigned_returns_explicit_assignments():
    _user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    assert assigned(membership) == {"inventario.crear"}


def test_restricted_returns_active_restrictions():
    _user, _farm, membership = _make_membership()
    _restrict(membership, "inventario", "crear")
    assert restricted(membership) == {"inventario.crear"}


def test_effective_intersects_ceiling_and_assigned():
    _user, _farm, membership = _make_membership(role=FarmRole.OPERADOR)
    _grant(membership, "inventario", "crear")
    _grant(membership, "inventario", "consultar")
    assert effective(membership) == {"inventario.crear", "inventario.consultar"}


def test_effective_subtracts_restrictions():
    _user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    _restrict(membership, "inventario", "crear")
    assert effective(membership) == set()


def test_effective_denies_without_assignment():
    _user, _farm, membership = _make_membership()
    assert effective(membership) == set()


def test_effective_denies_outside_ceiling():
    _user, _farm, membership = _make_membership(role=FarmRole.CONSULTA)
    _grant(membership, "inventario", "crear")  # fuera del techo de CONSULTATION
    assert "inventario.crear" not in effective(membership)


def test_has_true_for_effective():
    _user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    assert has(membership, "inventario", "crear") is True


def test_has_false_for_missing():
    _user, _farm, membership = _make_membership()
    assert has(membership, "inventario", "crear") is False


def test_has_false_for_restricted():
    _user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    _restrict(membership, "inventario", "crear")
    assert has(membership, "inventario", "crear") is False


# --- Denegación por estado ---


def test_effective_denies_inactive_farm():
    _user, farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    farm.is_active = False
    farm.save()
    assert effective(membership) == set()


def test_effective_denies_inactive_membership():
    _user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    membership.status = MembershipStatus.REVOKED
    membership.save()
    assert effective(membership) == set()


def test_effective_denies_inactive_user():
    user, _farm, membership = _make_membership()
    _grant(membership, "inventario", "crear")
    user.is_active = False
    user.save()
    assert effective(membership) == set()


# --- Aislamiento multi-finca ---


def test_isolation_between_farms():
    _user1, _farm1, membership1 = _make_membership("11111111")
    _user2, _farm2, membership2 = _make_membership("22222222")
    _grant(membership1, "inventario", "crear")
    assert has(membership1, "inventario", "crear") is True
    assert has(membership2, "inventario", "crear") is False


# --- Escritura: helpers ---


def _make_actor_and_target(
    role=FarmRole.OPERADOR,
    *,
    actor_id="90000001",
    target_id="90000002",
    actor_active=True,
    target_active=True,
    farm_active=True,
):
    actor = _make_user(actor_id, active=actor_active)
    farm = Farm.objects.create(name="Finca permisos")
    if not farm_active:
        farm.is_active = False
        farm.save(update_fields=["is_active"])
    actor_membership = FarmMembership.objects.create(
        farm=farm,
        user=actor,
        role=FarmRole.ADMINISTRADOR,
    )
    target_user = _make_user(target_id, active=target_active)
    target = FarmMembership.objects.create(farm=farm, user=target_user, role=role)
    return actor, farm, actor_membership, target


def _permission_events(farm, event_type):
    return AuditEvent.objects.filter(farm=farm, event_type=event_type)


# --- Escritura: grant ---


def test_grant_creates_assignment_and_audit():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    assignment = permission_services.grant(actor, target, "inventario", "crear")
    assert assignment.membership_id == target.pk
    assert assignment.source == AssignmentSource.ADMIN
    assert assignment.granted_by_id == actor.pk
    assert has(target, "inventario", "crear") is True
    event = _permission_events(farm, EventType.PERMISSION_GRANTED).get()
    assert event.actor_id == actor.pk
    assert event.entity_type == "FarmMembership"
    assert event.entity_id == str(target.pk)
    assert event.reason == ""
    assert event.metadata == {
        "membership_id": target.pk,
        "module": "inventario",
        "action": "crear",
    }
    assert event.after_data == {
        "module": "inventario",
        "action": "crear",
        "source": AssignmentSource.ADMIN,
    }


def test_grant_is_idempotent():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    first = permission_services.grant(actor, target, "inventario", "crear")
    second = permission_services.grant(actor, target, "inventario", "crear")
    assert first.pk == second.pk
    assert PermissionAssignment.objects.count() == 1
    assert _permission_events(farm, EventType.PERMISSION_GRANTED).count() == 1


def test_ungrant_removes_assignment_and_audits_once():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    permission_services.grant(actor, target, "inventario", "crear")
    assert permission_services.ungrant(
        actor, target, "inventario", "crear"
    ) is None
    assert has(target, "inventario", "crear") is False
    event = _permission_events(farm, EventType.PERMISSION_REVOKED).get()
    assert event.before_data == {
        "module": "inventario",
        "action": "crear",
        "source": AssignmentSource.ADMIN,
    }
    assert event.after_data is None
    assert permission_services.ungrant(
        actor, target, "inventario", "crear"
    ) is None
    assert _permission_events(farm, EventType.PERMISSION_REVOKED).count() == 1


# --- Escritura: restrict / unrestrict ---


def test_restrict_and_unrestrict_round_trip():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    permission_services.grant(actor, target, "inventario", "crear")
    restriction = permission_services.restrict(actor, target, "inventario", "crear")
    assert restriction.membership_id == target.pk
    assert has(target, "inventario", "crear") is False
    restricted_event = _permission_events(
        farm, EventType.PERMISSION_RESTRICTED
    ).get()
    assert restricted_event.after_data == {
        "module": "inventario",
        "action": "crear",
    }
    assert permission_services.unrestrict(
        actor, target, "inventario", "crear"
    ) is None
    assert has(target, "inventario", "crear") is True
    unrestricted_event = _permission_events(
        farm, EventType.PERMISSION_UNRESTRICTED
    ).get()
    assert unrestricted_event.before_data == {
        "module": "inventario",
        "action": "crear",
    }
    assert permission_services.unrestrict(
        actor, target, "inventario", "crear"
    ) is None
    assert _permission_events(
        farm, EventType.PERMISSION_UNRESTRICTED
    ).count() == 1


def test_restrict_is_idempotent():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    first = permission_services.restrict(actor, target, "inventario", "crear")
    second = permission_services.restrict(actor, target, "inventario", "crear")
    assert first.pk == second.pk
    assert PermissionRestriction.objects.count() == 1
    assert _permission_events(farm, EventType.PERMISSION_RESTRICTED).count() == 1


# --- Catálogo y techo de rol ---


@pytest.mark.parametrize(
    "operation",
    [
        permission_services.grant,
        permission_services.restrict,
        permission_services.ungrant,
        permission_services.unrestrict,
    ],
)
def test_write_services_reject_unknown_permission(operation):
    actor, _farm, _actor_membership, target = _make_actor_and_target()
    with pytest.raises(AppError) as error:
        operation(actor, target, "inventario", "borrar")
    assert error.value.status_code == 400
    assert not PermissionAssignment.objects.filter(membership=target).exists()
    assert not PermissionRestriction.objects.filter(membership=target).exists()


def test_write_services_reject_non_assignable_permission(monkeypatch):
    actor, _farm, _actor_membership, target = _make_actor_and_target()
    fake = Permission(
        module="inventario",
        action="crear",
        label="No asignable",
        allowed_roles=frozenset({FarmRole.OPERADOR}),
        assignable=False,
    )
    monkeypatch.setattr(permission_services, "PERMISSION_CATALOG", (fake,))
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 400
    assert not PermissionAssignment.objects.filter(membership=target).exists()


def test_grant_rejects_permission_outside_role_ceiling():
    actor, farm, _actor_membership, target = _make_actor_and_target(
        role=FarmRole.CONSULTA
    )
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 400
    assert not PermissionAssignment.objects.filter(membership=target).exists()
    assert not _permission_events(farm, EventType.PERMISSION_GRANTED).exists()


def test_restrict_rejects_permission_outside_role_ceiling():
    actor, _farm, _actor_membership, target = _make_actor_and_target(
        role=FarmRole.CONSULTA
    )
    with pytest.raises(AppError) as error:
        permission_services.restrict(actor, target, "inventario", "crear")
    assert error.value.status_code == 400
    assert not PermissionRestriction.objects.filter(membership=target).exists()


# --- Autorización y estados ---


def test_grant_requires_active_admin_actor():
    actor, _farm, actor_membership, target = _make_actor_and_target()
    actor_membership.role = FarmRole.OPERADOR
    actor_membership.save(update_fields=["role"])
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 403
    assert not PermissionAssignment.objects.filter(membership=target).exists()


def test_grant_rejects_inactive_actor_user():
    actor, _farm, _actor_membership, target = _make_actor_and_target(
        actor_active=False
    )
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 403


def test_grant_rejects_inactive_target_membership():
    actor, _farm, _actor_membership, target = _make_actor_and_target()
    target.status = MembershipStatus.REVOKED
    target.save(update_fields=["status"])
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 404
    assert not PermissionAssignment.objects.filter(membership=target).exists()


def test_grant_rejects_inactive_target_user():
    actor, _farm, _actor_membership, target = _make_actor_and_target(
        target_active=False
    )
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 404


def test_grant_rejects_inactive_farm():
    actor, _farm, _actor_membership, target = _make_actor_and_target(
        farm_active=False
    )
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target, "inventario", "crear")
    assert error.value.status_code == 404


def test_grant_rejects_membership_from_another_farm():
    actor, farm_a, _actor_membership, _target_a = _make_actor_and_target()
    _actor_b, _farm_b, _actor_m_b, target_b = _make_actor_and_target(
        actor_id="90000003",
        target_id="90000004",
    )
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, target_b, "inventario", "crear")
    assert error.value.status_code == 404
    assert not PermissionAssignment.objects.filter(membership=target_b).exists()
    assert not PermissionAssignment.objects.filter(membership__farm=farm_a).exists()


# --- Reglas de autogestión ---


def test_admin_cannot_grant_permission_to_self():
    actor, farm, actor_membership, _target = _make_actor_and_target()
    with pytest.raises(AppError) as error:
        permission_services.grant(actor, actor_membership, "inventario", "crear")
    assert error.value.status_code == 403
    assert not PermissionAssignment.objects.filter(
        membership=actor_membership
    ).exists()
    assert not _permission_events(farm, EventType.PERMISSION_GRANTED).exists()


def test_admin_cannot_remove_own_restriction():
    actor, _farm, actor_membership, _target = _make_actor_and_target()
    _restrict(actor_membership, "inventario", "crear")
    with pytest.raises(AppError) as error:
        permission_services.unrestrict(
            actor, actor_membership, "inventario", "crear"
        )
    assert error.value.status_code == 403
    assert PermissionRestriction.objects.filter(
        membership=actor_membership
    ).exists()


def test_admin_can_restrict_self():
    actor, _farm, actor_membership, _target = _make_actor_and_target()
    restriction = permission_services.restrict(
        actor, actor_membership, "inventario", "crear"
    )
    assert restriction.membership_id == actor_membership.pk
    assert restricted(actor_membership) == {"inventario.crear"}


def test_admin_can_ungrant_own_assignment():
    actor, _farm, actor_membership, _target = _make_actor_and_target()
    _grant(actor_membership, "inventario", "crear")
    assert permission_services.ungrant(
        actor, actor_membership, "inventario", "crear"
    ) is None
    assert not PermissionAssignment.objects.filter(
        membership=actor_membership
    ).exists()


# --- Restricciones de otro administrador ---


def test_unrestrict_other_admin_requires_reason_with_restriction():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    target.role = FarmRole.ADMINISTRADOR
    target.save(update_fields=["role"])
    _restrict(target, "inventario", "crear")
    with pytest.raises(AppError) as error:
        permission_services.unrestrict(actor, target, "inventario", "crear")
    assert error.value.status_code == 400
    assert PermissionRestriction.objects.filter(membership=target).exists()
    assert not _permission_events(
        farm, EventType.PERMISSION_UNRESTRICTED
    ).exists()


def test_unrestrict_other_admin_with_reason_removes_and_audits():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    target.role = FarmRole.ADMINISTRADOR
    target.save(update_fields=["role"])
    _restrict(target, "inventario", "crear")
    assert permission_services.unrestrict(
        actor,
        target,
        "inventario",
        "crear",
        reason="  Ajuste operativo  ",
    ) is None
    assert not PermissionRestriction.objects.filter(membership=target).exists()
    event = _permission_events(farm, EventType.PERMISSION_UNRESTRICTED).get()
    assert event.actor_id == actor.pk
    assert event.reason == "Ajuste operativo"


def test_unrestrict_other_admin_without_restriction_is_noop():
    actor, farm, _actor_membership, target = _make_actor_and_target()
    target.role = FarmRole.ADMINISTRADOR
    target.save(update_fields=["role"])
    assert permission_services.unrestrict(
        actor, target, "inventario", "crear"
    ) is None
    assert not _permission_events(
        farm, EventType.PERMISSION_UNRESTRICTED
    ).exists()


# --- Atomicidad ---


def test_grant_rolls_back_when_audit_fails(monkeypatch):
    actor, _farm, _actor_membership, target = _make_actor_and_target()

    def fail(**kwargs):
        raise RuntimeError("auditoría fallida")

    monkeypatch.setattr(permission_services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        permission_services.grant(actor, target, "inventario", "crear")
    assert not PermissionAssignment.objects.filter(membership=target).exists()


def test_restrict_rolls_back_when_audit_fails(monkeypatch):
    actor, _farm, _actor_membership, target = _make_actor_and_target()

    def fail(**kwargs):
        raise RuntimeError("auditoría fallida")

    monkeypatch.setattr(permission_services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        permission_services.restrict(actor, target, "inventario", "crear")
    assert not PermissionRestriction.objects.filter(membership=target).exists()
