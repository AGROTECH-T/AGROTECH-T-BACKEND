# ruff: noqa: S101
"""Ida y vuelta de las migraciones de IoT-02.2 y IoT-03.2 sobre la base desechable.

Propósito: demostrar con ``MigrationExecutor`` que ``iot.0002`` (FK compuestas),
``iot.0001``, ``iot.0004`` (credenciales de coordinador), ``iot.0005``
(renombrado de la versión de clave maestra) y las migraciones de
``trazabilidad`` se aplican, se revierten y se vuelven a aplicar, y que la
reversión elimina exactamente lo que crea.
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


@pytest.fixture(autouse=True)
def allow_destructive_rollback_on_disposable_database(settings):
    """VALIDATION-02: these roundtrips cross the 0011/0013/0015 rollback barriers.

    Only valid because the IoT disposable profile proves the database is a
    throwaway MariaDB 11.8 before any test runs.
    """
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = True

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


# --- IoT-03.1: credenciales de gateway y eventos ---

IOT_CREDENTIALS = ("iot", "0003_gatewaycredential")
IOT_COORD_CREDENTIALS_BASE = (
    "iot",
    "0004_coordinatorbootwindow_coordinatorcredential",
)
IOT_COORD_CREDENTIALS = ("iot", "0005_rename_key_version_master_key_version")
IOT_PERSISTENCE = ("iot", "0008_iot_persistence_composite_fks")
IOT_GENERATIONS = ("iot", "0009_coordinator_key_generation")
TRAZ_CREDENTIALS = ("trazabilidad", "0010_alter_auditevent_event_type")
TRAZ_COORD_CREDENTIALS = ("trazabilidad", "0011_alter_auditevent_event_type")
TRAZ_GENERATIONS = ("trazabilidad", "0012_alter_auditevent_event_type")
CREDENTIALS_TABLE = "iot_gateway_credentials"
COORD_CREDENTIALS_TABLE = "iot_coordinator_credentials"
COORD_WINDOW_TABLE = "iot_coordinator_boot_window"
COORD_GENERATION_TABLE = "iot_coordinator_key_generation"


def _has_table(name: str) -> bool:
    with connection.cursor() as cursor:
        return name in connection.introspection.table_names(cursor)


def _columns(name: str) -> set:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [name],
        )
        return {row[0] for row in cursor.fetchall()}


def test_iot_0003_roundtrip():
    try:
        assert _has_table(CREDENTIALS_TABLE)
        _migrate(IOT_FKS)
        assert not _has_table(CREDENTIALS_TABLE)
        assert _composite_fks() == COMPOSITE_FKS  # revertir 0003 no toca 0002
        _migrate(IOT_CREDENTIALS)
        assert _has_table(CREDENTIALS_TABLE)
        assert IOT_CREDENTIALS in _recorded()
    finally:
        _restore_leaves()


def test_iot_0004_roundtrip():
    try:
        assert _has_table(COORD_CREDENTIALS_TABLE)
        assert _has_table(COORD_WINDOW_TABLE)
        _migrate(IOT_CREDENTIALS)  # revierte 0005 y 0004
        assert not _has_table(COORD_CREDENTIALS_TABLE)
        assert not _has_table(COORD_WINDOW_TABLE)
        assert _has_table(CREDENTIALS_TABLE)  # 0003 sigue aplicada
        _migrate(IOT_COORD_CREDENTIALS_BASE)
        assert _has_table(COORD_CREDENTIALS_TABLE)
        assert _has_table(COORD_WINDOW_TABLE)
        assert IOT_TABLES <= _tables()
        assert IOT_COORD_CREDENTIALS_BASE in _recorded()
    finally:
        _restore_leaves()


def test_iot_0005_renames_master_key_version():
    try:
        columns = _columns(COORD_CREDENTIALS_TABLE)
        assert "master_key_version" in columns
        assert "key_version" in columns  # generación de 0009
        _migrate(IOT_COORD_CREDENTIALS_BASE)
        columns = _columns(COORD_CREDENTIALS_TABLE)
        assert "key_version" in columns  # nombre histórico de la clave maestra
        assert "master_key_version" not in columns
        _migrate(IOT_COORD_CREDENTIALS)
        columns = _columns(COORD_CREDENTIALS_TABLE)
        assert "master_key_version" in columns
        assert "key_version" not in columns
        assert IOT_COORD_CREDENTIALS in _recorded()
    finally:
        _restore_leaves()


def _check_clauses(table: str) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CONSTRAINT_NAME, CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table],
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def test_iot_0009_roundtrip_preserves_credentials_and_generations():
    from django.utils import timezone

    from apps.farms.models import Farm
    from apps.iot.models import Coordinator, CoordinatorCredential

    farm = Farm.objects.create(name="Finca generaciones")
    coordinator = Coordinator.objects.create(
        farm_id=farm.pk, uid="CO-ABCDEFGHJK", name="Coordinador"
    )
    credential = CoordinatorCredential.objects.create(
        coordinator_id=coordinator.pk,
        ciphertext=bytes(range(48)),
        nonce=bytes(range(12)),
        master_key_version=1,
        key_version=None,
        active_slot=1,
        issued_at=timezone.now(),
    )
    credential_id = credential.pk
    ciphertext = bytes(credential.ciphertext)
    nonce = bytes(credential.nonce)
    try:
        assert _has_table(COORD_GENERATION_TABLE)
        assert "key_version" in _columns(COORD_CREDENTIALS_TABLE)
        assert "iot_coordcred_generation_range" in _check_clauses(
            COORD_CREDENTIALS_TABLE
        )
        assert "iot_coordkeygen_range" in _check_clauses(COORD_GENERATION_TABLE)

        _migrate(IOT_PERSISTENCE)
        assert not _has_table(COORD_GENERATION_TABLE)
        assert "key_version" not in _columns(COORD_CREDENTIALS_TABLE)

        _migrate(IOT_GENERATIONS)
        assert _has_table(COORD_GENERATION_TABLE)
        assert "key_version" in _columns(COORD_CREDENTIALS_TABLE)
        credential = CoordinatorCredential.objects.get(pk=credential_id)
        assert credential.key_version is None
        assert bytes(credential.ciphertext) == ciphertext
        assert bytes(credential.nonce) == nonce
        assert credential.master_key_version == 1
    finally:
        _restore_leaves()


def test_trazabilidad_0012_roundtrip_keeps_generation_event():
    from apps.trazabilidad.models import AuditEvent, EventType

    try:
        AuditEvent.objects.create(
            event_type=EventType.IOT_COORDINATOR_CREDENTIAL_GENERATION_EXHAUSTED,
            entity_type="iot_coordinator_credential",
            entity_id="1",
        )
        _migrate(TRAZ_COORD_CREDENTIALS)
        _migrate(TRAZ_GENERATIONS)
        assert (
            AuditEvent.objects.filter(
                event_type=EventType.IOT_COORDINATOR_CREDENTIAL_GENERATION_EXHAUSTED
            ).count()
            == 1
        )
    finally:
        _restore_leaves()


def test_trazabilidad_0010_roundtrip_keeps_events():
    from apps.trazabilidad.models import AuditEvent

    try:
        AuditEvent.objects.create(
            event_type="iot_gateway_created", entity_type="iot_gateway", entity_id="GW-ABCDEFGHJK"
)
        _migrate(TRAZ_IOT)
        _migrate(TRAZ_CREDENTIALS)
        assert AuditEvent.objects.filter(event_type="iot_gateway_created").count() == 1
    finally:
        _restore_leaves()


def test_trazabilidad_0011_roundtrip_keeps_events():
    from apps.trazabilidad.models import AuditEvent

    try:
        AuditEvent.objects.create(
            event_type="iot_gateway_created", entity_type="iot_gateway", entity_id="GW-ABCDEFGHJK"
        )
        _migrate(TRAZ_CREDENTIALS)  # revierte 0011
        _migrate(TRAZ_COORD_CREDENTIALS)
        assert AuditEvent.objects.filter(event_type="iot_gateway_created").count() == 1
    finally:
        _restore_leaves()


# --- B-01 (IoT-03.2-R3): la migración 0005 conserva los datos ---

PRESERVED_VERSIONS = (7, 65535)


def _historical(node, app_label, model_name):
    return _executor().loader.project_state(node).apps.get_model(app_label, model_name)


def _version_column(column: str) -> list:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {connection.ops.quote_name(column)} "  # noqa: S608 - nombre fijo
            f"FROM {connection.ops.quote_name(COORD_CREDENTIALS_TABLE)} ORDER BY id"
        )
        return [row[0] for row in cursor.fetchall()]


def _version_check_clause() -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND CONSTRAINT_NAME = %s",
            [COORD_CREDENTIALS_TABLE, "iot_coordcred_key_version_valid"],
        )
        row = cursor.fetchone()
        return row[0] if row else ""


def _seed_credentials_at_0004():
    """Crea credenciales con ``key_version`` usando los modelos históricos de 0004."""
    from django.utils import timezone

    from apps.farms.models import Farm

    # La finca con el modelo actual: farms no se revierte y su estado histórico
    # en iot.0004 es farms.0001 (dependencias mínimas), sin ``is_active``.
    coordinator_model = _historical(IOT_COORD_CREDENTIALS_BASE, "iot", "Coordinator")
    credential_model = _historical(IOT_COORD_CREDENTIALS_BASE, "iot", "CoordinatorCredential")
    farm = Farm.objects.create(name="Finca migración")
    for index, version in enumerate(PRESERVED_VERSIONS):
        coordinator = coordinator_model.objects.create(
            uid=f"CO-ABCDEFGHJ{'KM'[index]}", farm_id=farm.pk, name=f"C{index}"
        )
        credential_model.objects.create(
            coordinator_id=coordinator.pk,
            ciphertext=bytes(48),
            nonce=bytes(12),
            key_version=version,
            active_slot=1,
            issued_at=timezone.now(),
        )


def test_iot_0005_preserves_master_key_version_values():
    try:
        _migrate(IOT_COORD_CREDENTIALS_BASE)
        assert "key_version" in _columns(COORD_CREDENTIALS_TABLE)
        _seed_credentials_at_0004()
        assert _version_column("key_version") == list(PRESERVED_VERSIONS)

        _migrate(IOT_COORD_CREDENTIALS)
        columns = _columns(COORD_CREDENTIALS_TABLE)
        assert "master_key_version" in columns and "key_version" not in columns
        assert _version_column("master_key_version") == list(PRESERVED_VERSIONS)
        clause = _version_check_clause()
        assert "master_key_version" in clause and "65535" in clause

        _migrate(IOT_COORD_CREDENTIALS_BASE)  # reversión
        columns = _columns(COORD_CREDENTIALS_TABLE)
        assert "key_version" in columns and "master_key_version" not in columns
        assert _version_column("key_version") == list(PRESERVED_VERSIONS)

        _migrate(IOT_COORD_CREDENTIALS)  # reaplicación
        assert _version_column("master_key_version") == list(PRESERVED_VERSIONS)
    finally:
        _restore_leaves()
