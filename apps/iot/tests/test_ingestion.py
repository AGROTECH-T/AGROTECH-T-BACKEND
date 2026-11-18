# ruff: noqa: S101, S105, S106
"""Pruebas de la ingesta transaccional contra MariaDB desechable."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections

from apps.accounts.errors import AppError
import apps.iot.services as iot
from apps.iot.lora_frame import LORA_FRAME_HMAC_CONTEXT
from apps.iot.models import (
    ACTIVE_SLOT,
    Coordinator,
    CoordinatorBootWindow,
    CoordinatorCredential,
    FrameConflict,
    Gateway,
    Measurement,
    ReceivedFrame,
    Sensor,
    SensorSource,
    SensorSourceChange,
    SensorSourceRole,
)
from apps.iot.mqtt.payload import parse_payload
from apps.iot.services import ingestion as ingestion_service
from apps.iot.services.ingestion import IngestionStatus, ingest
from apps.iot.tests.conftest import make_source_uid
from apps.iot.tests import lora_vectors

pytestmark = pytest.mark.django_db(transaction=True)

PAYLOAD_AT = "2026-11-13T11:59:00+00:00"
RECEIVED_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class World:
    farm_id: int
    gateway: Gateway
    coordinator: Coordinator
    credential: CoordinatorCredential
    key: bytes
    temperature_sensor: object
    humidity_sensor: object


@pytest.fixture(autouse=True)
def key_settings(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(secrets.token_bytes(32)).decode(
        "ascii"
    )
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    settings.SECRET_KEY = "test-" + secrets.token_urlsafe(48)


@pytest.fixture
def world(admin, farm_id) -> World:
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    iot.authorize_gateway(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        gateway_uid=gateway.uid,
    )
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    issued = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    source_uid = make_source_uid(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    temperature_sensor = iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=source_uid,
        channel=1,
        metric="temperature",
    )
    humidity_sensor = iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=source_uid,
        channel=2,
        metric="relative_humidity",
    )
    return World(
        farm_id=farm_id,
        gateway=gateway,
        coordinator=coordinator,
        credential=issued.credential,
        key=issued.key,
        temperature_sensor=temperature_sensor,
        humidity_sensor=humidity_sensor,
    )


def build_frame(
    world: World,
    *,
    boot: int = 7,
    seq: int = 120,
    readings=lora_vectors.READINGS,
    key_version: int | None = None,
    key: bytes | None = None,
    corrupt_tag: bool = False,
) -> bytes:
    frame = lora_vectors.frame(
        uid=world.coordinator.uid,
        key=world.key if key is None else key,
        key_version=(world.credential.key_version if key_version is None else key_version),
        boot_counter=boot,
        seq=seq,
        readings=readings,
    )
    if corrupt_tag:
        frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
    return frame


def make_payload(frame: bytes, **overrides):
    document = {
        "v": 1,
        "frame": base64.b64encode(frame).decode("ascii"),
        "lora_rx_at": PAYLOAD_AT,
        "clock_synced": True,
        "buffered": False,
        "rssi": -87,
        "snr": 7.5,
    }
    document.update(overrides)
    return parse_payload(json.dumps(document).encode("utf-8"))


def ingest_frame(
    world: World,
    frame: bytes,
    *,
    server_received_at: datetime = RECEIVED_AT,
    **payload_overrides,
):
    return ingest(
        gateway_uid=world.gateway.uid,
        payload=make_payload(frame, **payload_overrides),
        server_received_at=server_received_at,
    )


def counts() -> tuple[int, int, int, int]:
    return (
        ReceivedFrame.objects.count(),
        Measurement.objects.count(),
        CoordinatorBootWindow.objects.count(),
        FrameConflict.objects.count(),
    )


def test_accepted_frame_persists_measurements(world: World) -> None:
    frame = build_frame(world)

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.ACCEPTED
    assert result.measurement_count == 2
    stored = ReceivedFrame.objects.get()
    assert stored.raw_frame == frame
    assert stored.reading_count == 2
    assert stored.gateway_received_at == datetime(2026, 11, 13, 11, 59, tzinfo=UTC)
    assert stored.gateway_clock_synced is True
    assert stored.buffered is False
    assert stored.measured_at == stored.gateway_received_at
    assert stored.measured_at_approximate is False
    assert stored.rssi == -87
    assert stored.snr == Decimal("7.50")

    measurements = {row.channel: row for row in Measurement.objects.order_by("channel")}
    assert measurements[1].sensor_id == world.temperature_sensor.pk
    assert measurements[1].value == Decimal("25.34")
    assert measurements[1].is_invalid_marker is False
    assert measurements[2].sensor_id == world.humidity_sensor.pk
    assert measurements[2].value == Decimal("63.25")


def test_source_identity_freezes_after_measurement_but_description_is_audited(world: World):
    assert ingest_frame(world, build_frame(world)).status is IngestionStatus.ACCEPTED
    source = SensorSource.objects.get(pk=world.temperature_sensor.source_id)

    for field, value in (
        ("name", "Renombrada"),
        ("model", "DHT11"),
        ("manufacturer", "Fabricante"),
        ("role", SensorSourceRole.BACKUP_COMPARISON),
    ):
        with pytest.raises(AppError) as excinfo:
            iot.update_sensor_source(
                user=world.coordinator.created_by,
                farm_id=world.farm_id,
                source_uid=source.uid,
                **{field: value},
            )
        assert excinfo.value.status_code == 409

    iot.update_sensor_source(
        user=world.coordinator.created_by,
        farm_id=world.farm_id,
        source_uid=source.uid,
        description="Revisada después de la medición",
    )
    assert SensorSourceChange.objects.filter(source=source).count() == 1

    source.name = "Cambio directo"
    with pytest.raises(ValidationError):
        source.save()

    replacement = iot.create_sensor_source(
        user=world.coordinator.created_by,
        farm_id=world.farm_id,
        coordinator_uid=world.coordinator.uid,
        name="Reemplazo",
        role=SensorSourceRole.PRIMARY,
    )
    historical_sensor = Sensor.objects.get(pk=world.temperature_sensor.pk)
    historical_sensor.source = replacement
    with pytest.raises(ValidationError):
        historical_sensor.save()


def test_clock_not_synced_uses_server_time(world: World) -> None:
    result = ingest_frame(
        world,
        build_frame(world),
        lora_rx_at=None,
        clock_synced=False,
    )

    assert result.status is IngestionStatus.ACCEPTED
    stored = ReceivedFrame.objects.get()
    assert stored.measured_at == RECEIVED_AT
    assert stored.measured_at_approximate is True


def test_invalid_marker_is_stored_without_value(world: World) -> None:
    frame = build_frame(world, readings=((1, 1, -32768),))

    result = ingest_frame(world, frame, rssi=None, snr=None)

    assert result.status is IngestionStatus.ACCEPTED
    measurement = Measurement.objects.get()
    assert measurement.is_invalid_marker is True
    assert measurement.value is None


def test_same_frame_is_duplicate_without_new_writes(world: World) -> None:
    frame = build_frame(world)
    first = ingest_frame(world, frame)

    second = ingest_frame(world, frame)

    assert first.status is IngestionStatus.ACCEPTED
    assert second.status is IngestionStatus.DUPLICATE
    assert second.frame_id == first.frame_id
    assert counts() == (1, 2, 1, 0)


def test_different_bytes_same_identity_is_conflict(world: World) -> None:
    frame = build_frame(world)
    ingest_frame(world, frame)
    conflicting = build_frame(world, readings=((1, 1, 2600), (2, 2, 6325)))

    result = ingest_frame(world, conflicting)

    assert result.status is IngestionStatus.CONFLICT
    stored = ReceivedFrame.objects.get()
    assert stored.raw_frame == frame
    assert Measurement.objects.count() == 2
    assert Measurement.objects.get(channel=1).value == Decimal("25.34")
    conflict = FrameConflict.objects.get()
    assert conflict.conflicting_raw_frame == conflicting
    assert conflict.conflicting_sha256 == hashlib.sha256(conflicting).hexdigest()

    again = ingest_frame(world, conflicting)
    assert again.status is IngestionStatus.CONFLICT
    assert FrameConflict.objects.count() == 1


def test_structurally_invalid_frame_is_rejected(world: World) -> None:
    frame = lora_vectors.frame(
        uid=world.coordinator.uid,
        key=world.key,
        key_version=world.credential.key_version,
        boot_counter=7,
        seq=121,
        readings=((1, 1, 2534),),
        reading_count=2,
    )

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.REJECTED_INVALID_PAYLOAD
    assert counts() == (0, 0, 0, 0)


def test_unknown_gateway_is_rejected(world: World) -> None:
    result = ingest(
        gateway_uid="GW-ZZZZZZZZZZ",
        payload=make_payload(build_frame(world)),
        server_received_at=RECEIVED_AT,
    )

    assert result.status is IngestionStatus.REJECTED_GATEWAY
    assert counts() == (0, 0, 0, 0)


def test_inactive_gateway_is_rejected(admin, world: World) -> None:
    iot.deactivate_gateway(user=admin, farm_id=world.farm_id, uid=world.gateway.uid)

    result = ingest_frame(world, build_frame(world))

    assert result.status is IngestionStatus.REJECTED_GATEWAY
    assert counts() == (0, 0, 0, 0)


def test_gateway_without_credential_is_rejected(admin, farm_id) -> None:
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Sin credencial")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    iot.authorize_gateway(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        gateway_uid=gateway.uid,
    )
    issued = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    frame = lora_vectors.frame(
        uid=coordinator.uid,
        key=issued.key,
        key_version=issued.credential.key_version,
    )

    result = ingest(
        gateway_uid=gateway.uid,
        payload=make_payload(frame),
        server_received_at=RECEIVED_AT,
    )

    assert result.status is IngestionStatus.REJECTED_GATEWAY


def test_inactive_farm_is_rejected(admin, world: World) -> None:
    from apps.farms.services import deactivate_farm

    deactivate_farm(user=admin, farm_id=world.farm_id)

    result = ingest_frame(world, build_frame(world))

    assert result.status is IngestionStatus.REJECTED_GATEWAY


def test_missing_authorization_is_rejected(admin, farm_id) -> None:
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    issued = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    frame = lora_vectors.frame(
        uid=coordinator.uid,
        key=issued.key,
        key_version=issued.credential.key_version,
    )

    result = ingest(
        gateway_uid=gateway.uid,
        payload=make_payload(frame),
        server_received_at=RECEIVED_AT,
    )

    assert result.status is IngestionStatus.REJECTED_AUTHORIZATION


def test_revoked_authorization_is_rejected(admin, world: World) -> None:
    iot.revoke_gateway_authorization(
        user=admin,
        farm_id=world.farm_id,
        coordinator_uid=world.coordinator.uid,
        gateway_uid=world.gateway.uid,
    )

    result = ingest_frame(world, build_frame(world))

    assert result.status is IngestionStatus.REJECTED_AUTHORIZATION


def test_coordinator_from_another_farm_is_not_resolved(
    admin, other_admin, other_farm_id, world: World
) -> None:
    foreign = iot.create_coordinator(user=other_admin, farm_id=other_farm_id, name="Ajeno")
    frame = lora_vectors.frame(uid=foreign.uid)

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.REJECTED_AUTHORIZATION


def test_revoked_credential_is_rejected(admin, world: World) -> None:
    frame = build_frame(world)
    iot.revoke_coordinator_credential(
        user=admin,
        farm_id=world.farm_id,
        coordinator_uid=world.coordinator.uid,
    )

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.REJECTED_CREDENTIAL
    assert counts() == (0, 0, 0, 0)


def test_invalid_hmac_is_rejected(world: World) -> None:
    result = ingest_frame(world, build_frame(world, corrupt_tag=True))

    assert result.status is IngestionStatus.REJECTED_HMAC
    assert counts() == (0, 0, 0, 0)


def test_wrong_generation_is_rejected(world: World) -> None:
    result = ingest_frame(world, build_frame(world, key_version=world.credential.key_version + 1))

    assert result.status is IngestionStatus.REJECTED_GENERATION
    assert counts() == (0, 0, 0, 0)


def test_replayed_boot_is_rejected_and_window_kept(world: World) -> None:
    first = ingest_frame(world, build_frame(world, boot=100, seq=1))
    assert first.status is IngestionStatus.ACCEPTED

    result = ingest_frame(world, build_frame(world, boot=0, seq=2))

    assert result.status is IngestionStatus.REJECTED_REPLAY
    assert counts() == (1, 2, 1, 0)
    assert CoordinatorBootWindow.objects.get().boot_counter == 100


def test_unknown_channel_rolls_back_the_window(world: World) -> None:
    frame = build_frame(world, readings=((3, 1, 2534),))

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.REJECTED_SENSOR_MAPPING
    assert counts() == (0, 0, 0, 0)


def test_incompatible_metric_rolls_back_the_window(world: World) -> None:
    frame = build_frame(world, readings=((1, 2, 6325),))

    result = ingest_frame(world, frame)

    assert result.status is IngestionStatus.REJECTED_SENSOR_MAPPING
    assert counts() == (0, 0, 0, 0)


def test_inactive_sensor_is_skipped(admin, world: World) -> None:
    iot.deactivate_sensor(
        user=admin,
        farm_id=world.farm_id,
        uid=world.temperature_sensor.uid,
    )

    result = ingest_frame(world, build_frame(world))

    assert result.status is IngestionStatus.ACCEPTED
    assert result.measurement_count == 1
    assert Measurement.objects.get().channel == 2


def test_all_inactive_sensors_keep_the_frame(admin, world: World) -> None:
    for sensor in (world.temperature_sensor, world.humidity_sensor):
        iot.deactivate_sensor(user=admin, farm_id=world.farm_id, uid=sensor.uid)

    result = ingest_frame(world, build_frame(world))

    assert result.status is IngestionStatus.ACCEPTED
    assert result.measurement_count == 0
    assert ReceivedFrame.objects.count() == 1
    assert Measurement.objects.count() == 0


def test_persistence_failure_rolls_back_frame_and_window(world: World, monkeypatch) -> None:
    def boom(_rows):
        raise RuntimeError("fallo de persistencia")

    monkeypatch.setattr(ingestion_service, "_create_measurements", boom)

    with pytest.raises(RuntimeError):
        ingest_frame(world, build_frame(world))

    assert counts() == (0, 0, 0, 0)


def test_unexpected_integrity_error_is_not_hidden(world: World, monkeypatch) -> None:
    def boom(**_kwargs):
        raise IntegrityError("restricción no relacionada")

    monkeypatch.setattr(ingestion_service, "_create_frame_row", boom)

    with pytest.raises(IntegrityError):
        ingest_frame(world, build_frame(world))

    assert counts() == (0, 0, 0, 0)


def test_revocation_waits_for_in_flight_ingestion(admin, world: World, monkeypatch) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()
    original = ingestion_service._verify_frame_hmac

    def paused(*, key, parsed):
        started.set()
        assert release.wait(timeout=10)
        return original(key=key, parsed=parsed)

    monkeypatch.setattr(ingestion_service, "_verify_frame_hmac", paused)
    frame = build_frame(world)

    def run_ingest():
        try:
            return ingest_frame(world, frame)
        finally:
            connections.close_all()

    def run_revocation():
        try:
            return iot.revoke_coordinator_credential(
                user=admin,
                farm_id=world.farm_id,
                coordinator_uid=world.coordinator.uid,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ingestion_future = pool.submit(run_ingest)
        assert started.wait(timeout=10)
        revocation_future = pool.submit(run_revocation)
        time.sleep(0.3)
        assert not revocation_future.done()
        release.set()
        result = ingestion_future.result(timeout=10)
        revoked = revocation_future.result(timeout=10)

    assert result.status is IngestionStatus.ACCEPTED
    assert revoked.revoked_at is not None
    assert not CoordinatorCredential.objects.filter(
        coordinator=world.coordinator, active_slot=ACTIVE_SLOT
    ).exists()

    after = ingest_frame(world, frame)
    assert after.status is IngestionStatus.REJECTED_CREDENTIAL


def test_hmac_context_is_the_v1_constant() -> None:
    assert LORA_FRAME_HMAC_CONTEXT == lora_vectors.CONTEXT
