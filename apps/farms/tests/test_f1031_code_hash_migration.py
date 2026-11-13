# ruff: noqa: S101
"""Ida y vuelta de ``farms.0005_farminvitation_code_hash`` (F10.3.1).

Propósito: aplicar, revertir y reaplicar la migración con
``MigrationExecutor`` en la base desechable, capturar su SQL y comprobar que
las invitaciones existentes, su ``token_hash`` y el resto de columnas se
conservan.
Contexto: solo debe ejecutarse mediante el orquestador desechable; la guarda
de identidad rechaza cualquier base cuyo nombre no empiece por
``agrotech_permtest_``. Al terminar, la base vuelve a los nodos finales.
"""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = ("farms", "0004_farminvitation")
TARGET = ("farms", "0005_farminvitation_code_hash")
TABLE = "farm_invitations"
LEGACY_TOKEN_HASHES = ("a" * 64, "b" * 64, "")
SNAPSHOT_FIELDS = (
    "pk",
    "farm_id",
    "email",
    "pending_email",
    "role",
    "status",
    "token_hash",
    "expires_at",
    "delivery_pending",
    "accepted_at",
    "cancelled_at",
)


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(target) -> None:
    _executor().migrate([target])


def _restore_leaves() -> None:
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _applied() -> set:
    return set(MigrationRecorder(connection).applied_migrations())


def _model_at(node):
    return _executor().loader.project_state(node).apps.get_model("farms", "FarmInvitation")


def _farm_model_at(node):
    return _executor().loader.project_state(node).apps.get_model("farms", "Farm")


def _columns() -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [TABLE],
        )
        return {name: (ctype, nullable, default) for name, ctype, nullable, default in cursor}


def _unique_indexes_on(column: str) -> list:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s AND NON_UNIQUE = 0",
            [TABLE, column],
        )
        return [row[0] for row in cursor]


def _sql(backwards: bool = False) -> str:
    out = StringIO()
    args = list(TARGET) + (["--backwards"] if backwards else [])
    call_command("sqlmigrate", *args, stdout=out, no_color=True)
    return out.getvalue().strip()


def _snapshot(model) -> list:
    return sorted(model.objects.values_list(*SNAPSHOT_FIELDS))


@pytest.fixture
def server_identity(record_testsuite_property):
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), VERSION(), @@hostname, @@port")
        database, version, hostname, port = cursor.fetchone()
    assert str(database).startswith("agrotech_permtest_")
    assert str(hostname).startswith("agrotech-permtest-")
    record_testsuite_property("f1031_database", str(database))
    record_testsuite_property("f1031_server", f"{version} host={hostname} port={port}")
    return database


@pytest.fixture
def restore_graph():
    """Devuelve la base a los nodos finales aunque la prueba falle."""
    yield
    _restore_leaves()


def test_model_and_migrations_are_in_sync(server_identity):
    out = StringIO()
    call_command("makemigrations", "farms", "--check", "--dry-run", stdout=out, no_color=True)
    assert "No changes detected" in out.getvalue()


def test_code_hash_migration_roundtrip(server_identity, restore_graph, record_testsuite_property):
    # Estado anterior: invitaciones existentes con token_hash.
    _migrate(BEFORE)
    assert TARGET not in _applied()
    assert "code_hash" not in _columns()
    before_columns = _columns()

    Farm = _farm_model_at(BEFORE)
    OldInvitation = _model_at(BEFORE)
    farm = Farm.objects.create(name="Finca migración")
    statuses = ("pending", "accepted", "cancelled")
    for index, (status, token_hash) in enumerate(zip(statuses, LEGACY_TOKEN_HASHES, strict=True)):
        email = f"legado{index}@example.com"
        OldInvitation.objects.create(
            farm_id=farm.pk,
            email=email,
            pending_email=email if status == "pending" else None,
            role="operador",
            status=status,
            token_hash=token_hash,
            expires_at=timezone.now() + timedelta(days=7),
        )
    snapshot = _snapshot(OldInvitation)
    assert len(snapshot) == 3

    # SQL (sqlmigrate no aplica nada).
    forward_sql = _sql()
    backward_sql = _sql(backwards=True)
    record_testsuite_property("sql_farms_0005_forward", forward_sql)
    record_testsuite_property("sql_farms_0005_backward", backward_sql)
    assert TARGET not in _applied()
    assert "`farm_invitations`" in forward_sql and "`code_hash`" in forward_sql
    assert "ADD COLUMN" in forward_sql.upper() and "UNIQUE" in forward_sql.upper()
    assert "UPDATE" not in forward_sql.upper()
    assert "token_hash" not in forward_sql
    assert "DROP COLUMN `code_hash`" in backward_sql
    assert "token_hash" not in backward_sql

    # Aplicación: solo aparece code_hash, NULL y único; los datos no cambian.
    _migrate(TARGET)
    assert TARGET in _applied()
    after_columns = _columns()
    assert set(after_columns) - set(before_columns) == {"code_hash"}
    assert {k: v for k, v in after_columns.items() if k != "code_hash"} == before_columns
    ctype, nullable, default = after_columns["code_hash"]
    record_testsuite_property(
        "farms_0005_column", f"type={ctype} nullable={nullable} default={default}"
    )
    assert ctype == "varchar(64)"
    assert nullable == "YES"
    assert default in (None, "NULL")
    assert _unique_indexes_on("code_hash")

    NewInvitation = _model_at(TARGET)
    assert _snapshot(NewInvitation) == snapshot
    assert NewInvitation.objects.filter(code_hash__isnull=True).count() == 3
    NewInvitation.objects.filter(email="legado0@example.com").update(code_hash="c" * 64)

    # Reversión: desaparece solo la columna; invitaciones y token_hash intactos.
    _migrate(BEFORE)
    assert TARGET not in _applied()
    assert _columns() == before_columns
    assert not _unique_indexes_on("code_hash")
    OldInvitation = _model_at(BEFORE)
    assert _snapshot(OldInvitation) == snapshot

    # Reaplicación: la columna vuelve vacía (NULL) para todas.
    _migrate(TARGET)
    assert TARGET in _applied()
    NewInvitation = _model_at(TARGET)
    assert _snapshot(NewInvitation) == snapshot
    assert NewInvitation.objects.filter(code_hash__isnull=True).count() == 3
    assert sorted(NewInvitation.objects.values_list("token_hash", flat=True)) == sorted(
        LEGACY_TOKEN_HASHES
    )


def test_reverting_farms_0005_does_not_touch_other_apps(server_identity, restore_graph):
    applied_before = _applied()
    _migrate(BEFORE)
    applied = _applied()
    assert applied_before - applied == {TARGET}
    _restore_leaves()
    assert _applied() == applied_before
