# ruff: noqa: S101
"""Ida y vuelta de las migraciones de IoT-02.2 sobre la base desechable.

Propósito: demostrar con ``MigrationExecutor`` que ``iot.0002`` (FK compuestas),
``iot.0001`` y ``trazabilidad.0009`` se aplican, se revierten y se vuelven a
aplicar, y que la reversión elimina exactamente lo que crea.
Contexto: ``transaction=True`` porque son cambios de esquema (DDL con
autocommit en MariaDB). Cada prueba deja la base en los nodos finales del grafo.
Solo mediante el orquestador desechable.
"""
import importlib

import pytest
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

pytestmark = pytest.mark.django_db(transaction=True)

IOT_INITIAL = ("iot", "0001_initial")
IOT_FKS = ("iot", "0002_composite_farm_fks")
IOT_ZERO = ("iot", None)
TRAZ_BEFORE = ("trazabilidad", "0008_alter_auditevent_event_type")
TRAZ_IOT = ("trazabilidad", "0009_alter_auditevent_event_type")
IOT_TABLES = {"iot_gateways", "iot_coordinators", "iot_sensors", "iot_coordinator_gateways"}
COMPOSITE_FKS = {"iot_sensor_coord_farm_fk", "iot_cg_coord_farm_fk", "iot_cg_gw_farm_fk"}


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(target) -> None:
    _executor().migrate([target])


def _restore_leaves() -> None:
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _tables() -> set:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor)) & IOT_TABLES


def _composite_fks() -> set:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE()"
        )
        return {row[0] for row in cursor.fetchall()} & COMPOSITE_FKS


def test_iot_migrations_roundtrip():
    try:
        assert _tables() == IOT_TABLES
        assert _composite_fks() == COMPOSITE_FKS

        _migrate(IOT_INITIAL)
        assert _composite_fks() == set()
        assert _tables() == IOT_TABLES

        _migrate(IOT_ZERO)
        assert _tables() == set()

        _migrate(IOT_FKS)
        assert _tables() == IOT_TABLES
        assert _composite_fks() == COMPOSITE_FKS
    finally:
        _restore_leaves()


def test_trazabilidad_0009_roundtrip_keeps_events():
    from apps.trazabilidad.models import AuditEvent

    try:
        AuditEvent.objects.create(event_type="farm_created", entity_type="farm", entity_id="1")
        _migrate(TRAZ_BEFORE)
        _migrate(TRAZ_IOT)
        assert AuditEvent.objects.filter(event_type="farm_created").count() == 1
    finally:
        _restore_leaves()



# --- Idempotencia y recuperación de iot.0002 (IoT-02.2-R2) ---

FKS_MODULE = importlib.import_module("apps.iot.migrations.0002_composite_farm_fks")
ALIEN = "iot_cg_gw_farm_fk"


def _run(function, *, fail_on_statement=None) -> None:
    """Ejecuta una función de la migración con un editor de esquema real.

    ``fail_on_statement`` simula una caída antes de la sentencia N (1-based).
    """
    with connection.schema_editor(atomic=False) as editor:
        if fail_on_statement is not None:
            original = editor.execute
            calls = {"n": 0}

            def failing(sql, params=()):
                calls["n"] += 1
                if calls["n"] == fail_on_statement:
                    raise RuntimeError("caída simulada")
                return original(sql, params)

            editor.execute = failing
        function(None, editor)


def _ddl(sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)


def _recorded() -> set:
    return set(MigrationRecorder(connection).applied_migrations())


def test_interrupted_application_leaves_partial_state_and_retry_completes():
    try:
        _migrate(IOT_INITIAL)
        with pytest.raises(RuntimeError, match="caída simulada"):
            _run(FKS_MODULE.add_composite_fks, fail_on_statement=2)
        assert _composite_fks() == {"iot_sensor_coord_farm_fk"}
        assert IOT_FKS not in _recorded()

        _migrate(IOT_FKS)  # reintento normal de migrate
        assert _composite_fks() == COMPOSITE_FKS
        assert IOT_FKS in _recorded()
    finally:
        _restore_leaves()


def test_application_is_idempotent_when_all_fks_exist():
    try:
        assert _composite_fks() == COMPOSITE_FKS
        _run(FKS_MODULE.add_composite_fks)
        assert _composite_fks() == COMPOSITE_FKS
    finally:
        _restore_leaves()


def test_reverse_skips_missing_fks():
    try:
        _ddl("ALTER TABLE `iot_coordinator_gateways` DROP FOREIGN KEY `iot_cg_coord_farm_fk`")
        _migrate(IOT_INITIAL)
        assert _composite_fks() == set()
        assert IOT_FKS not in _recorded()
    finally:
        _restore_leaves()


def test_homonymous_constraint_on_other_table_is_not_touched():
    alien_sql = (
        f"ALTER TABLE `iot_sensors` ADD CONSTRAINT `{ALIEN}` "
        "FOREIGN KEY (`coordinator_id`) REFERENCES `iot_coordinators` (`id`)"
    )
    try:
        _migrate(IOT_INITIAL)
        _ddl(alien_sql)
        # La búsqueda filtra por tabla: la propia figura como ausente.
        assert FKS_MODULE.fk_definition(connection, "iot_coordinator_gateways", ALIEN) is None
        _run(FKS_MODULE.drop_composite_fks)
        assert FKS_MODULE.fk_definition(connection, "iot_sensors", ALIEN) is not None
        # En MariaDB el nombre es único por esquema: crear la propia falla y la
        # ajena sigue intacta.
        with pytest.raises(DatabaseError):
            _run(FKS_MODULE.add_composite_fks)
        assert FKS_MODULE.fk_definition(connection, "iot_sensors", ALIEN) == [
            ("coordinator_id", "iot_coordinators", "id")
        ]
    finally:
        if FKS_MODULE.fk_definition(connection, "iot_sensors", ALIEN) is not None:
            _ddl(f"ALTER TABLE `iot_sensors` DROP FOREIGN KEY `{ALIEN}`")
        _restore_leaves()
    assert _composite_fks() == COMPOSITE_FKS


def test_same_name_with_other_definition_is_refused():
    alien_sql = (
        f"ALTER TABLE `iot_coordinator_gateways` ADD CONSTRAINT `{ALIEN}` "
        "FOREIGN KEY (`gateway_id`) REFERENCES `iot_gateways` (`id`)"
    )
    alien = [("gateway_id", "iot_gateways", "id")]
    try:
        _migrate(IOT_INITIAL)
        _ddl(alien_sql)
        with pytest.raises(RuntimeError, match="definición distinta"):
            _run(FKS_MODULE.add_composite_fks)
        assert FKS_MODULE.fk_definition(connection, "iot_coordinator_gateways", ALIEN) == alien
        with pytest.raises(RuntimeError, match="definición distinta"):
            _run(FKS_MODULE.drop_composite_fks)
        assert FKS_MODULE.fk_definition(connection, "iot_coordinator_gateways", ALIEN) == alien
    finally:
        if FKS_MODULE.fk_definition(connection, "iot_coordinator_gateways", ALIEN) == alien:
            _ddl(f"ALTER TABLE `iot_coordinator_gateways` DROP FOREIGN KEY `{ALIEN}`")
        _restore_leaves()
    assert _composite_fks() == COMPOSITE_FKS


def test_expected_definitions_match_created_fks():
    for name, table, column, target in FKS_MODULE.COMPOSITE_FKS:
        assert FKS_MODULE.fk_definition(connection, table, name) == (
            FKS_MODULE.expected_definition("mysql", column, target)
        )
