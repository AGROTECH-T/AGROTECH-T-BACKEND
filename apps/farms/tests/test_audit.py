"""Pruebas de integración de auditoría en las operaciones de Farms."""
import pytest

from apps.farms import services
from apps.farms.models import Farm, FarmMembership
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def test_create_farm_records_event(make_user):
    user = make_user()
    membership = services.create_farm(user=user, name="La Esperanza")
    event = AuditEvent.objects.get(
        entity_type="farm",
        entity_id=str(membership.farm_id),
    )
    assert event.event_type == EventType.FARM_CREATED
    assert event.actor_id == user.pk
    assert event.farm_id == membership.farm_id
    assert event.after_data == {"name": "La Esperanza"}


def test_update_farm_records_event(make_user):
    admin = make_user()
    membership = services.create_farm(user=admin, name="Original")
    services.update_farm(user=admin, farm_id=membership.farm_id, name="Nuevo")
    event = AuditEvent.objects.get(
        event_type=EventType.FARM_UPDATED,
        entity_type="farm",
        entity_id=str(membership.farm_id),
    )
    assert event.before_data == {"name": "Original"}
    assert event.after_data == {"name": "Nuevo"}


def test_deactivate_farm_records_event(make_user):
    admin = make_user()
    membership = services.create_farm(user=admin, name="Temporal")
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    event = AuditEvent.objects.get(
        event_type=EventType.FARM_DEACTIVATED,
        entity_type="farm",
        entity_id=str(membership.farm_id),
    )
    assert event.actor_id == admin.pk
    assert event.after_data == {"is_active": False}


def test_idempotent_deactivation_records_single_event(make_user):
    admin = make_user()
    membership = services.create_farm(user=admin, name="Temporal")
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    count = AuditEvent.objects.filter(
        event_type=EventType.FARM_DEACTIVATED,
        entity_type="farm",
        entity_id=str(membership.farm_id),
    ).count()
    assert count == 1


def test_audit_failure_rolls_back_create(make_user, monkeypatch):
    user = make_user()

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        services.create_farm(user=user, name="Revertida")
    assert not Farm.objects.filter(name="Revertida").exists()
    assert not FarmMembership.objects.exists()
