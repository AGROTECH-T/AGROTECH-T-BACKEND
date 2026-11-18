"""Disposable MariaDB profile limited to the accounts/farms/IoT graph."""

from django.core.exceptions import ImproperlyConfigured

from config.settings_test_disposable import *  # noqa: F401,F403
from config.settings_test_disposable import (
    DEFAULT_TEST_DB_HOST,
    EXPECTED_SERVER_PORT,
    EXPECTED_STORAGE_ENGINE,
    EXPECTED_TEST_DB_USER,
    PROTECTED_DATABASES,
    TEST_DB_ENGINE,
    TEST_DB_NAME_PREFIX,
    _required_run_id,
    build_test_database,
    disposable_container_hostname,
)
from config.settings_test_disposable_isolated import (
    AUTHORIZED_MIGRATIONS as BASE_AUTHORIZED_MIGRATIONS,
)
from config.settings_test_disposable_isolated import (
    FORBIDDEN_TABLE_NAMES as BASE_FORBIDDEN_TABLE_NAMES,
)
from config.settings_test_disposable_isolated import (
    _leaf_nodes,
)

EXPECTED_SETTINGS_MODULE = "config.settings_test_disposable_iot"
IOT_TEST_DB_PORT = "3318"
AUTHORIZED_APPS = frozenset({"accounts", "farms", "trazabilidad", "iot"})
IOT_MIGRATION_NAMES = (
    "0001_initial",
    "0002_composite_farm_fks",
    "0003_gatewaycredential",
    "0004_coordinatorbootwindow_coordinatorcredential",
    "0005_rename_key_version_master_key_version",
    "0006_iot_persistence_parent_uniques",
    "0007_iot_frame_measurement_conflict",
    "0008_iot_persistence_composite_fks",
    "0009_coordinator_key_generation",
    "0010_sensor_source",
    "0011_measurement_corrections",
    "0012_iot_history_triggers",
    "0013_iot_ingestion_history_guards",
    "0014_iot_mqtt_delivery",
    "0015_iot_delivery_identity_pauses",
    "0016_measurement_correction_actor_required",
    "0017_broker_identity_evidence",
    "0018_broker_identity_registry",
    "0019_broker_identity_append_only_guards",
    "0020_broker_identity_overflow_evidence",
    "0021_broker_credential",
)
AUTHORIZED_MIGRATIONS = frozenset(BASE_AUTHORIZED_MIGRATIONS) | frozenset(
    ("iot", name) for name in IOT_MIGRATION_NAMES
)
TERMINAL_MIGRATIONS = frozenset(
    {
        ("accounts", "0008_user_can_create_farms"),
        ("farms", "0006_farminvitation_code_hash"),
        ("trazabilidad", "0013_alter_auditevent_event_type"),
        ("iot", "0021_broker_credential"),
    }
)
AUXILIARY_TABLES = frozenset({"django_migrations"})
FORBIDDEN_TABLE_NAMES = frozenset(BASE_FORBIDDEN_TABLE_NAMES)
DISALLOWED_HOST_PORTS = frozenset({"3306", "3307", "3317"})

INSTALLED_APPS = ["apps.accounts", "apps.farms", "apps.trazabilidad", "apps.iot"]
# IoT API tests attach an authenticated user with DRF's force_authenticate; no
# auth middleware or its auth/contenttypes tables are needed. The production
# authenticator chain (apps.accounts SessionCookieAuthentication) still reads
# ``request.session`` for every anonymous request, so the session middleware is
# kept as in production. Signed-cookie storage avoids installing
# django.contrib.sessions (and its table) in this restricted migration graph;
# authentication itself is unchanged.
MIDDLEWARE = ["django.contrib.sessions.middleware.SessionMiddleware"]
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
FARMS_PERMISSION_CLEANUP = "disabled"
ROOT_URLCONF = "config.urls_test_disposable_iot"


def build_iot_test_database():
    database = build_test_database()
    database["PORT"] = IOT_TEST_DB_PORT
    database["TEST"]["NAME"] = database["NAME"]
    return database


DATABASES = {"default": build_iot_test_database()}


def validate_iot_disposable_database(databases):
    default = databases.get("default") or {}
    test_name = (default.get("TEST") or {}).get("NAME")
    run_id = _required_run_id()
    expected_name = f"{TEST_DB_NAME_PREFIX}{run_id}"
    problems = []
    if set(databases) != {"default"}:
        problems.append("solo se admite el alias default")
    if default.get("ENGINE") != TEST_DB_ENGINE:
        problems.append("motor de base inesperado")
    if default.get("NAME") != expected_name or test_name != expected_name:
        problems.append("nombre de base no corresponde al RunId desechable")
    if default.get("NAME") in PROTECTED_DATABASES or test_name in PROTECTED_DATABASES:
        problems.append("se detectó una base protegida")
    if default.get("USER") != EXPECTED_TEST_DB_USER:
        problems.append("usuario de base no autorizado")
    if default.get("HOST") != DEFAULT_TEST_DB_HOST:
        problems.append("host de base no autorizado")
    port = str(default.get("PORT", ""))
    if port in DISALLOWED_HOST_PORTS or port != IOT_TEST_DB_PORT:
        problems.append("puerto de host IoT no autorizado")
    if not isinstance(default.get("PASSWORD"), str) or not default["PASSWORD"].strip():
        problems.append("la credencial desechable está vacía")
    if problems:
        raise ImproperlyConfigured("Perfil IoT desechable inválido: " + "; ".join(problems))


def validate_disposable_environment(settings_module, databases):
    if settings_module != EXPECTED_SETTINGS_MODULE:
        raise ImproperlyConfigured("módulo IoT desechable no autorizado")
    validate_iot_disposable_database(databases)


def validate_live_iot_disposable_database(connection):
    validate_iot_disposable_database({"default": connection.settings_dict})
    run_id = _required_run_id()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DATABASE(), CURRENT_USER(), @@port, @@hostname, "
            "@@version, @@default_storage_engine"
        )
        row = cursor.fetchone()
    expected_name = f"{TEST_DB_NAME_PREFIX}{run_id}"
    actual_user = str(row[1]).rsplit("@", 1)[0].strip("'\"`") if row else ""
    if (
        not row
        or len(row) < 6
        or str(row[0]) != expected_name
        or actual_user != EXPECTED_TEST_DB_USER
        or str(row[2]) != EXPECTED_SERVER_PORT
        or str(row[3]) != disposable_container_hostname(run_id)
        or "mariadb" not in str(row[4]).lower()
        or not str(row[4]).startswith("11.8.8")
        or str(row[5]).lower() != EXPECTED_STORAGE_ENGINE.lower()
    ):
        raise ImproperlyConfigured("La identidad del servidor IoT desechable no coincide")


def validate_iot_migration_graph(nodes, deps_by_node):
    nodes = list(nodes)
    actual = set(nodes)
    problems = []
    if actual != AUTHORIZED_MIGRATIONS:
        problems.append("grafo de migraciones IoT inesperado")
    if any(app not in AUTHORIZED_APPS for app, _migration in nodes):
        problems.append("el grafo contiene una aplicación no autorizada")
    if _leaf_nodes(nodes, deps_by_node) != TERMINAL_MIGRATIONS:
        problems.append("nodos terminales IoT inesperados")
    for node, deps in deps_by_node.items():
        if any(dep[0] not in AUTHORIZED_APPS for dep in deps):
            problems.append(f"dependencia externa en {node!r}")
    if problems:
        raise ImproperlyConfigured("Grafo IoT desechable inválido: " + "; ".join(problems))


def validate_iot_migrations(connection=None):
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    validate_iot_migration_graph(
        loader.graph.nodes.keys(),
        {node: loader.graph.nodes[node].dependencies for node in loader.graph.nodes},
    )
    if connection is not None:
        from django.db.migrations.recorder import MigrationRecorder

        applied = set(MigrationRecorder(connection).applied_migrations())
        if applied != AUTHORIZED_MIGRATIONS:
            raise ImproperlyConfigured("Migraciones aplicadas distintas del grafo IoT autorizado")


def validate_iot_tables(connection):
    from django.apps import apps

    expected = {
        model._meta.db_table
        for label in AUTHORIZED_APPS
        for model in apps.get_app_config(label).get_models()
    }
    expected.update(AUXILIARY_TABLES)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE()"
        )
        actual = {str(row[0]) for row in cursor.fetchall()}
    forbidden = actual & FORBIDDEN_TABLE_NAMES
    if actual != expected or forbidden:
        raise ImproperlyConfigured("Tablas de la base IoT distintas de las autorizadas")


validate_iot_disposable_database(DATABASES)
