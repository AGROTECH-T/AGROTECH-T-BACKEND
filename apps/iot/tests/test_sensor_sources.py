# ruff: noqa: S101
"""Physical source domain behavior for BUILD IOT-SOURCES-01."""

import pytest
from django.core.exceptions import ValidationError
from uuid import UUID

from apps.accounts.errors import AppError
from apps.iot import services
from apps.iot.models import SensorSource, SensorSourceChange, SensorSourceRole

pytestmark = pytest.mark.django_db


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status


def _coordinator(admin, farm_id, name="Coordinator"):
    return services.create_coordinator(user=admin, farm_id=farm_id, name=name)


def _source(admin, farm_id, coordinator, *, name="Fuente", model=None, role="primary"):
    return services.create_sensor_source(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        name=name,
        model=model,
        role=role,
    )


def test_four_logical_sensors_share_two_physical_sources(admin, farm_id):
    coordinator = _coordinator(admin, farm_id)
    bme = _source(admin, farm_id, coordinator, name="Principal", model="BME680")
    dht = _source(
        admin, farm_id, coordinator, name="Comparación", model="DHT11", role="backup_comparison"
    )

    sensors = [
        services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
            source_uid=bme.uid, channel=1, metric="temperature",
        ),
        services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
            source_uid=bme.uid, channel=2, metric="relative_humidity",
        ),
        services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
            source_uid=dht.uid, channel=3, metric="temperature",
        ),
        services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
            source_uid=dht.uid, channel=4, metric="relative_humidity",
        ),
    ]

    assert [sensor.channel for sensor in sensors] == [1, 2, 3, 4]
    assert [sensor.metric for sensor in sensors] == [
        "temperature", "relative_humidity", "temperature", "relative_humidity"
    ]
    assert [sensor.source_id for sensor in sensors] == [bme.pk, bme.pk, dht.pk, dht.pk]


def test_sensor_creation_requires_source_and_rejects_cross_coordinator(admin, farm_id):
    first = _coordinator(admin, farm_id, "First")
    second = _coordinator(admin, farm_id, "Second")
    source = _source(admin, farm_id, first)

    _error(
        lambda: services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=first.uid,
            channel=1, metric="temperature",
        ),
        400,
    )
    _error(
        lambda: services.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=second.uid,
            source_uid=source.uid, channel=1, metric="temperature",
        ),
        404,
    )


def test_source_roles_are_controlled_and_uuid_is_generated(admin, farm_id):
    coordinator = _coordinator(admin, farm_id)
    source = _source(admin, farm_id, coordinator)
    second = _source(admin, farm_id, coordinator, name="Segunda")
    assert isinstance(source.uid, UUID) and source.uid != second.uid
    assert source.role == SensorSourceRole.PRIMARY
    _error(
        lambda: services.create_sensor_source(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
            name="Otro", role="failover",
        ),
        400,
    )


def test_source_description_changes_are_audited_and_identity_can_be_edited_before_measurement(
    admin, farm_id
):
    coordinator = _coordinator(admin, farm_id)
    source = _source(admin, farm_id, coordinator)

    updated = services.update_sensor_source(
        user=admin,
        farm_id=farm_id,
        source_uid=source.uid,
        name="Corregida",
        model="BME680",
        description="Instalada en el invernadero",
    )

    assert updated.name == "Corregida" and updated.model == "BME680"
    change = SensorSourceChange.objects.get(source=source)
    assert change.changed_by == admin
    assert change.before_data["name"] == "Fuente"
    assert change.after_data["description"] == "Instalada en el invernadero"


def test_source_deactivation_cascades_and_reactivation_keeps_sensors_inactive(admin, farm_id):
    coordinator = _coordinator(admin, farm_id)
    source = _source(admin, farm_id, coordinator)
    sensor = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=source.uid, channel=1, metric="temperature",
    )

    services.deactivate_sensor_source(
        user=admin, farm_id=farm_id, source_uid=source.uid, reason="sustitución"
    )
    sensor.refresh_from_db()
    assert not sensor.is_active and sensor.active_channel is None
    assert SensorSource.objects.filter(pk=source.pk).exists()

    services.reactivate_sensor_source(
        user=admin, farm_id=farm_id, source_uid=source.uid, reason="verificada"
    )
    sensor.refresh_from_db()
    assert SensorSource.objects.get(pk=source.pk).is_active
    assert not sensor.is_active


def test_source_and_sensor_relational_identity_are_immutable_through_model_save(admin, farm_id):
    coordinator = _coordinator(admin, farm_id)
    source = _source(admin, farm_id, coordinator)
    sensor = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=source.uid, channel=1, metric="temperature",
    )
    other_source = _source(admin, farm_id, coordinator, name="Otra")

    source.name = "Nuevo nombre"
    source.coordinator_id = coordinator.pk
    source.uid = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValidationError):
        source.save()

    sensor.source = other_source
    # No readings exist, so source reassignment remains possible before history starts.
    sensor.save()
    sensor.refresh_from_db()
    assert sensor.source_id == other_source.pk
