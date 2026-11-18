# ruff: noqa: S101
"""Pruebas del servicio de consulta de mediciones IoT (BUILD E3.1)."""
from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.errors import AppError
from apps.iot import services
from apps.iot.tests.conftest import make_source_uid
from apps.iot.services import readings as readings_service
from apps.iot.tests.test_ingestion import World, build_frame, ingest_frame

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _test_keys(settings):
    """Configura claves efímeras requeridas por la fixture de persistencia."""
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
    temperature = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid), channel=1,
        metric="temperature",
    )
    humidity = services.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid), channel=2,
        metric="relative_humidity",
    )
    return World(
        farm_id, gateway, coordinator, issued.credential, issued.key, temperature, humidity
    )


def _record(world, *, seq=120, measured_at="2026-11-13T11:59:00+00:00"):
    frame = build_frame(world, seq=seq)
    return ingest_frame(world, frame, lora_rx_at=measured_at)


def _call(user, farm_id, **filters):
    return services.list_readings(user=user, farm_id=farm_id, **filters)


def test_query_is_isolated_by_farm(admin, other_admin, farm_id, other_farm_id, reading_world):
    _record(reading_world)
    other_gateway = services.create_gateway(user=other_admin, farm_id=other_farm_id, name="G2")
    other_coordinator = services.create_coordinator(
        user=other_admin, farm_id=other_farm_id, name="C2"
    )
    services.authorize_gateway(
        user=other_admin, farm_id=other_farm_id, coordinator_uid=other_coordinator.uid,
        gateway_uid=other_gateway.uid,
    )
    services.issue_gateway_credential(user=other_admin, farm_id=other_farm_id,
                                       gateway_uid=other_gateway.uid)
    issued = services.issue_coordinator_credential(
        user=other_admin, farm_id=other_farm_id, coordinator_uid=other_coordinator.uid
    )
    sensor = services.create_sensor(
        user=other_admin, farm_id=other_farm_id, coordinator_uid=other_coordinator.uid,
        source_uid=make_source_uid(
            user=other_admin, farm_id=other_farm_id, coordinator_uid=other_coordinator.uid
        ),
        channel=1, metric="temperature",
    )
    other_world = World(
        other_farm_id, other_gateway, other_coordinator, issued.credential, issued.key,
        sensor, sensor,
    )
    ingest_frame(
        other_world, build_frame(other_world), lora_rx_at="2026-11-13T11:59:00+00:00"
    )
    assert list(_call(admin, farm_id).values_list("farm_id", flat=True)) == [farm_id, farm_id]


def test_sensor_filter_and_empty_results(admin, farm_id, reading_world):
    _record(reading_world)
    rows = list(_call(admin, farm_id, sensor_uid=reading_world.temperature_sensor.uid))
    assert len(rows) == 1 and rows[0].sensor_id == reading_world.temperature_sensor.pk
    humidity_rows = list(_call(admin, farm_id, sensor_uid=reading_world.humidity_sensor.uid))
    assert len(humidity_rows) == 1
    assert list(
        _call(
            admin, farm_id, date_from="2026-10-01T00:00:00+00:00",
            date_to="2026-10-02T00:00:00+00:00",
        )
    ) == []


def test_sensor_from_another_farm_is_not_found(
    admin, other_admin, farm_id, other_farm_id, reading_world
):
    foreign_coordinator = services.create_coordinator(
        user=other_admin, farm_id=other_farm_id, name="Finca ajena"
    )
    foreign_sensor = services.create_sensor(
        user=other_admin, farm_id=other_farm_id, coordinator_uid=foreign_coordinator.uid,
        source_uid=make_source_uid(user=other_admin, farm_id=other_farm_id, coordinator_uid=foreign_coordinator.uid),
        channel=4, metric="temperature",
    )
    with pytest.raises(AppError) as error:
        _call(admin, farm_id, sensor_uid=foreign_sensor.uid)
    assert error.value.status_code == 404


def test_date_window_validates_timezone_and_range(admin, farm_id, reading_world):
    _record(reading_world)
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    assert len(_call(admin, farm_id, now=now)) == 2
    with pytest.raises(AppError) as naive:
        _call(admin, farm_id, date_from="2026-11-01T00:00:00", now=now)
    assert naive.value.status_code == 400
    with pytest.raises(AppError) as too_wide:
        _call(admin, farm_id, date_from="2026-10-01T00:00:00+00:00",
              date_to="2026-11-13T00:00:00+00:00", now=now)
    assert too_wide.value.status_code == 400


def test_default_and_maximum_windows(admin, farm_id, reading_world):
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    _record(reading_world, seq=120, measured_at="2026-11-07T12:00:00+00:00")
    _record(reading_world, seq=121, measured_at="2026-11-05T11:59:00+00:00")
    assert len(_call(admin, farm_id, now=now)) == 2
    exact_start = "2026-10-13T12:00:00+00:00"
    exact_end = "2026-11-13T12:00:00+00:00"
    assert len(_call(admin, farm_id, date_from=exact_start, date_to=exact_end, now=now)) == 4
    assert readings_service.MAX_WINDOW == timedelta(days=31)


@pytest.mark.parametrize("ordering", readings_service.ORDERINGS)
def test_allowed_orderings(admin, farm_id, reading_world, ordering):
    _record(reading_world)
    assert _call(admin, farm_id, ordering=ordering).exists()


@pytest.mark.parametrize("ordering", ["raw_value", "farm", "-id", "measured_at,raw_value"])
def test_rejects_unlisted_ordering(admin, farm_id, ordering):
    with pytest.raises(AppError) as error:
        _call(admin, farm_id, ordering=ordering)
    assert error.value.status_code == 400
