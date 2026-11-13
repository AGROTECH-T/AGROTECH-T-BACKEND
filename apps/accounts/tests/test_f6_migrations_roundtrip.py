# ruff: noqa: S101
"""Ida y vuelta de las migraciones de BUILD F6 sobre la base desechable.

Propósito: demostrar, con ``MigrationExecutor``, la aplicación, reversión y
reaplicación de ``accounts.0006_user_can_create_farms`` y
``trazabilidad.0008_alter_auditevent_event_type``, capturar su SQL con
``sqlmigrate`` y comprobar la conservación de datos.
Contexto: solo debe ejecutarse mediante el orquestador desechable
(``-Validate``); la guarda de ``conftest.py`` rechaza cualquier otro destino.
Usa ``transaction=True`` porque las migraciones cambian el esquema (DDL con
autocommit en MariaDB). El SQL y la identidad del servidor se registran como
propiedades del ``junit.xml`` (fuera del repositorio). Al terminar, cada prueba
deja la base en los nodos finales del grafo, sin lógica de esquema manual.
"""
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

pytestmark = pytest.mark.django_db(transaction=True)

ACCOUNTS_BEFORE = ("accounts", "0005_logincode_delivery_fields")
ACCOUNTS_F6 = ("accounts", "0006_user_can_create_farms")
TRAZ_BEFORE = ("trazabilidad", "0007_alter_auditevent_event_type")
TRAZ_F6 = ("trazabilidad", "0008_alter_auditevent_event_type")
NEW_EVENT_TYPES = ("farm_creation_granted", "farm_creation_revoked")
OLD_EVENT_TYPES = ("farm_created", "session_opened", "invitation_accepted")


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(target) -> MigrationExecutor:
    """Migra solo hasta ``target`` y devuelve un ejecutor con el grafo fresco."""
    executor = _executor()
    executor.migrate([target])
    return _executor()


def _restore_leaves() -> None:
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _applied() -> set:
    return set(MigrationRecorder(connection).applied_migrations())


def _apps_at(node):
    return _executor().loader.project_state(node).apps


def _columns(table: str) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table],
        )
        return {name: (ctype, nullable, default) for name, ctype, nullable, default in cursor}


def _sql(app: str, name: str, backwards: bool = False) -> str:
    out = StringIO()
    args = [app, name] + (["--backwards"] if backwards else [])
    call_command("sqlmigrate", *args, stdout=out, no_color=True)
    return out.getvalue().strip()


@pytest.fixture
def server_identity(record_testsuite_property):
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), VERSION(), @@hostname, @@port")
        database, version, hostname, port = cursor.fetchone()
    assert str(database).startswith("agrotech_permtest_")
    assert str(hostname).startswith("agrotech-permtest-")
    record_testsuite_property("roundtrip_database", str(database))
    record_testsuite_property("roundtrip_server", f"{version} host={hostname} port={port}")
    return database


@pytest.fixture
def restore_graph():
    """Garantiza que la base vuelve a los nodos finales aunque la prueba falle."""
    yield
    _restore_leaves()


def test_accounts_0006_roundtrip(server_identity, restore_graph, record_testsuite_property):
    # Estado anterior reproducible.
    _migrate(ACCOUNTS_BEFORE)
    assert ACCOUNTS_F6 not in _applied()
    assert "can_create_farms" not in _columns("users")

    OldUser = _apps_at(ACCOUNTS_BEFORE).get_model("accounts", "User")
    rows = {
        "71000001": {"first_name": "Ana", "correo": "a@example.com"},
        "71000002": {"first_name": "Beto", "correo": "b@example.com"},
    }
    for identification, data in rows.items():
        OldUser.objects.create(
            identification=identification,
            last_name="Prueba",
            phone="3000000000",
            password_hash="hash-no-real",
            email_verified=identification.endswith("1"),
            **data,
        )
    snapshot = {
        u.identification: (u.pk, u.first_name, u.correo, u.email_verified, u.password_hash)
        for u in OldUser.objects.filter(identification__in=rows)
    }

    # SQL (no aplica nada: sqlmigrate solo imprime).
    forward_sql = _sql(*ACCOUNTS_F6)
    backward_sql = _sql(*ACCOUNTS_F6, backwards=True)
    record_testsuite_property("sql_accounts_0006_forward", forward_sql)
    record_testsuite_property("sql_accounts_0006_backward", backward_sql)
    assert ACCOUNTS_F6 not in _applied()
    assert "can_create_farms" in forward_sql and "`users`" in forward_sql
    assert "DROP COLUMN" in backward_sql.upper()

    # Aplicación.
    _migrate(ACCOUNTS_F6)
    assert ACCOUNTS_F6 in _applied()
    columns = _columns("users")
    assert "can_create_farms" in columns
    ctype, nullable, default = columns["can_create_farms"]
    record_testsuite_property("accounts_0006_column", f"type={ctype} nullable={nullable} default={default}")
    assert nullable == "NO"
    NewUser = _apps_at(ACCOUNTS_F6).get_model("accounts", "User")
    assert set(NewUser.objects.filter(identification__in=rows).values_list("can_create_farms", flat=True)) == {False}
    NewUser.objects.filter(identification="71000001").update(can_create_farms=True)
    values = dict(NewUser.objects.filter(identification__in=rows).values_list("identification", "can_create_farms"))
    assert values == {"71000001": True, "71000002": False}

    # Reversión: la columna (y su valor True) desaparece; las cuentas se conservan.
    _migrate(ACCOUNTS_BEFORE)
    assert ACCOUNTS_F6 not in _applied()
    assert "can_create_farms" not in _columns("users")
    OldUser = _apps_at(ACCOUNTS_BEFORE).get_model("accounts", "User")
    after_revert = {
        u.identification: (u.pk, u.first_name, u.correo, u.email_verified, u.password_hash)
        for u in OldUser.objects.filter(identification__in=rows)
    }
    assert after_revert == snapshot

    # Reaplicación: la columna vuelve y todas las cuentas quedan en False.
    _migrate(ACCOUNTS_F6)
    assert ACCOUNTS_F6 in _applied()
    assert "can_create_farms" in _columns("users")
    NewUser = _apps_at(ACCOUNTS_F6).get_model("accounts", "User")
    reapplied = {
        u.identification: (u.pk, u.first_name, u.correo, u.email_verified, u.password_hash)
        for u in NewUser.objects.filter(identification__in=rows)
    }
    assert reapplied == snapshot
    assert set(NewUser.objects.filter(identification__in=rows).values_list("can_create_farms", flat=True)) == {False}


def test_trazabilidad_0008_roundtrip(server_identity, restore_graph, record_testsuite_property):
    _migrate(TRAZ_BEFORE)
    assert TRAZ_F6 not in _applied()
    before_columns = _columns("audit_events")

    OldEvent = _apps_at(TRAZ_BEFORE).get_model("trazabilidad", "AuditEvent")
    old_choices = {value for value, _label in OldEvent._meta.get_field("event_type").choices}
    assert not old_choices & set(NEW_EVENT_TYPES)
    # La columna es VARCHAR(40) sin CHECK: la base admite los valores nuevos
    # aunque el modelo histórico no los liste.
    for index, event_type in enumerate(OLD_EVENT_TYPES + NEW_EVENT_TYPES):
        OldEvent.objects.create(
            event_type=event_type,
            entity_type="Roundtrip",
            entity_id=f"rt-{index}",
            reason="prueba de ida y vuelta",
            metadata={"roundtrip": index},
        )
    snapshot = sorted(
        OldEvent.objects.filter(entity_type="Roundtrip").values_list(
            "pk", "event_type", "entity_id", "reason", "metadata"
        )
    )
    assert len(snapshot) == 5

    forward_sql = _sql(*TRAZ_F6)
    backward_sql = _sql(*TRAZ_F6, backwards=True)
    record_testsuite_property("sql_trazabilidad_0008_forward", forward_sql or "<vacío>")
    record_testsuite_property("sql_trazabilidad_0008_backward", backward_sql or "<vacío>")
    assert TRAZ_F6 not in _applied()
    # Cambiar solo `choices` no altera la columna: Django no emite DDL.
    assert "ALTER TABLE" not in forward_sql.upper()
    assert "ALTER TABLE" not in backward_sql.upper()

    # Aplicación.
    _migrate(TRAZ_F6)
    assert TRAZ_F6 in _applied()
    assert _columns("audit_events") == before_columns
    NewEvent = _apps_at(TRAZ_F6).get_model("trazabilidad", "AuditEvent")
    new_choices = {value for value, _label in NewEvent._meta.get_field("event_type").choices}
    assert set(NEW_EVENT_TYPES) <= new_choices and old_choices < new_choices
    current = sorted(
        NewEvent.objects.filter(entity_type="Roundtrip").values_list(
            "pk", "event_type", "entity_id", "reason", "metadata"
        )
    )
    assert current == snapshot

    # Reversión: los registros y sus valores se conservan; el modelo histórico
    # vuelve a no listar los dos valores nuevos.
    _migrate(TRAZ_BEFORE)
    assert TRAZ_F6 not in _applied()
    assert _columns("audit_events") == before_columns
    OldEvent = _apps_at(TRAZ_BEFORE).get_model("trazabilidad", "AuditEvent")
    reverted_choices = {value for value, _label in OldEvent._meta.get_field("event_type").choices}
    assert reverted_choices == old_choices
    assert not reverted_choices & set(NEW_EVENT_TYPES)
    reverted = sorted(
        OldEvent.objects.filter(entity_type="Roundtrip").values_list(
            "pk", "event_type", "entity_id", "reason", "metadata"
        )
    )
    assert reverted == snapshot

    # Reaplicación.
    _migrate(TRAZ_F6)
    assert TRAZ_F6 in _applied()
    NewEvent = _apps_at(TRAZ_F6).get_model("trazabilidad", "AuditEvent")
    assert set(NEW_EVENT_TYPES) <= {
        value for value, _label in NewEvent._meta.get_field("event_type").choices
    }
    final = sorted(
        NewEvent.objects.filter(entity_type="Roundtrip").values_list(
            "pk", "event_type", "entity_id", "reason", "metadata"
        )
    )
    assert final == snapshot


def test_reverting_one_app_does_not_touch_the_other(server_identity, restore_graph):
    """Revertir accounts no desaplica trazabilidad 0008 ni al revés."""
    _migrate(ACCOUNTS_BEFORE)
    applied = _applied()
    assert ACCOUNTS_F6 not in applied
    assert TRAZ_F6 in applied
    _restore_leaves()
    _migrate(TRAZ_BEFORE)
    applied = _applied()
    assert TRAZ_F6 not in applied
    assert ACCOUNTS_F6 in applied
    _restore_leaves()
    applied = _applied()
    assert {ACCOUNTS_F6, TRAZ_F6} <= applied
