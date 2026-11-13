# ruff: noqa: S101
"""Pruebas de la reactivación de fincas (DEC-04)."""
import pytest

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _farm_inactive(admin, name="Finca"):
    membership = services.create_farm(user=admin, name=name)
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    return membership


# --- Servicio de dominio ---


def test_admin_reactivates_inactive_farm(make_user):
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    farm_id = membership.farm_id

    farm = services.reactivate_farm(user=admin, farm_id=farm_id)

    assert farm.is_active is True
    assert farm.deactivated_at is None
    assert farm.deactivated_by_id is None
    assert Farm.objects.get(pk=farm_id).is_active is True


def test_non_admin_cannot_reactivate(make_user):
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    operator = make_user("22222222")
    FarmMembership.objects.create(
        farm_id=membership.farm_id, user=operator, role=FarmRole.OPERADOR
    )

    with pytest.raises(AppError) as exc:
        services.reactivate_farm(user=operator, farm_id=membership.farm_id)

    assert exc.value.status_code == 403
    assert Farm.objects.get(pk=membership.farm_id).is_active is False


def test_inactive_account_cannot_reactivate(make_user):
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    admin.is_active = False
    admin.save(update_fields=["is_active"])

    with pytest.raises(AppError) as exc:
        services.reactivate_farm(user=admin, farm_id=membership.farm_id)

    assert exc.value.status_code == 403
    assert Farm.objects.get(pk=membership.farm_id).is_active is False


def test_reactivate_active_farm_is_idempotent(make_user):
    admin = make_user("11111111")
    membership = services.create_farm(user=admin, name="Activa")
    farm_id = membership.farm_id

    farm = services.reactivate_farm(user=admin, farm_id=farm_id)

    assert farm.is_active is True
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.FARM_REACTIVATED
    ).exists()


def test_reactivate_missing_farm_returns_404(make_user):
    admin = make_user("11111111")

    with pytest.raises(AppError) as exc:
        services.reactivate_farm(user=admin, farm_id=999999)

    assert exc.value.status_code == 404


def test_reactivation_preserves_identity_and_history(make_user):
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    farm_id = membership.farm_id
    original_created_at = membership.farm.created_at

    services.reactivate_farm(user=admin, farm_id=farm_id)

    farm = Farm.objects.get(pk=farm_id)
    assert farm.pk == farm_id
    assert farm.name == "Finca"
    assert farm.created_at == original_created_at
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.FARM_CREATED
    ).exists()
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.FARM_DEACTIVATED
    ).exists()


def test_reactivation_records_event(make_user):
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    farm_id = membership.farm_id

    services.reactivate_farm(user=admin, farm_id=farm_id)

    event = AuditEvent.objects.get(
        farm_id=farm_id,
        event_type=EventType.FARM_REACTIVATED,
        entity_type="farm",
        entity_id=str(farm_id),
    )
    assert event.actor_id == admin.pk
    assert event.after_data == {"is_active": True}


def test_admin_of_other_farm_cannot_reactivate(make_user):
    owner = make_user("11111111")
    membership = _farm_inactive(owner)
    other = make_user("22222222")
    services.create_farm(user=other, name="Otra")

    with pytest.raises(AppError) as exc:
        services.reactivate_farm(user=other, farm_id=membership.farm_id)

    assert exc.value.status_code == 404
    assert Farm.objects.get(pk=membership.farm_id).is_active is False


def test_audit_failure_rolls_back_reactivation(make_user, monkeypatch):
    """Si falla la auditoría, la finca permanece inactiva sin evento."""
    admin = make_user("11111111")
    membership = _farm_inactive(admin)
    farm_id = membership.farm_id
    farm = Farm.objects.get(pk=farm_id)
    original_at = farm.deactivated_at
    original_by = farm.deactivated_by_id

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        services.reactivate_farm(user=admin, farm_id=farm_id)

    farm.refresh_from_db()
    assert farm.is_active is False
    assert farm.deactivated_at == original_at
    assert farm.deactivated_by_id == original_by
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.FARM_REACTIVATED
    ).exists()


# --- API ---


def test_reactivate_endpoint_admin(auth_client, user):
    farm = Farm.objects.create(name="Inactiva")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.ADMINISTRADOR)
    services.deactivate_farm(user=user, farm_id=farm.pk)

    response = auth_client.post(f"/api/v1/farms/{farm.pk}/reactivate/")

    assert response.status_code == 204
    farm.refresh_from_db()
    assert farm.is_active is True
    assert farm.deactivated_at is None
    assert farm.deactivated_by_id is None


def test_reactivate_endpoint_non_admin_forbidden(login, make_user):
    admin = make_user("11111111")
    operator = make_user("22222222")
    farm = Farm.objects.create(name="Inactiva")
    FarmMembership.objects.create(farm=farm, user=admin, role=FarmRole.ADMINISTRADOR)
    FarmMembership.objects.create(farm=farm, user=operator, role=FarmRole.OPERADOR)
    services.deactivate_farm(user=admin, farm_id=farm.pk)

    response = login(operator).post(f"/api/v1/farms/{farm.pk}/reactivate/")

    assert response.status_code == 403
    farm.refresh_from_db()
    assert farm.is_active is False
