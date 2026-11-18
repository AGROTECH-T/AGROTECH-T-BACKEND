"""Integration checks for append-only accepted-frame history on disposable MariaDB."""

import base64
import secrets

import pytest
from django.db import DatabaseError, connection, transaction

from apps.iot import services
from apps.iot.migrations._ingestion_history_triggers import (
    TRIGGERS,
    _expected_statement,
    _normalize_sql,
)
from apps.iot.models import FrameConflict, ReceivedFrame
from apps.iot.tests.conftest import make_source_uid as _make_source_uid
from apps.iot.tests.test_ingestion import World, build_frame, ingest_frame


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def ingestion_keys(settings):
    """Use synthetic per-test keys; the root conftest requires the IoT profile."""
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    settings.SECRET_KEY = "test-" + secrets.token_urlsafe(48)


@pytest.fixture
def world(admin, farm_id):
    gateway = services.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = services.create_coordinator(
        user=admin, farm_id=farm_id, name="Coordinador"
    )
    services.authorize_gateway(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        gateway_uid=gateway.uid,
    )
    services.issue_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid
    )
    issued = services.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    source_uid = _make_source_uid(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    temperature_sensor = services.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=source_uid,
        channel=1,
        metric="temperature",
    )
    humidity_sensor = services.create_sensor(
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


def _ingest_accepted_frame_and_conflict(world):
    accepted_frame = build_frame(world)
    accepted = ingest_frame(world, accepted_frame)
    conflicting_frame = build_frame(
        world, readings=((1, 1, 2600), (2, 2, 6325))
    )
    conflict = ingest_frame(world, conflicting_frame)
    return accepted, conflict


def test_four_ingestion_history_triggers_exist_with_expected_definitions():
    names = [trigger[0] for trigger in TRIGGERS]
    placeholders = ", ".join(["%s"] * len(names))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, "
            "EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME IN ({placeholders})",
            names,
        )
        rows = cursor.fetchall()

    actual = {
        row[0]: (row[1], row[2], row[3], _normalize_sql(row[4]))
        for row in rows
    }
    expected = {
        name: (table, "BEFORE", event, _expected_statement(message))
        for name, table, event, message in TRIGGERS
    }
    assert actual == expected


def test_normal_ingestion_inserts_frame_measurements_and_conflict(world):
    accepted, conflict = _ingest_accepted_frame_and_conflict(world)

    assert accepted.status.value == "accepted"
    assert conflict.status.value == "conflict"
    assert ReceivedFrame.objects.count() == 1
    assert ReceivedFrame.objects.get().measurements.count() == 2
    assert FrameConflict.objects.count() == 1


@pytest.mark.parametrize(
    ("kind", "statement"),
    [
        (
            "frame",
            "UPDATE iot_received_frames SET buffered = NOT buffered WHERE id = %s",
        ),
        ("frame", "DELETE FROM iot_received_frames WHERE id = %s"),
        (
            "conflict",
            "UPDATE iot_frame_conflicts SET server_received_at = "
            "'2000-01-01 00:00:00' WHERE id = %s",
        ),
        ("conflict", "DELETE FROM iot_frame_conflicts WHERE id = %s"),
    ],
)
def test_history_triggers_reject_direct_update_and_delete(world, kind, statement):
    accepted, conflict = _ingest_accepted_frame_and_conflict(world)
    assert accepted.status.value == "accepted"
    assert conflict.status.value == "conflict"
    row_id = (
        ReceivedFrame.objects.get().pk
        if kind == "frame"
        else FrameConflict.objects.get().pk
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(statement, [row_id])
