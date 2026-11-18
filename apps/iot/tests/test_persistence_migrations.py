# ruff: noqa: S101
"""Forward, reverse, retry, and preservation checks for IoT-04.3-B."""

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.iot.identifiers import IdentifierKind, generate_identifier
from apps.iot.models import (
    Coordinator,
    CoordinatorCredential,
    CoordinatorGateway,
    Gateway,
    Sensor,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def allow_destructive_rollback_on_disposable_database(settings):
    """VALIDATION-02: these roundtrips cross the 0011/0013/0015 rollback barriers.

    Only valid because the IoT disposable profile proves the database is a
    throwaway MariaDB 11.8 before any test runs.
    """
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = True

IOT_0005 = ("iot", "0005_rename_key_version_master_key_version")
IOT_0007 = ("iot", "0007_iot_frame_measurement_conflict")
IOT_0008 = ("iot", "0008_iot_persistence_composite_fks")
TABLES = {"iot_received_frames", "iot_measurements", "iot_frame_conflicts"}
FK_MODULE = importlib.import_module("apps.iot.migrations.0008_iot_persistence_composite_fks")
PARENT_UNIQUES = {
    "iot_coordinator_credentials": "iot_coordcred_coord_id_uniq",
    "iot_sensors": "iot_sensor_id_coord_ch_metric_uniq",
    "iot_coordinator_gateways": "iot_cg_id_coord_gw_farm_uniq",
}


def _executor():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(target):
    _executor().migrate([target])


def _restore_leaves():
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _tables():
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor)) & TABLES


def _fks():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE()"
        )
        return {row[0] for row in cursor.fetchall()} & {
            spec.name for spec in FK_MODULE.PERSISTENCE_FKS
        }


def _parent_uniques():
    found = {}
    with connection.cursor() as cursor:
        for table in PARENT_UNIQUES:
            for name, info in connection.introspection.get_constraints(cursor, table).items():
                if info.get("unique"):
                    found[name] = tuple(info["columns"])
    return {name: found.get(name) for name in PARENT_UNIQUES.values()}


EXPECTED_PARENT_UNIQUES = {
    "iot_coordcred_coord_id_uniq": ("coordinator_id", "id"),
    "iot_sensor_id_coord_ch_metric_uniq": (
        "id", "coordinator_id", "channel", "metric"
    ),
    "iot_cg_id_coord_gw_farm_uniq": (
        "id", "coordinator_id", "gateway_id", "farm_id"
    ),
}


def test_iot_0006_to_0008_roundtrip_preserves_existing_rows(farm_id):
    coordinator = Coordinator.objects.create(
        uid=generate_identifier(IdentifierKind.COORDINATOR), farm_id=farm_id, name="CO"
    )
    gateway = Gateway.objects.create(
        uid=generate_identifier(IdentifierKind.GATEWAY), farm_id=farm_id, name="GW"
    )
    sensor = Sensor.objects.create(
        uid=generate_identifier(IdentifierKind.SENSOR),
        farm_id=farm_id,
        coordinator=coordinator,
        channel=1,
        active_channel=1,
        metric="temperature",
    )
    authorization = CoordinatorGateway.objects.create(
        farm_id=farm_id,
        coordinator=coordinator,
        gateway=gateway,
        authorized_at=timezone.now(),
    )
    credential = CoordinatorCredential.objects.create(
        coordinator=coordinator,
        ciphertext=bytes(48),
        nonce=bytes(12),
        master_key_version=1,
        issued_at=timezone.now(),
    )
    ids = (coordinator.pk, gateway.pk, sensor.pk, credential.pk, authorization.pk)

    def assert_parents_preserved():
        assert Coordinator.objects.filter(pk=ids[0]).exists()
        assert Gateway.objects.filter(pk=ids[1]).exists()
        assert Sensor.objects.filter(pk=ids[2]).exists()
        assert CoordinatorCredential.objects.filter(pk=ids[3]).exists()
        assert CoordinatorGateway.objects.filter(pk=ids[4]).exists()

    try:
        _migrate(IOT_0005)
        assert _tables() == set()
        assert _parent_uniques() == dict.fromkeys(EXPECTED_PARENT_UNIQUES)
        assert_parents_preserved()

        _migrate(IOT_0008)
        assert _tables() == TABLES
        assert len(_fks()) == 6
        assert _parent_uniques() == EXPECTED_PARENT_UNIQUES
        assert_parents_preserved()

        _migrate(IOT_0007)
        assert _tables() == TABLES
        assert _fks() == set()
        assert _parent_uniques() == EXPECTED_PARENT_UNIQUES
        assert_parents_preserved()
        _migrate(IOT_0008)
        assert _fks() == {spec.name for spec in FK_MODULE.PERSISTENCE_FKS}
        assert_parents_preserved()

        _migrate(IOT_0005)
        assert _tables() == set()
        assert _parent_uniques() == dict.fromkeys(EXPECTED_PARENT_UNIQUES)
        assert_parents_preserved()
        _migrate(IOT_0008)
        assert _tables() == TABLES
        assert len(_fks()) == 6
        assert _parent_uniques() == EXPECTED_PARENT_UNIQUES
        assert_parents_preserved()
    finally:
        _restore_leaves()


def test_iot_0008_helper_is_idempotent_and_reversible():
    try:
        assert _fks() == {spec.name for spec in FK_MODULE.PERSISTENCE_FKS}
        with connection.schema_editor(atomic=False) as editor:
            FK_MODULE.add_persistence_fks(None, editor)
            FK_MODULE.add_persistence_fks(None, editor)
        assert len(_fks()) == 6

        with connection.schema_editor(atomic=False) as editor:
            FK_MODULE.drop_persistence_fks(None, editor)
            FK_MODULE.drop_persistence_fks(None, editor)
        assert _fks() == set()

        with connection.schema_editor(atomic=False) as editor:
            FK_MODULE.add_persistence_fks(None, editor)
        assert len(_fks()) == 6
    finally:
        if _fks() != {spec.name for spec in FK_MODULE.PERSISTENCE_FKS}:
            with connection.schema_editor(atomic=False) as editor:
                FK_MODULE.add_persistence_fks(None, editor)
