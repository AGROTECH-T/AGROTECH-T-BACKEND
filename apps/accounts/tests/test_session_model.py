"""Verifica el modelo Session y la migración 0004 sin acceder a la base.

Estas pruebas son estáticas: inspeccionan la definición del modelo y la
estructura/orden de las operaciones de la migración, y comprueban la función de
backfill con un doble en memoria. No se conectan a MariaDB ni ejecutan SQL.
"""

import importlib

from django.db import migrations, models
from django.db.models import F

from apps.accounts.models import Session

MIGRATION_MODULE = (
    "apps.accounts.migrations."
    "0004_session_closed_at_session_created_at_session_id_and_more"
)


def _migration():
    return importlib.import_module(MIGRATION_MODULE)


# --- Modelo ---


def test_id_is_big_autofield_primary_key():
    field = Session._meta.get_field("id")
    assert isinstance(field, models.BigAutoField)
    assert field.primary_key is True


def test_user_is_foreign_key_with_sessions_related_name():
    field = Session._meta.get_field("user")
    assert isinstance(field, models.ForeignKey)
    assert field.primary_key is False
    assert field.remote_field.related_name == "sessions"


def test_session_key_is_nullable_unique_char40():
    field = Session._meta.get_field("session_key")
    assert isinstance(field, models.CharField)
    assert field.max_length == 40
    assert field.null is True
    assert field.unique is True


def test_token_hash_is_nullable_unique_char64():
    field = Session._meta.get_field("token_hash")
    assert isinstance(field, models.CharField)
    assert field.max_length == 64
    assert field.null is True
    assert field.unique is True


def test_timestamps_are_datetime_with_expected_nullability():
    last_seen = Session._meta.get_field("last_seen")
    created_at = Session._meta.get_field("created_at")
    closed_at = Session._meta.get_field("closed_at")
    assert isinstance(last_seen, models.DateTimeField)
    assert isinstance(created_at, models.DateTimeField)
    assert isinstance(closed_at, models.DateTimeField)
    assert last_seen.null is False
    assert created_at.null is False
    assert closed_at.null is True
    assert closed_at.blank is True


def test_meta_defines_db_table_and_composite_index():
    assert Session._meta.db_table == "sessions"
    indexes = {index.name: index for index in Session._meta.indexes}
    assert "sessions_user_closed_idx" in indexes
    assert indexes["sessions_user_closed_idx"].fields == ["user", "closed_at"]


# --- Migración ---


def _operation_by_name(migration, name):
    return [op for op in migration.Migration.operations if op.name == name]


def test_migration_alters_user_to_fk_before_adding_pk():
    """La PK se retira de user antes de crear la nueva PK id (orden comprobado)."""
    names = [getattr(op, "name", None) for op in _migration().Migration.operations]
    assert names.index("user") < names.index("id")


def test_migration_includes_backfill_for_created_at():
    migration = _migration()
    run_python = [
        op for op in migration.Migration.operations
        if isinstance(op, migrations.RunPython)
    ]
    assert len(run_python) == 1
    assert run_python[0].code.__name__ == "backfill_created_at"


def test_migration_creates_composite_index():
    add_index = [
        op for op in _migration().Migration.operations
        if isinstance(op, migrations.AddIndex)
    ]
    assert len(add_index) == 1
    assert add_index[0].index.name == "sessions_user_closed_idx"


def test_backfill_created_at_copies_last_seen():
    """El backfill rellena created_at con el valor de last_seen (no una constante)."""
    calls = {}

    class _FakeManager:
        def filter(self, **kwargs):
            calls["filter"] = kwargs
            return self

        def update(self, **kwargs):
            calls["update"] = kwargs

    class _FakeSession:
        objects = _FakeManager()

    fake_apps = type("Apps", (), {"get_model": lambda self, a, m: _FakeSession})()

    _migration().backfill_created_at(fake_apps, None)

    assert calls["filter"] == {"created_at__isnull": True}
    assert isinstance(calls["update"]["created_at"], F)
    assert calls["update"]["created_at"].name == "last_seen"
