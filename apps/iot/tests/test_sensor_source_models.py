# ruff: noqa: S101
"""Database constraints for source identity and cross-device relationships."""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.iot.identifiers import IdentifierKind, generate_identifier
from apps.iot.models import Coordinator, Sensor, SensorSource, SensorSourceRole

pytestmark = pytest.mark.django_db


def _coordinator(farm_id, name):
    return Coordinator.objects.create(
        uid=generate_identifier(IdentifierKind.COORDINATOR),
        farm_id=farm_id,
        name=name,
    )


def _source(farm_id, coordinator, **kwargs):
    return SensorSource.objects.create(
        farm_id=farm_id,
        coordinator=coordinator,
        name=kwargs.pop("name", "Fuente"),
        role=kwargs.pop("role", SensorSourceRole.PRIMARY),
        **kwargs,
    )


def _rejected(operation):
    with pytest.raises(IntegrityError), transaction.atomic():
        operation()


def test_uuid_is_unique_and_role_is_constrained(farm_id):
    coordinator = _coordinator(farm_id, "C")
    source = _source(farm_id, coordinator)
    _rejected(lambda: _source(farm_id, coordinator, uid=source.uid, name="Duplicada"))
    _rejected(lambda: _source(farm_id, coordinator, role="unknown"))


def test_composite_foreign_keys_reject_cross_farm_and_cross_coordinator(
    farm_id, other_farm_id
):
    coordinator = _coordinator(farm_id, "C1")
    another_coordinator = _coordinator(farm_id, "C2")
    source = _source(farm_id, coordinator)

    _rejected(
        lambda: _source(
            other_farm_id,
            coordinator,
            name="Finca cruzada",
        )
    )
    _rejected(
        lambda: Sensor.objects.create(
            uid=generate_identifier(IdentifierKind.SENSOR),
            farm_id=farm_id,
            coordinator=another_coordinator,
            source=source,
            channel=1,
            active_channel=1,
            metric="temperature",
        )
    )


def test_source_cannot_be_deleted_while_logical_sensor_references_it(farm_id):
    coordinator = _coordinator(farm_id, "C")
    source = _source(farm_id, coordinator)
    Sensor.objects.create(
        uid=generate_identifier(IdentifierKind.SENSOR),
        farm_id=farm_id,
        coordinator=coordinator,
        source=source,
        channel=1,
        active_channel=1,
        metric="temperature",
    )

    with pytest.raises(ProtectedError):
        source.delete()
