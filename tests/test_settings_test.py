"""Pruebas estáticas de la configuración de pruebas sobre MariaDB.

No se conectan a la base de datos ni ejecutan migraciones: solo importan y
validan los valores de ``config.settings_test`` y de la barrera de destinos.
"""
import os
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.base.base import BaseDatabaseWrapper

import config.settings_test as test_settings
import conftest as test_guard


def _valid_default() -> dict:
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "agrotech_farms_test",
        "HOST": "127.0.0.1",
        "PORT": "3307",
        "TEST": {"NAME": "agrotech_farms_test"},
    }


def test_engine_is_mysql():
    assert test_settings.DATABASES["default"]["ENGINE"] == "django.db.backends.mysql"


def test_name_and_test_name_are_authorized():
    default = test_settings.DATABASES["default"]
    assert default["NAME"] == "agrotech_farms_test"
    assert default["TEST"]["NAME"] == "agrotech_farms_test"


def test_host_and_port_are_fixed():
    default = test_settings.DATABASES["default"]
    assert default["HOST"] == "127.0.0.1"
    assert default["PORT"] == "3307"


def test_no_default_credentials(monkeypatch):
    monkeypatch.delenv("TEST_DB_USER", raising=False)
    monkeypatch.delenv("TEST_DB_PASSWORD", raising=False)
    database = test_settings.build_test_database()
    assert database["USER"] is None
    assert database["PASSWORD"] is None


def test_credentials_come_only_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_DB_USER", "env-user")
    monkeypatch.setenv("TEST_DB_PASSWORD", "env-value")
    database = test_settings.build_test_database()
    assert database["USER"] == os.environ["TEST_DB_USER"]
    assert database["PASSWORD"] == os.environ["TEST_DB_PASSWORD"]


def test_general_env_vars_cannot_change_the_destination(monkeypatch):
    monkeypatch.setenv("DB_NAME", "agrotech_db")
    monkeypatch.setenv("DB_ENGINE", "sqlite")
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.com:5432/agrotech_db")
    database = test_settings.build_test_database()
    assert database["ENGINE"] == "django.db.backends.mysql"
    assert database["NAME"] == "agrotech_farms_test"
    assert database["TEST"]["NAME"] == "agrotech_farms_test"
    assert database["HOST"] == "127.0.0.1"
    assert database["PORT"] == "3307"


@pytest.mark.parametrize("protected", ["agrotech_db", "agrotech_t"])
def test_rejects_protected_database(protected):
    default = {**_valid_default(), "NAME": protected, "TEST": {"NAME": protected}}
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_database({"default": default})


@pytest.mark.parametrize("protected", ["agrotech_db", "agrotech_t"])
def test_rejects_protected_test_name(protected):
    default = {**_valid_default(), "TEST": {"NAME": protected}}
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_database({"default": default})


def test_rejects_missing_test_name():
    default = _valid_default()
    del default["TEST"]
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_database({"default": default})


def test_rejects_wrong_port():
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_database(
            {
                "default": {
                    "ENGINE": "django.db.backends.mysql",
                    "NAME": "agrotech_farms_test",
                    "HOST": "127.0.0.1",
                    "PORT": "3306",
                    "TEST": {"NAME": "agrotech_farms_test"},
                }
            }
        )


def test_rejects_wrong_engine():
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_database(
            {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": "agrotech_farms_test",
                    "HOST": "127.0.0.1",
                    "PORT": "3307",
                    "TEST": {"NAME": "agrotech_farms_test"},
                }
            }
        )


def test_accepts_the_expected_configuration():
    test_settings.validate_test_database(test_settings.DATABASES)


def test_rejects_wrong_settings_module():
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_environment(
            "config.settings", {"default": _valid_default()}
        )


def test_rejects_unconfigured_settings_module():
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_environment(None, {"default": _valid_default()})


def test_rejects_extra_database_alias():
    databases = {"default": _valid_default(), "other": _valid_default()}
    with pytest.raises(ImproperlyConfigured):
        test_settings.validate_test_environment("config.settings_test", databases)


def test_accepts_expected_environment():
    test_settings.validate_test_environment(
        "config.settings_test", {"default": _valid_default()}
    )


def test_expected_constants():
    assert test_settings.EXPECTED_SETTINGS_MODULE == "config.settings_test"
    assert test_settings.ALLOWED_DATABASE_ALIASES == ("default",)
    assert test_settings.EXPECTED_TEST_DB_USER == "agrotech_test_user"


def test_accepts_expected_test_user():
    test_settings.validate_test_credentials(test_settings.EXPECTED_TEST_DB_USER)


@pytest.mark.parametrize("user", [None, "", "root", "agrotech_farms_test"])
def test_rejects_other_test_user(user):
    with pytest.raises(ImproperlyConfigured, match="agrotech_test_user"):
        test_settings.validate_test_credentials(user)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"Consulta no permitida: {sql}")
        self.statements.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0)


class _FakeConnection:
    def __init__(
        self,
        *,
        database="agrotech_farms_test",
        current_user="agrotech_test_user",
        server_port=3307,
        schema="agrotech_farms_test",
    ):
        self.settings_dict = {
            **_valid_default(),
            "USER": test_settings.EXPECTED_TEST_DB_USER,
        }
        schema_row = (schema,) if schema is not None else None
        self.cursor_obj = _FakeCursor(
            [
                (database, f"'{current_user}'@'localhost'", server_port),
                schema_row,
            ]
        )

    def cursor(self):
        return self.cursor_obj


class _BlockedConnection(_FakeConnection):
    def __init__(self, db_blocker):
        super().__init__()
        self._db_blocker = db_blocker

    def cursor(self):
        if _database_access_is_blocked(self._db_blocker):
            raise RuntimeError(
                'Database access not allowed, use the "django_db" mark, or the '
                '"db" or "transactional_db" fixtures to enable it.'
            )
        return super().cursor()


def _database_access_is_blocked(db_blocker):
    return (
        BaseDatabaseWrapper.ensure_connection
        is not db_blocker._real_ensure_connection
    )


def test_live_database_validation_accepts_authorized_connection():
    connection = _FakeConnection()
    test_settings.validate_live_test_database(connection)
    assert len(connection.cursor_obj.statements) == 2
    assert all(
        sql.lstrip().upper().startswith("SELECT")
        for sql, _params in connection.cursor_obj.statements
    )


def test_live_database_validation_rejects_wrong_database():
    with pytest.raises(ImproperlyConfigured, match="base autorizada"):
        test_settings.validate_live_test_database(
            _FakeConnection(database="agrotech_db")
        )


def test_live_database_validation_rejects_wrong_actual_user():
    with pytest.raises(ImproperlyConfigured, match="usuario de pruebas autorizado"):
        test_settings.validate_live_test_database(
            _FakeConnection(current_user="root")
        )


def test_live_database_validation_rejects_wrong_server_port():
    with pytest.raises(ImproperlyConfigured, match="puerto autorizado"):
        test_settings.validate_live_test_database(
            _FakeConnection(server_port=3306)
        )


def test_live_database_validation_rejects_missing_schema():
    with pytest.raises(ImproperlyConfigured, match="esquema de pruebas no existe"):
        test_settings.validate_live_test_database(_FakeConnection(schema=None))


def test_live_database_validation_rejects_wrong_configured_user_before_connecting():
    connection = _FakeConnection()
    connection.settings_dict["USER"] = "root"
    with pytest.raises(ImproperlyConfigured, match="agrotech_test_user"):
        test_settings.validate_live_test_database(connection)
    assert connection.cursor_obj.statements == []


def test_preflight_unblocks_database_access_and_restores_the_block(
    django_db_blocker,
):
    assert django_db_blocker is not None
    assert _database_access_is_blocked(django_db_blocker)
    connection = _BlockedConnection(django_db_blocker)

    with pytest.raises(RuntimeError, match="Database access not allowed"):
        test_settings.validate_live_test_database(connection)

    test_guard._verify_live_test_database(connection, django_db_blocker)

    assert _database_access_is_blocked(django_db_blocker)
    assert len(connection.cursor_obj.statements) == 2
    assert all(
        sql.lstrip().upper().startswith("SELECT")
        for sql, _params in connection.cursor_obj.statements
    )


def _run_db_setup_guard(*, reuse_db=True, create_db=False, db_blocker=None):
    options = {"reuse_db": reuse_db, "create_db": create_db}
    fixturedef = SimpleNamespace(argname="django_db_setup")
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=options.__getitem__),
        getfixturevalue=lambda name: db_blocker,
    )
    test_guard.pytest_fixture_setup(fixturedef, request)


def _set_valid_credentials(monkeypatch):
    monkeypatch.setenv("TEST_DB_USER", test_settings.EXPECTED_TEST_DB_USER)
    monkeypatch.setenv("TEST_DB_PASSWORD", "test-password")


def test_db_setup_requires_reuse_db(monkeypatch):
    _set_valid_credentials(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--reuse-db"):
        _run_db_setup_guard(reuse_db=False)


def test_db_setup_rejects_create_db(monkeypatch):
    _set_valid_credentials(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--create-db"):
        _run_db_setup_guard(create_db=True)


def test_db_setup_requires_credentials(monkeypatch):
    monkeypatch.delenv("TEST_DB_USER", raising=False)
    monkeypatch.delenv("TEST_DB_PASSWORD", raising=False)
    with pytest.raises(pytest.UsageError, match="TEST_DB_USER y TEST_DB_PASSWORD"):
        _run_db_setup_guard()


def test_db_setup_rejects_credentials_for_other_user(monkeypatch):
    monkeypatch.setenv("TEST_DB_USER", "root")
    monkeypatch.setenv("TEST_DB_PASSWORD", "test-password")
    with pytest.raises(pytest.UsageError, match="agrotech_test_user"):
        _run_db_setup_guard()


def test_db_setup_accepts_reuse_db_and_credentials(monkeypatch):
    _set_valid_credentials(monkeypatch)
    db_blocker = object()
    received = {}

    def verify(connection, blocker):
        received["blocker"] = blocker

    monkeypatch.setattr(test_guard, "_verify_live_test_database", verify)
    _run_db_setup_guard(db_blocker=db_blocker)
    assert received["blocker"] is db_blocker


def test_db_setup_blocks_when_live_database_check_fails(monkeypatch):
    _set_valid_credentials(monkeypatch)

    def fail(connection, db_blocker):
        raise ImproperlyConfigured("el esquema de pruebas no existe")

    monkeypatch.setattr(test_guard, "_verify_live_test_database", fail)
    with pytest.raises(pytest.UsageError, match="esquema de pruebas no existe"):
        _run_db_setup_guard(db_blocker=object())


def test_non_database_fixture_does_not_require_runner_options(monkeypatch):
    monkeypatch.delenv("TEST_DB_USER", raising=False)
    monkeypatch.delenv("TEST_DB_PASSWORD", raising=False)
    options = {"reuse_db": False, "create_db": True}
    fixturedef = SimpleNamespace(argname="monkeypatch")
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=options.__getitem__)
    )
    test_guard.pytest_fixture_setup(fixturedef, request)
