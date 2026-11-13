"""Pruebas estáticas de la configuración desechable de validación de permisos.

No se conectan a MariaDB ni ejecutan migraciones: importan el módulo con
credenciales ficticias y validan nombres, destinos, alias y el despacho de la
guarda de seguridad.
"""
import contextlib
import importlib
import sys
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

import conftest as test_guard

RUN_ID = "0123456789ab"
DUMMY_VALUE = "dummy-disposable-value"
DISPOSABLE_MODULE = "config.settings_test_disposable"
LIVE_MODULE = "config.settings_test"


@pytest.fixture(autouse=True)
def _disposable_environment(monkeypatch):
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "1")
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", RUN_ID)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", DUMMY_VALUE)


def _load(monkeypatch):
    import config.settings_test_disposable as module

    return importlib.reload(module)


def _valid_default(module, **overrides):
    name = module.disposable_database_name(RUN_ID)
    default = {
        "ENGINE": module.TEST_DB_ENGINE,
        "NAME": name,
        "USER": module.EXPECTED_TEST_DB_USER,
        "PASSWORD": DUMMY_VALUE,
        "HOST": module.DEFAULT_TEST_DB_HOST,
        "PORT": module.DEFAULT_TEST_DB_PORT,
        "TEST": {"NAME": name},
    }
    default.update(overrides)
    return default


def test_expected_constants(monkeypatch):
    module = _load(monkeypatch)
    assert module.EXPECTED_SETTINGS_MODULE == DISPOSABLE_MODULE
    assert module.EXPECTED_TEST_DB_USER == "agrotech_perm_validation"
    assert module.DEFAULT_TEST_DB_HOST == "127.0.0.1"
    assert module.DEFAULT_TEST_DB_PORT == "3317"
    assert module.EXPECTED_SERVER_PORT == "3306"
    assert module.ALLOWED_DATABASE_ALIASES == ("default",)
    assert "agrotech_farms_validation" in module.PROTECTED_DATABASES


def test_builds_expected_configuration(monkeypatch):
    module = _load(monkeypatch)
    database = module.build_test_database()
    assert database["ENGINE"] == "django.db.backends.mysql"
    assert database["NAME"] == "agrotech_permtest_0123456789ab"
    assert database["TEST"]["NAME"] == database["NAME"]
    assert database["USER"] == "agrotech_perm_validation"
    assert database["PASSWORD"] == DUMMY_VALUE
    assert database["HOST"] == "127.0.0.1"
    assert database["PORT"] == "3317"


def test_container_hostname_is_derived(monkeypatch):
    module = _load(monkeypatch)
    assert module.disposable_container_hostname(RUN_ID) == "agrotech-permtest-0123456789ab"


def test_missing_confirmation_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.delenv("AGROTECH_DISPOSABLE_CONFIRM", raising=False)
    with pytest.raises(ImproperlyConfigured, match="AGROTECH_DISPOSABLE_CONFIRM"):
        module.build_test_database()


def test_wrong_confirmation_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "yes")
    with pytest.raises(ImproperlyConfigured, match="AGROTECH_DISPOSABLE_CONFIRM"):
        module.build_test_database()


def test_missing_run_id_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.delenv("AGROTECH_DISPOSABLE_RUN_ID", raising=False)
    with pytest.raises(ImproperlyConfigured, match="AGROTECH_DISPOSABLE_RUN_ID"):
        module.build_test_database()


@pytest.mark.parametrize(
    "run_id",
    [
        "0123456789AB",
        "0123456789a",
        "0123456789abc",
        "g123456789ab",
        "0123456789ab ",
        "agrotech-permtest-0123456789ab",
    ],
)
def test_invalid_run_ids_are_rejected(monkeypatch, run_id):
    module = _load(monkeypatch)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", run_id)
    with pytest.raises(ImproperlyConfigured, match="AGROTECH_DISPOSABLE_RUN_ID"):
        module.build_test_database()


def test_missing_password_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.delenv("AGROTECH_DISPOSABLE_DB_PASSWORD", raising=False)
    with pytest.raises(
        ImproperlyConfigured, match="AGROTECH_DISPOSABLE_DB_PASSWORD"
    ):
        module.build_test_database()


def test_blank_password_is_rejected(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", "   ")
    with pytest.raises(
        ImproperlyConfigured, match="AGROTECH_DISPOSABLE_DB_PASSWORD"
    ):
        module.build_test_database()


def test_runtime_environment_requires_confirmation(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "0")
    with pytest.raises(ImproperlyConfigured, match="AGROTECH_DISPOSABLE_CONFIRM"):
        module.validate_disposable_runtime_environment()


def test_accepts_expected_configuration(monkeypatch):
    module = _load(monkeypatch)
    module.validate_disposable_database({"default": _valid_default(module)})


@pytest.mark.parametrize(
    "protected",
    ["agrotech_db", "agrotech_t", "agrotech_farms_test", "agrotech_farms_validation"],
)
def test_rejects_protected_database(monkeypatch, protected):
    module = _load(monkeypatch)
    default = _valid_default(module, NAME=protected, TEST={"NAME": protected})
    with pytest.raises(ImproperlyConfigured, match="protegida"):
        module.validate_disposable_database({"default": default})


def test_rejects_database_outside_pattern(monkeypatch):
    module = _load(monkeypatch)
    default = _valid_default(
        module, NAME="otra_base", TEST={"NAME": "otra_base"}
    )
    with pytest.raises(ImproperlyConfigured, match="nombre de base no autorizado"):
        module.validate_disposable_database({"default": default})


def test_rejects_test_name_diverging_from_name(monkeypatch):
    module = _load(monkeypatch)
    other = module.disposable_database_name("ffffffffffff")
    default = _valid_default(module, TEST={"NAME": other})
    with pytest.raises(ImproperlyConfigured, match="no puede desviarse"):
        module.validate_disposable_database({"default": default})


@pytest.mark.parametrize("user", [None, "", "root", "agrotech_validation_user"])
def test_rejects_unexpected_user(monkeypatch, user):
    module = _load(monkeypatch)
    default = _valid_default(module, USER=user)
    with pytest.raises(ImproperlyConfigured, match="usuario de BD no autorizado"):
        module.validate_disposable_database({"default": default})


def test_rejects_wrong_engine(monkeypatch):
    module = _load(monkeypatch)
    default = _valid_default(module, ENGINE="django.db.backends.sqlite3")
    with pytest.raises(ImproperlyConfigured, match="motor inesperado"):
        module.validate_disposable_database({"default": default})


@pytest.mark.parametrize(
    "host",
    ["localhost", "0.0.0.0", "db.example.com"],  # noqa: S104
)
def test_rejects_wrong_host(monkeypatch, host):
    module = _load(monkeypatch)
    default = _valid_default(module, HOST=host)
    with pytest.raises(ImproperlyConfigured, match="host no autorizado"):
        module.validate_disposable_database({"default": default})


@pytest.mark.parametrize("port", ["3306", "3307", "3318"])
def test_rejects_wrong_port(monkeypatch, port):
    module = _load(monkeypatch)
    default = _valid_default(module, PORT=port)
    with pytest.raises(ImproperlyConfigured, match="puerto no autorizado"):
        module.validate_disposable_database({"default": default})


def test_rejects_missing_test_name(monkeypatch):
    module = _load(monkeypatch)
    default = _valid_default(module)
    del default["TEST"]
    with pytest.raises(ImproperlyConfigured):
        module.validate_disposable_database({"default": default})


def test_rejects_extra_database_alias(monkeypatch):
    module = _load(monkeypatch)
    databases = {"default": _valid_default(module), "other": _valid_default(module)}
    with pytest.raises(ImproperlyConfigured, match="aliases"):
        module.validate_disposable_environment(DISPOSABLE_MODULE, databases)


def test_rejects_wrong_settings_module(monkeypatch):
    module = _load(monkeypatch)
    with pytest.raises(ImproperlyConfigured, match="módulo de settings"):
        module.validate_disposable_environment(
            "config.settings_test", {"default": _valid_default(module)}
        )


def test_accepts_expected_environment(monkeypatch):
    module = _load(monkeypatch)
    module.validate_disposable_environment(
        DISPOSABLE_MODULE, {"default": _valid_default(module)}
    )


def test_dispatch_accepts_live_settings():
    live = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "agrotech_farms_validation",
        "TEST": {"NAME": "agrotech_farms_validation"},
        "USER": "agrotech_validation_user",
        "HOST": "127.0.0.1",
        "PORT": "3307",
    }
    test_guard._validate_settings_environment("config.settings_test", {"default": live})


def test_dispatch_accepts_disposable_settings(monkeypatch):
    module = _load(monkeypatch)
    test_guard._validate_settings_environment(
        DISPOSABLE_MODULE, {"default": _valid_default(module)}
    )


def test_dispatch_rejects_unknown_settings():
    with pytest.raises(ImproperlyConfigured, match="no autorizado"):
        test_guard._validate_settings_environment("config.settings", {"default": {}})


def test_dispatch_rejects_extra_alias(monkeypatch):
    module = _load(monkeypatch)
    databases = {"default": _valid_default(module), "other": _valid_default(module)}
    with pytest.raises(ImproperlyConfigured, match="aliases"):
        test_guard._validate_settings_environment(DISPOSABLE_MODULE, databases)


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
    def __init__(self, settings_dict, rows):
        self.settings_dict = settings_dict
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


def _fake_connection(module, **overrides):
    configured_name = overrides.pop(
        "configured_name", module.disposable_database_name(RUN_ID)
    )
    configured_user = overrides.pop("configured_user", module.EXPECTED_TEST_DB_USER)
    configured_port = overrides.pop("configured_port", module.DEFAULT_TEST_DB_PORT)
    database = overrides.pop("database", configured_name)
    current_user = overrides.pop("current_user", module.EXPECTED_TEST_DB_USER)
    server_port = overrides.pop("server_port", module.EXPECTED_SERVER_PORT)
    hostname = overrides.pop(
        "hostname", module.disposable_container_hostname(RUN_ID)
    )
    storage_engine = overrides.pop("storage_engine", module.EXPECTED_STORAGE_ENGINE)
    schema = overrides.pop("schema", configured_name)

    settings = _valid_default(
        module,
        NAME=configured_name,
        TEST={"NAME": configured_name},
        PORT=configured_port,
    )
    settings["USER"] = configured_user
    row = (database, f"'{current_user}'@'%'", server_port, hostname, storage_engine)
    schema_row = None if schema is None else (schema,)
    return _FakeConnection(settings, [row, schema_row])


def test_live_validation_accepts_authorized_connection(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module)
    module.validate_live_disposable_database(connection)
    assert len(connection.cursor_obj.statements) == 2
    assert all(
        sql.lstrip().upper().startswith("SELECT")
        for sql, _params in connection.cursor_obj.statements
    )


def test_live_validation_rejects_wrong_database(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, database="agrotech_permtest_ffffffffffff")
    with pytest.raises(ImproperlyConfigured, match="base autorizada"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_wrong_actual_user(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, current_user="root")
    with pytest.raises(ImproperlyConfigured, match="usuario de pruebas autorizado"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_wrong_server_port(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, server_port="3317")
    with pytest.raises(ImproperlyConfigured, match="puerto interno"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_wrong_hostname(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, hostname="otro-contenedor")
    with pytest.raises(ImproperlyConfigured, match="contenedor autorizado"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_non_innodb(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, storage_engine="MyISAM")
    with pytest.raises(ImproperlyConfigured, match="InnoDB"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_missing_schema(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, schema=None)
    with pytest.raises(ImproperlyConfigured, match="esquema de pruebas no existe"):
        module.validate_live_disposable_database(connection)


def test_live_validation_rejects_protected_name_before_connecting(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, configured_name="agrotech_farms_validation")
    with pytest.raises(ImproperlyConfigured, match="protegida"):
        module.validate_live_disposable_database(connection)
    assert connection.cursor_obj.statements == []


def test_live_validation_rejects_wrong_configured_user_before_connecting(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, configured_user="root")
    with pytest.raises(ImproperlyConfigured, match="usuario de BD no autorizado"):
        module.validate_live_disposable_database(connection)
    assert connection.cursor_obj.statements == []


# --- Despacho de la guarda de pytest (conftest.py) para la configuración desechable.


class _FakeBlocker:
    """Simula ``django_db_blocker`` y registra cada desbloqueo temporal."""

    def __init__(self):
        self.unblocked = 0

    @contextlib.contextmanager
    def unblock(self):
        self.unblocked += 1
        yield


def _run_db_setup_guard(*, reuse_db=True, create_db=False, db_blocker=None):
    options = {"reuse_db": reuse_db, "create_db": create_db}
    fixturedef = SimpleNamespace(argname="django_db_setup")
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=options.__getitem__),
        getfixturevalue=lambda name: db_blocker,
    )
    test_guard.pytest_fixture_setup(fixturedef, request)


def _use_settings_module(monkeypatch, settings_module):
    monkeypatch.setattr(test_guard, "_active_settings_module", lambda: settings_module)


def _track_verifiers(monkeypatch, disposable=None):
    """Sustituye ambos verificadores y registra cuál se invoca.

    No lanza excepciones propias: ``pytest_fixture_setup`` convierte cualquier
    excepción del verificador en ``UsageError``, por lo que un ``AssertionError``
    dentro del sustituto quedaría oculto. El registro se comprueba después.
    """
    calls = []

    def live(connection, db_blocker):
        calls.append("live")

    def disposable_verify(connection, db_blocker):
        calls.append("disposable")
        if disposable is not None:
            disposable(connection, db_blocker)

    monkeypatch.setattr(test_guard, "_verify_live_test_database", live)
    monkeypatch.setattr(test_guard, "_verify_live_disposable_database", disposable_verify)
    return calls


class _FakeDefaultConnection:
    """Conexión simulada que registra el cierre ejecutado en ``finally``."""

    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _use_fake_connection(monkeypatch):
    connection = _FakeDefaultConnection()
    monkeypatch.setattr("django.db.connections", {"default": connection})
    return connection


def _fake_django_settings(settings_module, databases, configured=True):
    return SimpleNamespace(
        configured=configured,
        SETTINGS_MODULE=settings_module,
        DATABASES=databases,
    )


def test_authorized_settings_modules_whitelist():
    assert test_guard.AUTHORIZED_SETTINGS_MODULES == frozenset(
        {
            "config.settings_test",
            "config.settings_test_disposable",
            "config.settings_test_disposable_isolated",
        }
    )
    assert test_guard.DISPOSABLE_SETTINGS_MODULES == frozenset(
        {"config.settings_test_disposable", "config.settings_test_disposable_isolated"}
    )
    assert test_guard.DISPOSABLE_SETTINGS_MODULES < test_guard.AUTHORIZED_SETTINGS_MODULES


def test_settings_guard_uses_whitelist_before_importing(monkeypatch):
    monkeypatch.setattr(test_guard, "AUTHORIZED_SETTINGS_MODULES", frozenset())
    monkeypatch.delitem(sys.modules, DISPOSABLE_MODULE, raising=False)
    with pytest.raises(ImproperlyConfigured, match="no autorizado"):
        test_guard._settings_guard(DISPOSABLE_MODULE)
    assert DISPOSABLE_MODULE not in sys.modules


def test_settings_guard_returns_disposable_module(monkeypatch):
    module = _load(monkeypatch)
    assert test_guard._settings_guard(DISPOSABLE_MODULE) is module


@pytest.mark.parametrize(
    "settings_module",
    [
        "config.settings",
        "config.settings_test_disposable_extra",
        "config.settings_test_disposable.isolated",
        "",
        None,
    ],
)
def test_settings_guard_rejects_unknown_modules(settings_module):
    with pytest.raises(ImproperlyConfigured, match="no autorizado"):
        test_guard._settings_guard(settings_module)


def test_dispatch_disposable_does_not_import_live_settings(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.delitem(sys.modules, LIVE_MODULE, raising=False)
    test_guard._validate_settings_environment(
        DISPOSABLE_MODULE, {"default": _valid_default(module)}
    )
    assert LIVE_MODULE not in sys.modules


def test_dispatch_rejects_live_database_with_disposable_settings(monkeypatch):
    _load(monkeypatch)
    live = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "agrotech_farms_validation",
        "TEST": {"NAME": "agrotech_farms_validation"},
        "USER": "agrotech_validation_user",
        "PASSWORD": DUMMY_VALUE,
        "HOST": "127.0.0.1",
        "PORT": "3307",
    }
    with pytest.raises(ImproperlyConfigured, match="protegida"):
        test_guard._validate_settings_environment(DISPOSABLE_MODULE, {"default": live})


def test_dispatch_rejects_disposable_database_with_live_settings(monkeypatch):
    module = _load(monkeypatch)
    with pytest.raises(ImproperlyConfigured, match="nombre de base no autorizado"):
        test_guard._validate_settings_environment(
            LIVE_MODULE, {"default": _valid_default(module)}
        )


def test_pytest_configure_accepts_disposable_settings(monkeypatch):
    module = _load(monkeypatch)
    fake = _fake_django_settings(DISPOSABLE_MODULE, {"default": _valid_default(module)})
    monkeypatch.setattr(test_guard, "django_settings", fake)
    test_guard.pytest_configure(SimpleNamespace())


def test_pytest_configure_rejects_unknown_settings(monkeypatch):
    fake = _fake_django_settings("config.settings", {"default": {}})
    monkeypatch.setattr(test_guard, "django_settings", fake)
    with pytest.raises(pytest.UsageError, match="no autorizado"):
        test_guard.pytest_configure(SimpleNamespace())


def test_pytest_configure_rejects_disposable_extra_alias(monkeypatch):
    module = _load(monkeypatch)
    databases = {"default": _valid_default(module), "other": _valid_default(module)}
    monkeypatch.setattr(
        test_guard, "django_settings", _fake_django_settings(DISPOSABLE_MODULE, databases)
    )
    with pytest.raises(pytest.UsageError, match="aliases"):
        test_guard.pytest_configure(SimpleNamespace())


def test_pytest_configure_rejects_unconfigured_django(monkeypatch):
    fake = _fake_django_settings(DISPOSABLE_MODULE, {}, configured=False)
    monkeypatch.setattr(test_guard, "django_settings", fake)
    with pytest.raises(pytest.UsageError, match="no está configurado"):
        test_guard.pytest_configure(SimpleNamespace())


def test_pytest_configure_converts_unexpected_errors(monkeypatch):
    def broken(settings_module, databases):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(test_guard, "_validate_settings_environment", broken)
    monkeypatch.setattr(
        test_guard, "django_settings", _fake_django_settings(DISPOSABLE_MODULE, {})
    )
    with pytest.raises(pytest.UsageError, match="RuntimeError"):
        test_guard.pytest_configure(SimpleNamespace())


def test_db_setup_disposable_requires_reuse_db(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--reuse-db"):
        _run_db_setup_guard(reuse_db=False)
    assert calls == []


def test_db_setup_disposable_rejects_create_db(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--create-db"):
        _run_db_setup_guard(create_db=True)
    assert calls == []


@pytest.mark.parametrize("settings_module", ["config.settings", "config.settings_prod"])
def test_db_setup_rejects_unknown_settings_module(monkeypatch, settings_module):
    _use_settings_module(monkeypatch, settings_module)
    calls = _track_verifiers(monkeypatch)
    with pytest.raises(pytest.UsageError, match="no autorizado"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []


def test_db_setup_converts_unexpected_precheck_errors(monkeypatch):
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)

    def broken(settings_module):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(test_guard, "_validate_runner_credentials", broken)
    with pytest.raises(pytest.UsageError, match="RuntimeError"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []


@pytest.mark.parametrize(
    "variable",
    [
        "AGROTECH_DISPOSABLE_CONFIRM",
        "AGROTECH_DISPOSABLE_RUN_ID",
        "AGROTECH_DISPOSABLE_DB_PASSWORD",
    ],
)
def test_db_setup_disposable_requires_runtime_variables(monkeypatch, variable):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(pytest.UsageError, match=variable):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []


def test_db_setup_disposable_rejects_malformed_run_id(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", "NOT-A-RUN-ID")
    with pytest.raises(pytest.UsageError, match="AGROTECH_DISPOSABLE_RUN_ID"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []


def test_db_setup_disposable_verifies_identity_without_live_credentials(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    monkeypatch.delenv("TEST_DB_USER", raising=False)
    monkeypatch.delenv("TEST_DB_PASSWORD", raising=False)
    db_blocker = _FakeBlocker()
    received = {}

    def verify(connection, blocker):
        received["blocker"] = blocker

    calls = _track_verifiers(monkeypatch, disposable=verify)
    connection = _use_fake_connection(monkeypatch)
    _run_db_setup_guard(db_blocker=db_blocker)
    assert calls == ["disposable"]
    assert received["blocker"] is db_blocker
    assert connection.closed == 1


def test_db_setup_disposable_blocks_on_identity_failure(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)

    def fail(connection, db_blocker):
        raise ImproperlyConfigured("la conexión no apunta al contenedor autorizado")

    calls = _track_verifiers(monkeypatch, disposable=fail)
    connection = _use_fake_connection(monkeypatch)
    with pytest.raises(pytest.UsageError, match="contenedor autorizado"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == ["disposable"]
    assert connection.closed == 1


def test_db_setup_disposable_blocks_on_unexpected_failure(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)

    def fail(connection, db_blocker):
        raise RuntimeError("fallo inesperado")

    calls = _track_verifiers(monkeypatch, disposable=fail)
    connection = _use_fake_connection(monkeypatch)
    with pytest.raises(pytest.UsageError, match=r"no se pudo verificar.*RuntimeError"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == ["disposable"]
    assert connection.closed == 1


def test_db_setup_disposable_requires_db_blocker(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    monkeypatch.setattr(
        test_guard,
        "_verify_live_test_database",
        lambda connection, blocker: pytest.fail("no debe verificarse settings_test"),
    )
    connection = _use_fake_connection(monkeypatch)
    with pytest.raises(pytest.UsageError, match="django_db_blocker"):
        _run_db_setup_guard(db_blocker=None)
    assert connection.closed == 1


def test_db_setup_disposable_does_not_import_live_settings(monkeypatch):
    _load(monkeypatch)
    _use_settings_module(monkeypatch, DISPOSABLE_MODULE)
    calls = _track_verifiers(monkeypatch)
    _use_fake_connection(monkeypatch)
    monkeypatch.delitem(sys.modules, LIVE_MODULE, raising=False)
    _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == ["disposable"]
    assert LIVE_MODULE not in sys.modules


def test_verify_live_disposable_database_uses_unblock_and_select_only(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module)
    db_blocker = _FakeBlocker()
    test_guard._verify_live_disposable_database(connection, db_blocker)
    assert db_blocker.unblocked == 1
    assert len(connection.cursor_obj.statements) == 2
    assert all(
        sql.lstrip().upper().startswith("SELECT")
        for sql, _params in connection.cursor_obj.statements
    )


def test_verify_live_disposable_database_rejects_wrong_hostname(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module, hostname="otro-contenedor")
    with pytest.raises(ImproperlyConfigured, match="contenedor autorizado"):
        test_guard._verify_live_disposable_database(connection, _FakeBlocker())


def test_verify_live_disposable_database_rejects_other_run_id(monkeypatch):
    module = _load(monkeypatch)
    other_name = module.disposable_database_name("ffffffffffff")
    connection = _fake_connection(module, configured_name=other_name)
    with pytest.raises(ImproperlyConfigured, match="identificador actual"):
        test_guard._verify_live_disposable_database(connection, _FakeBlocker())


def test_verify_live_disposable_database_requires_db_blocker(monkeypatch):
    module = _load(monkeypatch)
    connection = _fake_connection(module)
    with pytest.raises(ImproperlyConfigured, match="django_db_blocker"):
        test_guard._verify_live_disposable_database(connection, None)
    assert connection.cursor_obj.statements == []
