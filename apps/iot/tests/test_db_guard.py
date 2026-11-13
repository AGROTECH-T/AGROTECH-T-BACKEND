# ruff: noqa: S101
"""Pruebas de la exigencia de MariaDB para IoT (IoT-02.1).

Propósito: verificar que ``require_mariadb`` acepta solo MariaDB y falla de
forma cerrada con otros motores, sin abrir conexiones reales ni modificar la
configuración. Las conexiones se sustituyen por dobles de prueba.
"""
import copy

import pytest
from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.iot import db_guard
from apps.iot.db_guard import MARIADB_REQUIRED_MESSAGE, require_mariadb


class FakeConnection:
    """Conexión simulada que registra lecturas y prohíbe escrituras."""

    def __init__(self, vendor, mysql_is_mariadb=None):
        object.__setattr__(self, "vendor", vendor)
        object.__setattr__(self, "read_attributes", [])
        if mysql_is_mariadb is not None:
            object.__setattr__(self, "_mariadb", mysql_is_mariadb)

    def __getattr__(self, name):
        if name == "mysql_is_mariadb" and "_mariadb" in vars(self):
            self.read_attributes.append(name)
            return vars(self)["_mariadb"]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        raise AssertionError(f"la guarda no debe modificar la conexión ({name})")


@pytest.fixture
def fake_connections(monkeypatch):
    registry = {}
    monkeypatch.setattr(db_guard, "connections", registry)
    return registry


def test_accepts_mariadb(fake_connections):
    fake_connections["default"] = FakeConnection("mysql", mysql_is_mariadb=True)
    assert require_mariadb() is None


def test_rejects_postgresql_without_inspecting_mariadb_flag(fake_connections):
    connection = FakeConnection("postgresql")
    fake_connections["default"] = connection
    with pytest.raises(ImproperlyConfigured, match=MARIADB_REQUIRED_MESSAGE):
        require_mariadb()
    assert connection.read_attributes == []


def test_rejects_sqlite(fake_connections):
    fake_connections["default"] = FakeConnection("sqlite")
    with pytest.raises(ImproperlyConfigured, match=MARIADB_REQUIRED_MESSAGE):
        require_mariadb()


def test_rejects_mysql_that_is_not_mariadb(fake_connections):
    connection = FakeConnection("mysql", mysql_is_mariadb=False)
    fake_connections["default"] = connection
    with pytest.raises(ImproperlyConfigured, match=MARIADB_REQUIRED_MESSAGE):
        require_mariadb()
    assert connection.read_attributes == ["mysql_is_mariadb"]


def test_rejects_mysql_backend_without_mariadb_flag(fake_connections):
    fake_connections["default"] = FakeConnection("mysql")
    with pytest.raises(ImproperlyConfigured):
        require_mariadb()


def test_rejects_truthy_non_boolean_flag(fake_connections):
    fake_connections["default"] = FakeConnection("mysql", mysql_is_mariadb="yes")
    with pytest.raises(ImproperlyConfigured):
        require_mariadb()


def test_checks_only_the_requested_alias(fake_connections):
    other = FakeConnection("postgresql")
    target = FakeConnection("mysql", mysql_is_mariadb=True)
    fake_connections["default"] = other
    fake_connections["iot_check"] = target
    require_mariadb(using="iot_check")
    assert target.read_attributes == ["mysql_is_mariadb"]
    assert other.read_attributes == []


def test_does_not_alter_global_configuration(fake_connections):
    before = copy.deepcopy(settings.DATABASES)
    fake_connections["default"] = FakeConnection("postgresql")
    with pytest.raises(ImproperlyConfigured):
        require_mariadb()
    fake_connections["default"] = FakeConnection("mysql", mysql_is_mariadb=True)
    require_mariadb()
    assert settings.DATABASES == before
    assert list(settings.DATABASES) == ["default"]
    assert not getattr(settings, "DATABASE_ROUTERS", [])


def test_guard_is_not_run_at_startup():
    """La app IoT no ejecuta la guarda al arrancar: otros motores siguen arrancando."""
    config = apps.get_app_config("iot")
    assert type(config).ready is AppConfig.ready
