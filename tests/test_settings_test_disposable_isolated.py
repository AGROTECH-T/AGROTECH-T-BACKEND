"""Pruebas estáticas de la configuración de validación aislada desechable.

No se conectan a MariaDB ni ejecutan migraciones: importan el módulo con
credenciales ficticias y validan el ``INSTALLED_APPS`` mínimo, la ausencia de
``MIGRATION_MODULES``, las guardas del grafo de migraciones y la auditoría de
tablas usando cursores simulados.
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
ISOLATED_MODULE = "config.settings_test_disposable_isolated"
DISPOSABLE_MODULE = "config.settings_test_disposable"
LIVE_MODULE = "config.settings_test"

EXPECTED_TABLES = frozenset(
    {
        "users",
        "login_codes",
        "sessions",
        "farms",
        "farm_memberships",
        "farm_invitations",
        "audit_events",
        "django_migrations",
    }
)


@pytest.fixture(autouse=True)
def _disposable_environment(monkeypatch):
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "1")
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", RUN_ID)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", DUMMY_VALUE)


def _load(monkeypatch):
    import config.settings_test_disposable_isolated as module

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


def _authorized_deps():
    return {
        ("accounts", "0001_initial"): (),
        ("accounts", "0002_harden_sessions"): (("accounts", "0001_initial"),),
        (
            "accounts",
            "0003_logincode_id_logincode_purpose_user_email_verified_and_more",
        ): (("accounts", "0002_harden_sessions"),),
        (
            "accounts",
            "0004_session_closed_at_session_created_at_session_id_and_more",
        ): (
            (
                "accounts",
                "0003_logincode_id_logincode_purpose_user_email_verified_and_more",
            ),
        ),
        ("accounts", "0005_logincode_delivery_fields"): (
            (
                "accounts",
                "0004_session_closed_at_session_created_at_session_id_and_more",
            ),
        ),
        ("accounts", "0006_user_can_create_farms"): (
            ("accounts", "0005_logincode_delivery_fields"),
        ),
        ("farms", "0001_initial"): (("accounts", "0002_harden_sessions"),),
        ("farms", "0002_farm_deactivation"): (
            ("farms", "0001_initial"),
            ("accounts", "0002_harden_sessions"),
        ),
        ("farms", "0003_farmmembership_left_at_farmmembership_revoked_at_and_more"): (
            ("accounts", "0002_harden_sessions"),
            ("farms", "0002_farm_deactivation"),
        ),
        ("farms", "0004_farminvitation"): (
            ("farms", "0003_farmmembership_left_at_farmmembership_revoked_at_and_more"),
        ),
        ("farms", "0005_farminvitation_code_hash"): (
            ("farms", "0004_farminvitation"),
        ),
        ("trazabilidad", "0001_initial"): (
            ("accounts", "0002_harden_sessions"),
            ("farms", "0002_farm_deactivation"),
        ),
        ("trazabilidad", "0002_alter_auditevent_event_type"): (
            ("trazabilidad", "0001_initial"),
        ),
        ("trazabilidad", "0003_alter_auditevent_event_type"): (
            ("trazabilidad", "0002_alter_auditevent_event_type"),
        ),
        ("trazabilidad", "0004_alter_auditevent_event_type"): (
            ("trazabilidad", "0003_alter_auditevent_event_type"),
        ),
        ("trazabilidad", "0005_alter_auditevent_event_type"): (
            ("trazabilidad", "0004_alter_auditevent_event_type"),
        ),
        ("trazabilidad", "0006_alter_auditevent_event_type"): (
            ("trazabilidad", "0005_alter_auditevent_event_type"),
        ),
        ("trazabilidad", "0007_alter_auditevent_event_type"): (
            ("trazabilidad", "0006_alter_auditevent_event_type"),
        ),
        ("trazabilidad", "0008_alter_auditevent_event_type"): (
            ("trazabilidad", "0007_alter_auditevent_event_type"),
        ),
    }


def _disk_graph(module):
    """Obtiene del disco los nodos autorizados y sus dependencias reales.

    Usa ``MigrationLoader`` sin conexión, por lo que no consulta la base ni
    ejecuta migraciones.
    """
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    nodes = [
        node for node in loader.graph.nodes if node[0] in module.AUTHORIZED_APPS
    ]
    deps = {
        node: tuple(tuple(dep) for dep in loader.graph.nodes[node].dependencies)
        for node in nodes
    }
    return nodes, deps


def test_expected_constants(monkeypatch):
    module = _load(monkeypatch)
    assert module.EXPECTED_SETTINGS_MODULE == ISOLATED_MODULE
    assert module.INSTALLED_APPS == [
        "apps.accounts",
        "apps.farms",
        "apps.trazabilidad",
    ]
    assert not hasattr(module, "MIGRATION_MODULES")
    assert module.AUTHORIZED_APPS == frozenset({"accounts", "farms", "trazabilidad"})
    assert len(module.AUTHORIZED_MIGRATIONS) == 19
    assert module.AUTHORIZED_MIGRATIONS == frozenset(_authorized_deps())
    assert module.TERMINAL_MIGRATIONS == frozenset(
        {
            ("accounts", "0006_user_can_create_farms"),
            ("farms", "0005_farminvitation_code_hash"),
            ("trazabilidad", "0008_alter_auditevent_event_type"),
        }
    )


def test_accepts_authorized_environment(monkeypatch):
    module = _load(monkeypatch)
    module.validate_disposable_environment(
        ISOLATED_MODULE, {"default": _valid_default(module)}
    )


def test_rejects_wrong_settings_module(monkeypatch):
    module = _load(monkeypatch)
    with pytest.raises(ImproperlyConfigured, match="módulo de settings"):
        module.validate_disposable_environment(
            "config.settings_test_disposable", {"default": _valid_default(module)}
        )


def test_rejects_extra_database_alias(monkeypatch):
    module = _load(monkeypatch)
    databases = {"default": _valid_default(module), "other": _valid_default(module)}
    with pytest.raises(ImproperlyConfigured, match="aliases"):
        module.validate_disposable_environment(ISOLATED_MODULE, databases)


def test_accepts_authorized_migration_graph(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_missing_authorized_migration(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    del deps[("accounts", "0004_session_closed_at_session_created_at_session_id_and_more")]
    with pytest.raises(ImproperlyConfigured, match="migraciones autorizadas ausentes"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_unauthorized_migration(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    deps[("permissions", "0001_initial")] = ()
    with pytest.raises(ImproperlyConfigured, match="migraciones no autorizadas presentes"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_unauthorized_same_app_migration(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    deps[("farms", "0006_future")] = (("farms", "0005_farminvitation_code_hash"),)
    with pytest.raises(ImproperlyConfigured, match="migraciones no autorizadas presentes"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_external_application_in_graph(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    deps[("auth", "0001_initial")] = ()
    with pytest.raises(ImproperlyConfigured, match="aplicación externa"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_wrong_terminal_nodes(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    deps[("accounts", "0006_user_can_create_farms")] = (
        ("accounts", "0005_logincode_delivery_fields"),
        ("trazabilidad", "0008_alter_auditevent_event_type"),
    )
    with pytest.raises(ImproperlyConfigured, match="nodos terminales inesperados"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_rejects_external_dependency(monkeypatch):
    module = _load(monkeypatch)
    deps = _authorized_deps()
    deps[("farms", "0001_initial")] = (("auth", "0001_initial"),)
    with pytest.raises(ImproperlyConfigured, match="dependencia externa"):
        module.validate_isolated_migration_graph(deps.keys(), deps)


def test_disk_migrations_match_authorized(monkeypatch):
    module = _load(monkeypatch)
    nodes, _deps = _disk_graph(module)
    assert len(nodes) == len(set(nodes))
    assert set(nodes) == module.AUTHORIZED_MIGRATIONS


def test_disk_migration_graph_is_valid(monkeypatch):
    module = _load(monkeypatch)
    nodes, deps = _disk_graph(module)
    module.validate_isolated_migration_graph(nodes, deps)
    expected = {node: set(node_deps) for node, node_deps in _authorized_deps().items()}
    assert {node: set(node_deps) for node, node_deps in deps.items()} == expected


def test_disk_terminal_nodes_match(monkeypatch):
    module = _load(monkeypatch)
    nodes, deps = _disk_graph(module)
    assert module._leaf_nodes(nodes, deps) == module.TERMINAL_MIGRATIONS


def test_disk_graph_has_no_external_dependencies(monkeypatch):
    module = _load(monkeypatch)
    _nodes, deps = _disk_graph(module)
    external = sorted(
        (node, dep)
        for node, node_deps in deps.items()
        for dep in node_deps
        if dep[0] not in module.AUTHORIZED_APPS
    )
    assert external == []


def test_loader_has_permissions_and_inventario_outside_whitelist(monkeypatch):
    from django.db.migrations.loader import MigrationLoader

    module = _load(monkeypatch)
    loader = MigrationLoader(None, ignore_no_migrations=True)
    loader_apps = {app_label for app_label, _name in loader.graph.nodes}
    excluded = {"permissions", "inventario"}
    assert excluded <= loader_apps
    assert not excluded & module.AUTHORIZED_APPS
    assert not {app for app, _name in module.AUTHORIZED_MIGRATIONS} & excluded
    nodes, _deps = _disk_graph(module)
    assert not {app for app, _name in nodes} & excluded


def test_trazabilidad_migration_state_matches_event_type():
    """El estado proyectado por las migraciones coincide con ``EventType``.

    Construye el estado únicamente desde los archivos de migración en disco,
    sin conexión a base de datos ni ejecución de migraciones.
    """
    from django.db.migrations.loader import MigrationLoader

    from apps.trazabilidad.models import EventType

    loader = MigrationLoader(None, ignore_no_migrations=True)
    state = loader.project_state()
    field = state.models[("trazabilidad", "auditevent")].fields["event_type"]
    migration_choices = [tuple(choice) for choice in field.choices]
    model_choices = [tuple(choice) for choice in EventType.choices]
    assert len(model_choices) == 28
    assert migration_choices == model_choices
    assert field.max_length == 40


def test_expected_isolated_tables_match_models(monkeypatch):
    module = _load(monkeypatch)
    assert module.expected_isolated_tables() == EXPECTED_TABLES


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

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


def _table_rows(table_names):
    return [(name,) for name in table_names]


def test_table_audit_accepts_authorized_tables(monkeypatch):
    module = _load(monkeypatch)
    connection = _FakeConnection(_table_rows(sorted(EXPECTED_TABLES)))
    module.validate_isolated_tables(connection)
    assert len(connection.cursor_obj.statements) == 1
    assert connection.cursor_obj.statements[0][0].lstrip().upper().startswith("SELECT")


def test_table_audit_rejects_forbidden_table(monkeypatch):
    module = _load(monkeypatch)
    rows = _table_rows(sorted(EXPECTED_TABLES | {"django_content_type"}))
    connection = _FakeConnection(rows)
    with pytest.raises(ImproperlyConfigured, match="tablas prohibidas presentes"):
        module.validate_isolated_tables(connection)


def test_table_audit_rejects_missing_authorized_table(monkeypatch):
    module = _load(monkeypatch)
    rows = _table_rows(sorted(EXPECTED_TABLES - {"audit_events"}))
    connection = _FakeConnection(rows)
    with pytest.raises(ImproperlyConfigured, match="tablas autorizadas ausentes"):
        module.validate_isolated_tables(connection)


def test_table_audit_rejects_unknown_table(monkeypatch):
    module = _load(monkeypatch)
    rows = _table_rows(sorted(EXPECTED_TABLES | {"inventory_products"}))
    connection = _FakeConnection(rows)
    with pytest.raises(ImproperlyConfigured, match="prohibidas"):
        module.validate_isolated_tables(connection)


def test_isolated_settings_use_empty_urlconf(monkeypatch):
    module = _load(monkeypatch)
    assert module.ROOT_URLCONF == "config.urls_test_disposable_isolated"


def test_isolated_urlconf_is_empty():
    import config.urls_test_disposable_isolated as isolated_urls

    assert isolated_urls.__name__ == "config.urls_test_disposable_isolated"
    assert isolated_urls.urlpatterns == []
    namespace = vars(isolated_urls)
    for forbidden in ("admin", "include", "path", "re_path", "url", "register"):
        assert forbidden not in namespace


def test_isolated_urlconf_resolves_without_external_routes():
    from django.urls import get_resolver

    resolver = get_resolver("config.urls_test_disposable_isolated")
    assert resolver.url_patterns == []


def test_isolated_urlconf_does_not_import_external_url_modules():
    import config.urls_test_disposable_isolated as isolated_urls

    namespace = vars(isolated_urls)
    for module_name in (
        "config.urls",
        "config.api_urls",
        "django.contrib.admin",
        "drf_spectacular",
        "apps.accounts.api.urls",
        "apps.farms.urls",
        "apps.inventario.api.urls",
    ):
        assert module_name not in namespace.values()


# --- Despacho de la guarda de pytest (conftest.py) para la configuración aislada.


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


def _use_isolated_settings(monkeypatch):
    """Activa la configuración aislada y registra si se verifica settings_test.

    El sustituto no lanza excepciones: ``pytest_fixture_setup`` convertiría un
    ``AssertionError`` en ``UsageError`` y lo ocultaría. Cada prueba comprueba
    el registro devuelto.
    """
    monkeypatch.setattr(test_guard, "_active_settings_module", lambda: ISOLATED_MODULE)
    live_calls = []
    monkeypatch.setattr(
        test_guard,
        "_verify_live_test_database",
        lambda connection, blocker: live_calls.append("live"),
    )
    return live_calls


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


def _accept_isolated_graph(module, monkeypatch, calls):
    monkeypatch.setattr(
        module, "validate_isolated_migrations", lambda: calls.append("graph")
    )


def test_settings_guard_returns_isolated_module(monkeypatch):
    module = _load(monkeypatch)
    guard = test_guard._settings_guard(ISOLATED_MODULE)
    assert guard is module
    assert guard.EXPECTED_SETTINGS_MODULE == ISOLATED_MODULE


def test_dispatch_accepts_isolated_settings(monkeypatch):
    module = _load(monkeypatch)
    test_guard._validate_settings_environment(
        ISOLATED_MODULE, {"default": _valid_default(module)}
    )


def test_dispatch_isolated_rejects_extra_alias(monkeypatch):
    module = _load(monkeypatch)
    databases = {"default": _valid_default(module), "other": _valid_default(module)}
    with pytest.raises(ImproperlyConfigured, match="aliases"):
        test_guard._validate_settings_environment(ISOLATED_MODULE, databases)


def test_dispatch_isolated_rejects_protected_database(monkeypatch):
    module = _load(monkeypatch)
    default = _valid_default(
        module,
        NAME="agrotech_farms_validation",
        TEST={"NAME": "agrotech_farms_validation"},
    )
    with pytest.raises(ImproperlyConfigured, match="protegida"):
        test_guard._validate_settings_environment(ISOLATED_MODULE, {"default": default})


def test_dispatch_isolated_rejects_live_target(monkeypatch):
    module = _load(monkeypatch)
    default = _valid_default(module, PORT="3307")
    with pytest.raises(ImproperlyConfigured, match="puerto no autorizado"):
        test_guard._validate_settings_environment(ISOLATED_MODULE, {"default": default})


def test_isolated_validator_rejects_disposable_module_name(monkeypatch):
    module = _load(monkeypatch)
    guard = test_guard._settings_guard(ISOLATED_MODULE)
    with pytest.raises(ImproperlyConfigured, match="módulo de settings"):
        guard.validate_disposable_environment(
            DISPOSABLE_MODULE, {"default": _valid_default(module)}
        )


def test_dispatch_isolated_does_not_import_live_settings(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.delitem(sys.modules, LIVE_MODULE, raising=False)
    test_guard._validate_settings_environment(
        ISOLATED_MODULE, {"default": _valid_default(module)}
    )
    assert LIVE_MODULE not in sys.modules


def test_pytest_configure_accepts_isolated_settings(monkeypatch):
    module = _load(monkeypatch)
    fake = SimpleNamespace(
        configured=True,
        SETTINGS_MODULE=ISOLATED_MODULE,
        DATABASES={"default": _valid_default(module)},
    )
    monkeypatch.setattr(test_guard, "django_settings", fake)
    test_guard.pytest_configure(SimpleNamespace())


def test_pytest_configure_rejects_isolated_live_target(monkeypatch):
    module = _load(monkeypatch)
    fake = SimpleNamespace(
        configured=True,
        SETTINGS_MODULE=ISOLATED_MODULE,
        DATABASES={"default": _valid_default(module, PORT="3307")},
    )
    monkeypatch.setattr(test_guard, "django_settings", fake)
    with pytest.raises(pytest.UsageError, match="puerto no autorizado"):
        test_guard.pytest_configure(SimpleNamespace())


def test_db_setup_isolated_requires_reuse_db(monkeypatch):
    _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--reuse-db"):
        _run_db_setup_guard(reuse_db=False)
    assert live_calls == []


def test_db_setup_isolated_rejects_create_db(monkeypatch):
    _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    with pytest.raises(pytest.UsageError, match="--create-db"):
        _run_db_setup_guard(create_db=True)
    assert live_calls == []


@pytest.mark.parametrize(
    "variable",
    [
        "AGROTECH_DISPOSABLE_CONFIRM",
        "AGROTECH_DISPOSABLE_RUN_ID",
        "AGROTECH_DISPOSABLE_DB_PASSWORD",
    ],
)
def test_db_setup_isolated_requires_runtime_variables(monkeypatch, variable):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    calls = []
    _accept_isolated_graph(module, monkeypatch, calls)
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(pytest.UsageError, match=variable):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []
    assert live_calls == []


def test_db_setup_isolated_validates_graph_before_identity(monkeypatch):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    monkeypatch.delenv("TEST_DB_USER", raising=False)
    monkeypatch.delenv("TEST_DB_PASSWORD", raising=False)
    calls = []
    _accept_isolated_graph(module, monkeypatch, calls)
    db_blocker = _FakeBlocker()
    received = {}

    def verify(connection, blocker):
        received["blocker"] = blocker
        calls.append("identity")

    monkeypatch.setattr(test_guard, "_verify_live_disposable_database", verify)
    connection = _use_fake_connection(monkeypatch)
    _run_db_setup_guard(db_blocker=db_blocker)
    assert calls == ["graph", "identity"]
    assert received["blocker"] is db_blocker
    assert live_calls == []
    assert connection.closed == 1


def test_db_setup_isolated_blocks_on_invalid_graph(monkeypatch):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    calls = []

    def invalid_graph():
        raise ImproperlyConfigured("Grafo de migraciones aisladas inválido: prueba")

    monkeypatch.setattr(module, "validate_isolated_migrations", invalid_graph)
    monkeypatch.setattr(
        test_guard,
        "_verify_live_disposable_database",
        lambda connection, blocker: calls.append("identity"),
    )
    with pytest.raises(pytest.UsageError, match="Grafo de migraciones aisladas"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []
    assert live_calls == []


def test_db_setup_isolated_converts_unexpected_graph_errors(monkeypatch):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    calls = []

    def broken_graph():
        raise RuntimeError("fallo inesperado del cargador de migraciones")

    monkeypatch.setattr(module, "validate_isolated_migrations", broken_graph)
    monkeypatch.setattr(
        test_guard,
        "_verify_live_disposable_database",
        lambda connection, blocker: calls.append("identity"),
    )
    with pytest.raises(pytest.UsageError, match=r"validación previa.*RuntimeError"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == []
    assert live_calls == []


def test_db_setup_isolated_blocks_on_identity_failure(monkeypatch):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    _accept_isolated_graph(module, monkeypatch, [])

    def fail(connection, db_blocker):
        raise ImproperlyConfigured("la conexión no apunta al contenedor autorizado")

    monkeypatch.setattr(test_guard, "_verify_live_disposable_database", fail)
    connection = _use_fake_connection(monkeypatch)
    with pytest.raises(pytest.UsageError, match="contenedor autorizado"):
        _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert live_calls == []
    assert connection.closed == 1


def test_db_setup_isolated_does_not_import_live_settings(monkeypatch):
    module = _load(monkeypatch)
    live_calls = _use_isolated_settings(monkeypatch)
    calls = []
    _accept_isolated_graph(module, monkeypatch, calls)
    monkeypatch.setattr(
        test_guard,
        "_verify_live_disposable_database",
        lambda connection, blocker: calls.append("identity"),
    )
    _use_fake_connection(monkeypatch)
    monkeypatch.delitem(sys.modules, LIVE_MODULE, raising=False)
    _run_db_setup_guard(db_blocker=_FakeBlocker())
    assert calls == ["graph", "identity"]
    assert live_calls == []
    assert LIVE_MODULE not in sys.modules
