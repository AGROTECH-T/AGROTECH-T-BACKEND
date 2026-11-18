# ruff: noqa: S101
"""Pruebas HTTP del endpoint de lecturas IoT."""
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from rest_framework.test import APIClient

from apps.iot import services
from apps.iot.tests.conftest import make_source_uid
from apps.iot.tests.test_ingestion import World, build_frame, ingest_frame
from apps.accounts.models import User
from apps.iot.models import MeasurementCorrection, MeasurementCorrectionType

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _test_keys(settings):
    import base64
    import secrets

    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    settings.SECRET_KEY = "test-" + secrets.token_urlsafe(48)


@pytest.fixture
def reading_world(admin, farm_id):
    gateway = services.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    services.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    services.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    issued = services.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    sensor = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid), channel=1,
        metric="temperature",
    )
    humidity_sensor = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid), channel=2,
        metric="relative_humidity",
    )
    world = World(
        farm_id, gateway, coordinator, issued.credential, issued.key, sensor, humidity_sensor
    )
    return world


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _url(farm_id, query=""):
    return f"/api/v1/iot/farms/{farm_id}/readings/{query}"


def _record(world, *, seq=120, measured_at="2026-11-13T11:59:00+00:00"):
    return ingest_frame(world, build_frame(world, seq=seq), lora_rx_at=measured_at)


def test_authentication_and_read_permission(admin, viewer, farm_id, reading_world):
    _record(reading_world)
    assert _client().get(_url(farm_id)).status_code == 401
    response = _client(viewer).get(_url(farm_id))
    assert response.status_code == 200 and response.data["count"] == 2


def test_farm_isolation(admin, other_admin, farm_id, other_farm_id):
    response = _client(other_admin).get(_url(farm_id))
    assert response.status_code == 404


def test_filters_pagination_and_public_serializer(admin, farm_id, reading_world):
    _record(reading_world)
    _record(reading_world, seq=121)
    response = _client(admin).get(
        _url(
            farm_id,
            f"?sensor_uid={reading_world.temperature_sensor.uid}"
            "&from=2026-11-13T00:00:00%2B00:00"
            "&to=2026-11-13T23:59:59%2B00:00&page=2&page_size=1",
        )
    )
    assert response.status_code == 200
    assert response.data["count"] == 2 and len(response.data["results"]) == 1
    row = response.data["results"][0]
    assert set(row) == {
        "uid",
        "sensor_uid", "metric", "value", "unit", "measured_at", "server_received_at",
        "measured_at_approximate", "original_value", "latest_correction", "effective_value",
    }
    assert not {"raw_frame", "raw_value", "hmac", "ciphertext", "nonce"} & set(row)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutation_methods_are_not_allowed(admin, farm_id, method):
    response = getattr(_client(admin), method)(_url(farm_id), {}, format="json")
    assert response.status_code == 405


def test_date_and_ordering_validation(admin, farm_id):
    assert _client(admin).get(_url(farm_id, "?from=2026-11-13T00:00:00")).status_code == 400
    assert _client(admin).get(_url(farm_id, "?ordering=raw_value")).status_code == 400


def test_correction_history_preserves_original_and_calculates_effective_value(
    admin, viewer, farm_id, reading_world
):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    base = f"/api/v1/iot/farms/{farm_id}/readings/{reading.uid}/corrections/"

    response = _client(viewer).get(base)
    assert response.status_code == 200
    assert response.data["original_value"] == "25.34"
    assert response.data["effective_value"] == "25.34"
    assert response.data["corrections"] == []

    created = _client(admin).post(
        base,
        {"correction_type": "value_adjustment", "corrected_value": "24.90", "reason": "calibración"},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["correction"]["revision"] == 1
    assert created.data["effective_value"] == "24.90"
    reading.refresh_from_db()
    assert reading.value == Decimal("25.34")

    invalidated = _client(admin).post(
        base,
        {"correction_type": "invalidation", "corrected_value": None, "reason": "lectura inválida"},
        format="json",
    )
    assert invalidated.status_code == 201
    assert invalidated.data["correction"]["revision"] == 2
    assert invalidated.data["effective_value"] is None
    history = _client(viewer).get(base)
    assert history.data["original_value"] == "25.34"
    assert history.data["effective_value"] is None
    assert [row["revision"] for row in history.data["corrections"]] == [1, 2]


def test_correction_rejects_actor_spoofing_and_inexact_values(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    base = f"/api/v1/iot/farms/{farm_id}/readings/{reading.uid}/corrections/"
    spoofed = _client(admin).post(
        base,
        {"correction_type": "value_adjustment", "corrected_value": "24.901", "reason": "ajuste", "process_id": "forged"},
        format="json",
    )
    assert spoofed.status_code == 400

    float_value = _client(admin).post(
        base,
        {"correction_type": "value_adjustment", "corrected_value": 24.9, "reason": "ajuste"},
        format="json",
    )
    assert float_value.status_code == 400


def test_correction_requires_manage_permission_and_is_farm_scoped(
    viewer, other_admin, farm_id, other_farm_id, reading_world
):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    path = f"/api/v1/iot/farms/{farm_id}/readings/{reading.uid}/corrections/"
    body = {"correction_type": "value_adjustment", "corrected_value": "24.90", "reason": "ajuste"}
    assert _client(viewer).post(path, body, format="json").status_code == 403
    assert _client(other_admin).get(path).status_code == 404
    assert _client(other_admin).post(path, body, format="json").status_code == 404
    assert _client(viewer).get(
        f"/api/v1/iot/farms/{farm_id}/readings/00000000-0000-0000-0000-000000000000/corrections/"
    ).status_code == 404


def test_corrections_are_read_only_after_creation(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    base = f"/api/v1/iot/farms/{farm_id}/readings/{reading.uid}/corrections/"
    assert _client(admin).put(base, {}, format="json").status_code == 405
    assert _client(admin).patch(base, {}, format="json").status_code == 405
    assert _client(admin).delete(base).status_code == 405


def test_measurement_and_correction_orm_are_append_only(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    correction = services.create_measurement_correction(
        user=admin,
        farm_id=farm_id,
        measurement_uid=reading.uid,
        correction_type="value_adjustment",
        corrected_value="24.90",
        reason="calibración",
    )
    with pytest.raises(ValidationError):
        type(reading).objects.filter(pk=reading.pk).update(value=Decimal("24.90"))
    with pytest.raises(ValidationError):
        reading.delete()
    with pytest.raises(ValidationError):
        type(correction).objects.filter(pk=correction.pk).update(reason="otro")
    with pytest.raises(ValidationError):
        correction.delete()


def test_correction_database_constraints_reject_missing_or_ambiguous_actor(
    admin, farm_id, reading_world
):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    common = {
        "measurement": reading,
        "revision": 1,
        "value_before": reading.value,
        "corrected_value": Decimal("24.90"),
        "correction_type": MeasurementCorrectionType.VALUE_ADJUSTMENT,
        "reason": "ajuste",
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        MeasurementCorrection.objects.create(**common, created_by=None, process_id=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        MeasurementCorrection.objects.create(**common, created_by=admin, process_id="ambiguous")


@pytest.mark.skipif(connection.vendor != "mysql", reason="los triggers requieren MariaDB 11.8")
def test_mariadb_trigger_rejects_measurement_update(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE iot_measurements SET value = %s WHERE id = %s",
                    ["24.90", reading.pk],
                )


@pytest.mark.skipif(connection.vendor != "mysql", reason="los triggers requieren MariaDB 11.8")
def test_mariadb_triggers_reject_correction_update_and_delete(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    correction = services.create_measurement_correction(
        user=admin,
        farm_id=farm_id,
        measurement_uid=reading.uid,
        correction_type="value_adjustment",
        corrected_value="24.90",
        reason="calibración",
    )
    for statement in (
        "UPDATE iot_measurement_corrections SET reason = 'alterada' WHERE id = %s",
        "DELETE FROM iot_measurement_corrections WHERE id = %s",
    ):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement, [correction.pk])


@pytest.mark.django_db(transaction=True)
def test_concurrent_corrections_get_distinct_sequential_revisions(admin, farm_id, reading_world):
    _record(reading_world)
    reading = reading_world.temperature_sensor.measurements.get()
    barrier = Barrier(2)

    def append(reason):
        close_old_connections()
        try:
            actor = User.objects.get(pk=admin.pk)
            barrier.wait(timeout=10)
            return services.create_measurement_correction(
                user=actor,
                farm_id=farm_id,
                measurement_uid=reading.uid,
                correction_type="value_adjustment",
                corrected_value="24.90",
                reason=reason,
            ).revision
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(append, ("concurrente 1", "concurrente 2")))
    assert sorted(revisions) == [1, 2]
    assert list(reading.corrections.order_by("revision").values_list("revision", flat=True)) == [1, 2]
