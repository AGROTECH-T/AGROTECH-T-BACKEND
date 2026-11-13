"""Pruebas de servicios del ciclo de vida de membresías."""
import pytest

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _farm(admin, name="Finca"):
    return services.create_farm(user=admin, name=name)


def _add(farm_id, user, role):
    return FarmMembership.objects.create(farm_id=farm_id, user=user, role=role)


def _last_event(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type).first()


# --- Cambio de rol ---


def test_admin_changes_operator_to_consulta(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.change_member_role(
        user=admin, farm_id=farm_id, membership_id=target.pk, role=FarmRole.CONSULTA
    )

    target.refresh_from_db()
    assert target.role == FarmRole.CONSULTA
    event = _last_event(farm_id, EventType.MEMBER_ROLE_CHANGED)
    assert event is not None
    assert event.entity_id == str(target.pk)
    assert event.before_data == {"role": "operador"}
    assert event.after_data == {"role": "consulta"}


def test_admin_changes_consulta_to_operator(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    consulta = make_user("22222222")
    target = _add(farm_id, consulta, FarmRole.CONSULTA)

    services.change_member_role(
        user=admin, farm_id=farm_id, membership_id=target.pk, role=FarmRole.OPERADOR
    )

    target.refresh_from_db()
    assert target.role == FarmRole.OPERADOR


def test_non_admin_cannot_change_roles(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    operator_membership = _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    target = _add(farm_id, consulta, FarmRole.CONSULTA)

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=operator,
            farm_id=farm_id,
            membership_id=target.pk,
            role=FarmRole.OPERADOR,
        )
    assert exc.value.status_code == 403
    assert operator_membership.pk != target.pk


def test_change_role_rejects_invalid_role(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=admin, farm_id=farm_id, membership_id=target.pk, role="propietario"
        )
    assert exc.value.status_code == 400


def test_change_role_rejects_revoked_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    with pytest.raises(AppError):
        services.change_member_role(
            user=admin, farm_id=farm_id, membership_id=target.pk, role=FarmRole.CONSULTA
        )


def test_change_role_rejects_left_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)
    services.leave_farm(user=operator, farm_id=farm_id)

    with pytest.raises(AppError):
        services.change_member_role(
            user=admin, farm_id=farm_id, membership_id=target.pk, role=FarmRole.CONSULTA
        )


def test_cannot_degrade_last_admin(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=admin,
            farm_id=farm_id,
            membership_id=membership.pk,
            role=FarmRole.OPERADOR,
        )
    assert exc.value.status_code == 409
    membership.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR


# --- Revocación ---


def test_revoke_member(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.revoke_member(
        user=admin, farm_id=farm_id, membership_id=target.pk, reason="incumplimiento"
    )

    target.refresh_from_db()
    assert target.status == MembershipStatus.REVOKED
    assert target.revoked_at is not None
    assert target.revoked_by_id == admin.pk
    assert target.left_at is None
    event = _last_event(farm_id, EventType.MEMBER_REVOKED)
    assert event is not None
    assert event.reason == "incumplimiento"
    assert event.actor_id == admin.pk


def test_revoke_reason_is_optional(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    target.refresh_from_db()
    assert target.status == MembershipStatus.REVOKED
    event = _last_event(farm_id, EventType.MEMBER_REVOKED)
    assert event.reason == ""


def test_revoke_co_admin_keeps_actor_as_admin(make_user):
    """Revocar a otro admin siempre deja al actor como administrador."""
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    other = make_user("22222222")
    other_membership = _add(farm_id, other, FarmRole.ADMINISTRADOR)

    services.revoke_member(user=admin, farm_id=farm_id, membership_id=other_membership.pk)

    other_membership.refresh_from_db()
    assert other_membership.status == MembershipStatus.REVOKED
    membership.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR
    assert membership.status == MembershipStatus.ACTIVE


def test_cannot_revoke_self(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    other = make_user("22222222")
    _add(farm_id, other, FarmRole.ADMINISTRADOR)

    with pytest.raises(AppError) as exc:
        services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)
    assert exc.value.status_code == 400
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE


def test_non_admin_cannot_revoke(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    target = _add(farm_id, consulta, FarmRole.CONSULTA)

    with pytest.raises(AppError) as exc:
        services.revoke_member(user=operator, farm_id=farm_id, membership_id=target.pk)
    assert exc.value.status_code == 403


def test_revoke_twice_is_rejected(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)
    with pytest.raises(AppError):
        services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)


# --- Salida voluntaria ---


def test_leave_farm(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.leave_farm(user=operator, farm_id=farm_id)

    target.refresh_from_db()
    assert target.status == MembershipStatus.LEFT
    assert target.left_at is not None
    assert target.revoked_at is None
    event = _last_event(farm_id, EventType.MEMBER_LEFT)
    assert event is not None
    assert event.entity_id == str(target.pk)


def test_leave_requires_own_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    outsider = make_user("33333333")

    with pytest.raises(AppError) as exc:
        services.leave_farm(user=outsider, farm_id=farm_id)
    assert exc.value.status_code == 404


def test_last_admin_cannot_leave(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id

    with pytest.raises(AppError) as exc:
        services.leave_farm(user=admin, farm_id=farm_id)
    assert exc.value.status_code == 409
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE


def test_left_member_has_no_read_access(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    services.leave_farm(user=operator, farm_id=farm_id)

    assert services.get_readable_membership(user=operator, farm_id=farm_id) is None
    with pytest.raises(AppError):
        services.require_active_membership(user=operator, farm_id=farm_id)


def test_revoked_member_has_no_read_access(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    assert services.get_readable_membership(user=operator, farm_id=farm_id) is None
    with pytest.raises(AppError):
        services.require_active_membership(user=operator, farm_id=farm_id)


# --- Transferencia ---


def test_transfer_admin(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)

    membership.refresh_from_db()
    target.refresh_from_db()
    assert target.role == FarmRole.ADMINISTRADOR
    assert membership.role == FarmRole.OPERADOR
    event = _last_event(farm_id, EventType.ADMIN_TRANSFERRED)
    assert event is not None
    assert event.entity_id == str(target.pk)
    assert event.after_data == {"from_role": "operador", "to_role": "administrador"}


def test_transfer_to_inactive_membership_fails(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    with pytest.raises(AppError):
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)


def test_transfer_to_other_admin_fails(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    other = make_user("22222222")
    target = _add(farm_id, other, FarmRole.ADMINISTRADOR)

    with pytest.raises(AppError) as exc:
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)
    assert exc.value.status_code == 400


def test_transfer_to_self_fails(make_user):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id

    with pytest.raises(AppError):
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=membership.pk)


def test_non_admin_cannot_transfer(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    target = _add(farm_id, consulta, FarmRole.CONSULTA)

    with pytest.raises(AppError) as exc:
        services.transfer_admin(user=operator, farm_id=farm_id, membership_id=target.pk)
    assert exc.value.status_code == 403


# --- Atomicidad y rollback ---


def test_audit_failure_rolls_back_revoke(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    target.refresh_from_db()
    assert target.status == MembershipStatus.ACTIVE
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_REVOKED
    ).exists()


def test_audit_failure_rolls_back_transfer(make_user, monkeypatch):
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    operator = make_user("22222222")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)

    membership.refresh_from_db()
    target.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR
    assert target.role == FarmRole.OPERADOR


# --- Conservación del último administrador efectivo ---


def test_cannot_degrade_last_effective_admin(make_user):
    """Con A activo y B con cuenta inactiva, A no puede degradarse a sí mismo."""
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    inactive_admin = make_user("22222222", is_active=False)
    _add(farm_id, inactive_admin, FarmRole.ADMINISTRADOR)

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=admin,
            farm_id=farm_id,
            membership_id=membership.pk,
            role=FarmRole.OPERADOR,
        )
    assert exc.value.status_code == 409
    membership.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR


def test_last_effective_admin_cannot_be_revoked(make_user):
    """Un co-administrador con cuenta inactiva no puede revocar al último efectivo.

    La revocación del último administrador efectivo queda protegida: el único
    otro administrador tiene la cuenta inactiva, de modo que no puede operar
    (403) y la membresía del administrador efectivo permanece intacta.
    """
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    inactive_admin = make_user("22222222", is_active=False)
    _add(farm_id, inactive_admin, FarmRole.ADMINISTRADOR)

    with pytest.raises(AppError) as exc:
        services.revoke_member(
            user=inactive_admin,
            farm_id=farm_id,
            membership_id=membership.pk,
        )
    assert exc.value.status_code == 403
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE


def test_last_effective_admin_cannot_leave(make_user):
    """Con A activo y B con cuenta inactiva, A no puede abandonar la finca."""
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    inactive_admin = make_user("22222222", is_active=False)
    _add(farm_id, inactive_admin, FarmRole.ADMINISTRADOR)

    with pytest.raises(AppError) as exc:
        services.leave_farm(user=admin, farm_id=farm_id)
    assert exc.value.status_code == 409
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE


def test_transfer_to_inactive_account_fails(make_user):
    """No se puede transferir la administración a un operador con cuenta inactiva."""
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    inactive_operator = make_user("22222222", is_active=False)
    target = _add(farm_id, inactive_operator, FarmRole.OPERADOR)

    with pytest.raises(AppError) as exc:
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)
    assert exc.value.status_code == 403
    membership.refresh_from_db()
    target.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR
    assert target.role == FarmRole.OPERADOR


def test_inactive_admin_cannot_execute_admin_operations(make_user):
    """Un administrador con cuenta inactiva no puede ejecutar operaciones de gestión."""
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    inactive_admin = make_user("22222222", is_active=False)
    _add(farm_id, inactive_admin, FarmRole.ADMINISTRADOR)
    operator = make_user("33333333")
    target = _add(farm_id, operator, FarmRole.OPERADOR)

    with pytest.raises(AppError) as exc:
        services.revoke_member(
            user=inactive_admin, farm_id=farm_id, membership_id=target.pk
        )
    assert exc.value.status_code == 403
    target.refresh_from_db()
    assert target.status == MembershipStatus.ACTIVE

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=inactive_admin,
            farm_id=farm_id,
            membership_id=target.pk,
            role=FarmRole.CONSULTA,
        )
    assert exc.value.status_code == 403
    target.refresh_from_db()
    assert target.role == FarmRole.OPERADOR


def test_cannot_promote_inactive_account_to_admin(make_user):
    """No se puede promover a ADMINISTRADOR a un usuario con cuenta inactiva."""
    admin = make_user("11111111")
    farm_id = _farm(admin).farm_id
    inactive_operator = make_user("22222222", is_active=False)
    target = _add(farm_id, inactive_operator, FarmRole.OPERADOR)

    with pytest.raises(AppError) as exc:
        services.change_member_role(
            user=admin,
            farm_id=farm_id,
            membership_id=target.pk,
            role=FarmRole.ADMINISTRADOR,
        )
    assert exc.value.status_code == 403
    target.refresh_from_db()
    assert target.role == FarmRole.OPERADOR


def test_two_effective_admins_allow_non_violating_operations(make_user):
    """Con dos administradores efectivos, las operaciones legítimas se permiten."""
    admin_a = make_user("11111111")
    membership_a = _farm(admin_a)
    farm_id = membership_a.farm_id
    admin_b = make_user("22222222")
    membership_b = _add(farm_id, admin_b, FarmRole.ADMINISTRADOR)

    services.change_member_role(
        user=admin_a,
        farm_id=farm_id,
        membership_id=membership_a.pk,
        role=FarmRole.OPERADOR,
    )
    membership_a.refresh_from_db()
    assert membership_a.role == FarmRole.OPERADOR

    services.revoke_member(user=admin_b, farm_id=farm_id, membership_id=membership_a.pk)
    membership_a.refresh_from_db()
    membership_b.refresh_from_db()
    assert membership_a.status == MembershipStatus.REVOKED
    assert membership_b.status == MembershipStatus.ACTIVE
    assert membership_b.role == FarmRole.ADMINISTRADOR


def test_rejected_operations_leave_no_partial_changes_or_events(make_user):
    """Las operaciones rechazadas no modifican membresías ni registran auditoría."""
    admin = make_user("11111111")
    membership = _farm(admin)
    farm_id = membership.farm_id
    inactive_admin = make_user("22222222", is_active=False)
    _add(farm_id, inactive_admin, FarmRole.ADMINISTRADOR)
    inactive_operator = make_user("33333333", is_active=False)
    target = _add(farm_id, inactive_operator, FarmRole.OPERADOR)

    with pytest.raises(AppError):
        services.change_member_role(
            user=admin,
            farm_id=farm_id,
            membership_id=membership.pk,
            role=FarmRole.OPERADOR,
        )
    membership.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR

    with pytest.raises(AppError):
        services.transfer_admin(user=admin, farm_id=farm_id, membership_id=target.pk)
    membership.refresh_from_db()
    target.refresh_from_db()
    assert membership.role == FarmRole.ADMINISTRADOR
    assert target.role == FarmRole.OPERADOR

    assert not AuditEvent.objects.filter(
        farm_id=farm_id,
        event_type__in=[
            EventType.MEMBER_ROLE_CHANGED,
            EventType.ADMIN_TRANSFERRED,
        ],
    ).exists()


def test_inactive_actor_cannot_transfer_admin(make_user):
    """Un administrador con cuenta inactiva no puede transferir la administración.

    Escenario: admin activo creador, admin con cuenta inactiva (actor) y un
    operador con cuenta activa (receptor). La operación debe rechazarse con 403
    sin modificar roles ni estados y sin registrar ADMIN_TRANSFERRED.
    """
    admin_a = make_user("11111111")
    farm_id = _farm(admin_a).farm_id
    inactive_actor = make_user("22222222", is_active=False)
    actor_membership = _add(farm_id, inactive_actor, FarmRole.ADMINISTRADOR)
    receiver = make_user("33333333")
    receiver_membership = _add(farm_id, receiver, FarmRole.OPERADOR)

    with pytest.raises(AppError) as exc:
        services.transfer_admin(
            user=inactive_actor,
            farm_id=farm_id,
            membership_id=receiver_membership.pk,
        )
    assert exc.value.status_code == 403

    actor_membership.refresh_from_db()
    receiver_membership.refresh_from_db()
    assert actor_membership.role == FarmRole.ADMINISTRADOR
    assert actor_membership.status == MembershipStatus.ACTIVE
    assert receiver_membership.role == FarmRole.OPERADOR
    assert receiver_membership.status == MembershipStatus.ACTIVE
    assert not AuditEvent.objects.filter(
        farm_id=farm_id,
        event_type=EventType.ADMIN_TRANSFERRED,
    ).exists()
