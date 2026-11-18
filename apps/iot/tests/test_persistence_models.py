# ruff: noqa: S101
"""Database integrity for IoT-04.3-B persistence models."""

import hashlib
import importlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.iot.catalog import MetricCode
from apps.iot.identifiers import IdentifierKind, generate_identifier
from apps.iot.migrations._composite_fk import fk_definition
from apps.iot.models import (
    INT16_MAX,
    INT16_MIN,
    Coordinator,
    CoordinatorCredential,
    CoordinatorGateway,
    FrameConflict,
    Gateway,
    Measurement,
    ReceivedFrame,
    Sensor,
)

pytestmark = pytest.mark.django_db(transaction=True)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSISTENCE_FKS = importlib.import_module(
    "apps.iot.migrations.0008_iot_persistence_composite_fks"
).PERSISTENCE_FKS


def _setup(farm_id, *, channel=1, metric=MetricCode.TEMPERATURE):
    coordinator = Coordinator.objects.create(
        uid=generate_identifier(IdentifierKind.COORDINATOR), farm_id=farm_id, name="CO"
    )
    gateway = Gateway.objects.create(
        uid=generate_identifier(IdentifierKind.GATEWAY), farm_id=farm_id, name="GW"
    )
    credential = CoordinatorCredential.objects.create(
        coordinator=coordinator,
        ciphertext=bytes(48),
        nonce=bytes(12),
        master_key_version=1,
        issued_at=timezone.now(),
    )
    authorization = CoordinatorGateway.objects.create(
        farm_id=farm_id,
        coordinator=coordinator,
        gateway=gateway,
        authorized_at=timezone.now(),
    )
    sensor = Sensor.objects.create(
        uid=generate_identifier(IdentifierKind.SENSOR),
        farm_id=farm_id,
        coordinator=coordinator,
        channel=channel,
        active_channel=channel,
        metric=metric,
    )
    return coordinator, gateway, credential, authorization, sensor


def _frame(farm_id, *, setup=None, seq=1, **overrides):
    coordinator, gateway, credential, authorization, _sensor = setup or _setup(farm_id)
    fields = {
        "farm_id": farm_id,
        "gateway": gateway,
        "coordinator": coordinator,
        "credential": credential,
        "authorization": authorization,
        "frame_version": 1,
        "key_version": 1,
        "boot_counter": 1,
        "seq": seq,
        "reading_count": 1,
        "raw_frame": bytes(62),
        "gateway_received_at": None,
        "gateway_clock_synced": False,
        "buffered": False,
        "rssi": None,
        "snr": None,
        "server_received_at": NOW,
        "measured_at": NOW,
        "measured_at_approximate": True,
    }
    fields.update(overrides)
    return ReceivedFrame.objects.create(**fields)


def _measurement(frame, sensor, *, channel=1, metric=MetricCode.TEMPERATURE,
                 raw_value=2500, value=Decimal("25.00"), is_invalid_marker=False, **overrides):
    fields = {
        "frame": frame,
        "farm_id": frame.farm_id,
        "coordinator_id": frame.coordinator_id,
        "sensor": sensor,
        "channel": channel,
        "metric": metric,
        "raw_value": raw_value,
        "value": value,
        "is_invalid_marker": is_invalid_marker,
        "measured_at": frame.measured_at,
    }
    fields.update(overrides)
    return Measurement.objects.create(**fields)


def _rejected(callable_):
    with pytest.raises(IntegrityError), transaction.atomic():
        callable_()


def test_received_frame_identity_and_zero_measurements(farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    assert frame.measurements.count() == 0
    _rejected(lambda: _frame(farm_id, setup=setup))


@pytest.mark.parametrize(
    "overrides",
    [
        {"frame_version": 2},
        {"key_version": 65536},
        {"boot_counter": 2**32},
        {"seq": 2**32},
        {"reading_count": 0},
        {"raw_frame": bytes(61)},
    ],
)
def test_received_frame_check_constraints(farm_id, overrides):
    _rejected(lambda: _frame(farm_id, **overrides))


@pytest.mark.parametrize("raw_value", [10001, 65534])
def test_humidity_rejects_values_outside_d09(farm_id, raw_value):
    setup = _setup(farm_id, metric=MetricCode.RELATIVE_HUMIDITY)
    frame = _frame(farm_id, setup=setup)
    _rejected(lambda: _measurement(
        frame,
        setup[4],
        metric=MetricCode.RELATIVE_HUMIDITY,
        raw_value=raw_value,
        value=Decimal(raw_value) / Decimal(100),
    ))


@pytest.mark.parametrize(
    ("raw_value", "value", "is_invalid"),
    [(0, Decimal("0.00"), False), (10000, Decimal("100.00"), False), (65535, None, True)],
)
def test_humidity_accepts_d09_values_and_sentinel(farm_id, raw_value, value, is_invalid):
    setup = _setup(farm_id, metric=MetricCode.RELATIVE_HUMIDITY)
    frame = _frame(farm_id, setup=setup)
    reading = _measurement(
        frame,
        setup[4],
        metric=MetricCode.RELATIVE_HUMIDITY,
        raw_value=raw_value,
        value=value,
        is_invalid_marker=is_invalid,
    )
    assert reading.pk


@pytest.mark.parametrize(
    ("raw_value", "value", "is_invalid"),
    [
        (INT16_MIN, None, True),
        (INT16_MIN + 1, Decimal("-327.67"), False),
        (INT16_MAX, Decimal("327.67"), False),
    ],
)
def test_temperature_accepts_int16_range_and_sentinel(
    farm_id, raw_value, value, is_invalid
):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    reading = _measurement(
        frame,
        setup[4],
        raw_value=raw_value,
        value=value,
        is_invalid_marker=is_invalid,
    )
    assert reading.pk


@pytest.mark.parametrize("raw_value", [INT16_MIN - 1, INT16_MAX + 1])
def test_temperature_rejects_values_outside_int16(farm_id, raw_value):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    _rejected(lambda: _measurement(
        frame,
        setup[4],
        raw_value=raw_value,
        value=Decimal(raw_value) / Decimal(100),
    ))


def test_measurement_unique_per_frame_and_channel(farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    _measurement(frame, setup[4])
    _rejected(lambda: _measurement(frame, setup[4]))


def test_measurement_check_constraints(farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    _rejected(lambda: _measurement(frame, setup[4], channel=0))
    _rejected(lambda: _measurement(frame, setup[4], metric="unknown"))
    _rejected(lambda: _measurement(frame, setup[4], raw_value=1, value=Decimal("2.00")))
    _rejected(lambda: _measurement(
        frame,
        setup[4],
        raw_value=-32768,
        value=Decimal("-327.68"),
        is_invalid_marker=False,
    ))


def test_frame_conflict_requires_lowercase_sha256_and_is_unique(farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    raw = b"different frame bytes"
    conflict = FrameConflict.objects.create(
        frame=frame,
        gateway=setup[1],
        farm_id=farm_id,
        conflicting_raw_frame=raw,
        conflicting_sha256=hashlib.sha256(raw).hexdigest(),
        server_received_at=NOW,
    )
    assert conflict.pk
    _rejected(lambda: FrameConflict.objects.create(
        frame=frame,
        gateway=setup[1],
        farm_id=farm_id,
        conflicting_raw_frame=raw,
        conflicting_sha256=hashlib.sha256(raw).hexdigest(),
        server_received_at=NOW,
    ))
    _rejected(lambda: FrameConflict.objects.create(
        frame=frame,
        gateway=setup[1],
        farm_id=farm_id,
        conflicting_raw_frame=raw,
        conflicting_sha256="A" * 64,
        server_received_at=NOW,
    ))


def test_all_six_persistence_foreign_keys_have_restrict_actions():
    assert connection.vendor == "mysql"
    for spec in PERSISTENCE_FKS:
        definition = fk_definition(connection, spec.table, spec.name)
        assert definition == [
            (child, spec.target_table, parent, "RESTRICT", "RESTRICT")
            for child, parent in zip(spec.columns, spec.target_columns, strict=True)
        ]


def test_composite_datetime_fk_columns_are_datetime6():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME, DATA_TYPE, DATETIME_PRECISION "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'measured_at' "
            "AND TABLE_NAME IN ('iot_received_frames', 'iot_measurements')"
        )
        found = {row[0]: (row[1].lower(), row[2]) for row in cursor.fetchall()}
    assert found == {
        "iot_received_frames": ("datetime", 6),
        "iot_measurements": ("datetime", 6),
    }


def test_three_tables_and_three_parent_unique_keys_exist():
    expected_tables = {
        "iot_received_frames": "receivedframe",
        "iot_measurements": "measurement",
        "iot_frame_conflicts": "frameconflict",
    }
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        assert set(expected_tables) <= tables
        expected_uniques = {
            "iot_coordcred_coord_id_uniq": ("coordinator_id", "id"),
            "iot_sensor_id_coord_ch_metric_uniq": (
                "id", "coordinator_id", "channel", "metric"
            ),
            "iot_cg_id_coord_gw_farm_uniq": (
                "id", "coordinator_id", "gateway_id", "farm_id"
            ),
        }
        actual = {}
        for table in (
            "iot_coordinator_credentials",
            "iot_sensors",
            "iot_coordinator_gateways",
        ):
            for name, info in connection.introspection.get_constraints(cursor, table).items():
                if info.get("unique"):
                    actual[name] = tuple(info["columns"])
    assert {name: actual.get(name) for name in expected_uniques} == expected_uniques


def test_composite_relations_reject_cross_entity_rows(farm_id, other_farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    other = _setup(farm_id, channel=1)
    wrong_channel_sensor = Sensor.objects.create(
        uid=generate_identifier(IdentifierKind.SENSOR),
        farm_id=farm_id,
        coordinator=setup[0],
        channel=2,
        active_channel=2,
        metric=MetricCode.TEMPERATURE,
    )
    other_auth = CoordinatorGateway.objects.create(
        farm_id=farm_id,
        coordinator=other[0],
        gateway=setup[1],
        authorized_at=timezone.now(),
    )

    _rejected(lambda: _frame(farm_id, setup=setup, credential=other[2]))
    _rejected(lambda: _frame(farm_id, setup=setup, seq=2, authorization=other_auth))
    _rejected(lambda: _measurement(frame, other[4], coordinator_id=other[0].id))
    _rejected(lambda: _measurement(frame, wrong_channel_sensor, channel=1))

    other_farm_setup = _setup(other_farm_id)
    raw = b"cross farm conflict"
    digest = hashlib.sha256(raw).hexdigest()
    _rejected(lambda: FrameConflict.objects.create(
        frame=frame,
        gateway=other_farm_setup[1],
        farm_id=other_farm_id,
        conflicting_raw_frame=raw,
        conflicting_sha256=digest,
        server_received_at=NOW,
    ))
    _rejected(lambda: FrameConflict.objects.create(
        frame=frame,
        gateway=other_farm_setup[1],
        farm_id=farm_id,
        conflicting_raw_frame=raw,
        conflicting_sha256=digest,
        server_received_at=NOW,
    ))


def test_received_frame_credential_and_authorization_must_match_farm(
    farm_id, other_farm_id
):
    setup = _setup(farm_id)
    other = _setup(other_farm_id)

    _rejected(lambda: _frame(farm_id, setup=setup, credential=other[2]))
    _rejected(lambda: _frame(farm_id, setup=setup, authorization=other[3]))
    _rejected(lambda: _frame(farm_id, setup=setup, gateway=other[1], seq=2))
    _rejected(lambda: _frame(other_farm_id, setup=setup))


def test_measurement_copies_must_match_its_frame(farm_id, other_farm_id):
    setup = _setup(farm_id)
    frame = _frame(farm_id, setup=setup)
    other = _setup(other_farm_id)

    _rejected(lambda: _measurement(frame, setup[4], farm_id=other_farm_id))
    _rejected(lambda: _measurement(frame, setup[4], measured_at=datetime(2026, 1, 2, tzinfo=UTC)))
    _rejected(lambda: _measurement(frame, other[4]))


def test_session_transaction_isolation_is_read_committed():
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@SESSION.transaction_isolation")
        isolation = str(cursor.fetchone()[0])
    assert isolation == "READ-COMMITTED"


def test_latest_migration_state_contains_persistence_models():
    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    for model in (ReceivedFrame, Measurement, FrameConflict):
        key = ("iot", model._meta.model_name)
        assert key in state.models
        assert state.models[key].options["db_table"] == model._meta.db_table
        assert state.models[key].options["constraints"] == model._meta.constraints
        assert state.models[key].options["indexes"] == model._meta.indexes

    parent_uniques = {
        CoordinatorCredential: ("iot_coordcred_coord_id_uniq", ("coordinator", "id")),
        Sensor: ("iot_sensor_id_coord_ch_metric_uniq", ("id", "coordinator", "channel", "metric")),
        CoordinatorGateway: (
            "iot_cg_id_coord_gw_farm_uniq",
            ("id", "coordinator", "gateway", "farm"),
        ),
    }
    for model, (name, fields) in parent_uniques.items():
        constraints = {
            constraint.name: tuple(constraint.fields)
            for constraint in state.models[("iot", model._meta.model_name)].options["constraints"]
            if hasattr(constraint, "fields")
        }
        assert constraints.get(name) == fields
