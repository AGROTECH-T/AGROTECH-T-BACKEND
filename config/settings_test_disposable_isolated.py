"""Configuración de validación aislada sobre una MariaDB desechable.

Propósito: ejecutar las pruebas ORM de Farms y Trazabilidad aplicando únicamente
las migraciones de ``apps.accounts``, ``apps.farms`` y ``apps.trazabilidad``,
heredando las protecciones de ``config.settings_test_disposable``.

Contexto: el aislamiento se logra definiendo un ``INSTALLED_APPS`` mínimo que
excluye las aplicaciones contrib de Django y los dominios ajenos, por lo que el
descubrimiento de migraciones solo considera las tres aplicaciones autorizadas.
``EXPECTED_SETTINGS_MODULE`` y ``validate_disposable_environment`` se redefinen
localmente porque la versión heredada mediante ``import *`` conserva en su
variable global el módulo original. La validación del destino de base se delega
en ``validate_disposable_database`` heredado, que mantiene las comprobaciones de
identidad, hostname, puerto, usuario, nombre de base, InnoDB y confirmación
explícita.

@author Cristian Deysdayr Jimenez
"""
from django.core.exceptions import ImproperlyConfigured

from config.settings_test_disposable import *  # noqa: F401,F403
from config.settings_test_disposable import (
    ALLOWED_DATABASE_ALIASES,
    validate_disposable_database,
)

INSTALLED_APPS = [
    "apps.accounts",
    "apps.farms",
    "apps.trazabilidad",
]

# A1: Permissions no está instalada en F2-2; la limpieza queda desactivada de
# forma explícita y cualquier otra combinación falla en apps.farms.services.
FARMS_PERMISSION_CLEANUP = "disabled"

ROOT_URLCONF = "config.urls_test_disposable_isolated"

EXPECTED_SETTINGS_MODULE = "config.settings_test_disposable_isolated"

AUTHORIZED_APPS = frozenset({"accounts", "farms", "trazabilidad"})

AUTHORIZED_MIGRATIONS = frozenset(
    {
        ("accounts", "0001_initial"),
        ("accounts", "0002_harden_sessions"),
        (
            "accounts",
            "0003_logincode_id_logincode_purpose_user_email_verified_and_more",
        ),
        (
            "accounts",
            "0004_session_closed_at_session_created_at_session_id_and_more",
        ),
        ("accounts", "0005_logincode_delivery_fields"),
        ("accounts", "0006_user_can_create_farms"),
        ("farms", "0001_initial"),
        ("farms", "0002_farm_deactivation"),
        (
            "farms",
            "0003_farmmembership_left_at_farmmembership_revoked_at_and_more",
        ),
        ("farms", "0004_farminvitation"),
        ("farms", "0005_farminvitation_code_hash"),
        ("trazabilidad", "0001_initial"),
        ("trazabilidad", "0002_alter_auditevent_event_type"),
        ("trazabilidad", "0003_alter_auditevent_event_type"),
        ("trazabilidad", "0004_alter_auditevent_event_type"),
        ("trazabilidad", "0005_alter_auditevent_event_type"),
        ("trazabilidad", "0006_alter_auditevent_event_type"),
        ("trazabilidad", "0007_alter_auditevent_event_type"),
        ("trazabilidad", "0008_alter_auditevent_event_type"),
    }
)

TERMINAL_MIGRATIONS = frozenset(
    {
        ("accounts", "0006_user_can_create_farms"),
        ("farms", "0005_farminvitation_code_hash"),
        ("trazabilidad", "0008_alter_auditevent_event_type"),
    }
)

AUXILIARY_TABLES = frozenset({"django_migrations"})

FORBIDDEN_TABLE_NAMES = frozenset(
    {
        "django_content_type",
        "django_session",
        "django_admin_log",
        "auth_user",
        "auth_group",
        "auth_permission",
        "auth_user_groups",
        "auth_user_user_permissions",
        "permission_assignments",
        "permission_restrictions",
        "approval_policies",
        "inventory_products",
        "inventory_movements",
    }
)


def _leaf_nodes(nodes, deps_by_node) -> set:
    """Calcula los nodos que no son dependencia de ningún otro nodo."""
    dependents = set()
    for deps in deps_by_node.values():
        for dep in deps:
            dependents.add(tuple(dep))
    return {tuple(node) for node in nodes} - dependents


def validate_isolated_migration_graph(nodes, deps_by_node) -> None:
    """Valida el grafo de migraciones aisladas sin acceder a disco ni base.

    ``nodes`` es un iterable de claves ``(app_label, name)`` y ``deps_by_node``
    asocia cada clave con sus dependencias directas, cada una expresada como
    tupla ``(app_label, name)``.
    """
    nodes = list(nodes)
    problems = []

    actual = set(nodes)
    if actual != AUTHORIZED_MIGRATIONS:
        missing = sorted(AUTHORIZED_MIGRATIONS - actual)
        extra = sorted(actual - AUTHORIZED_MIGRATIONS)
        if missing:
            problems.append(f"migraciones autorizadas ausentes: {missing}")
        if extra:
            problems.append(f"migraciones no autorizadas presentes: {extra}")

    for app_label, _name in nodes:
        if app_label not in AUTHORIZED_APPS:
            problems.append(f"aplicación externa en el grafo: {app_label!r}")

    terminals = _leaf_nodes(nodes, deps_by_node)
    if terminals != TERMINAL_MIGRATIONS:
        problems.append(f"nodos terminales inesperados: {sorted(terminals)}")

    for node in nodes:
        for dep in deps_by_node.get(node, ()):
            dep_app = dep[0] if isinstance(dep, tuple) and len(dep) == 2 else dep
            if dep_app not in AUTHORIZED_APPS:
                problems.append(f"dependencia externa en {node!r}: {dep!r}")

    if problems:
        raise ImproperlyConfigured(
            "Grafo de migraciones aisladas inválido: " + "; ".join(problems)
        )


def validate_isolated_migrations() -> None:
    """Valida el grafo de migraciones disponibles en disco (sin base de datos)."""
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    nodes = list(loader.graph.nodes.keys())
    deps_by_node = {
        node: loader.graph.nodes[node].dependencies for node in nodes
    }
    validate_isolated_migration_graph(nodes, deps_by_node)


def validate_isolated_applied_migrations(connection) -> None:
    """Valida las migraciones registradas como aplicadas en ``django_migrations``."""
    from django.db.migrations.recorder import MigrationRecorder

    recorder = MigrationRecorder(connection)
    applied = set(recorder.applied_migrations())
    problems = []
    missing = sorted(AUTHORIZED_MIGRATIONS - applied)
    extra = sorted(applied - AUTHORIZED_MIGRATIONS)
    if missing:
        problems.append(f"migraciones autorizadas no aplicadas: {missing}")
    if extra:
        problems.append(f"migraciones aplicadas no autorizadas: {extra}")
    if problems:
        raise ImproperlyConfigured(
            "Migraciones aplicadas aisladas inválidas: " + "; ".join(problems)
        )


def expected_isolated_tables() -> set:
    """Retorna las tablas autorizadas derivadas de los modelos de las apps aisladas."""
    from django.apps import apps

    tables = set()
    for label in AUTHORIZED_APPS:
        for model in apps.get_app_config(label).get_models():
            tables.add(model._meta.db_table)
    tables.update(AUXILIARY_TABLES)
    return tables


def validate_isolated_tables(connection) -> None:
    """Audita en solo lectura las tablas de la base desechable.

    Exige que existan exactamente las tablas derivadas de los modelos de las
    aplicaciones autorizadas más las tablas auxiliares necesarias, y rechaza las
    tablas de módulos ajenos (auth, contenttypes, sessions de Django,
    permissions, inventario, etc.).
    """
    expected = expected_isolated_tables()

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE()"
        )
        rows = cursor.fetchall()

    actual = {str(row[0]) for row in rows}
    problems = []

    forbidden = sorted(actual & FORBIDDEN_TABLE_NAMES)
    if forbidden:
        problems.append(f"tablas prohibidas presentes: {forbidden}")

    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            problems.append(f"tablas autorizadas ausentes: {missing}")
        if extra:
            problems.append(f"tablas no autorizadas presentes: {extra}")

    if problems:
        raise ImproperlyConfigured(
            "Auditoría de tablas aisladas fallida: " + "; ".join(problems)
        )


def validate_disposable_environment(settings_module, databases: dict) -> None:
    """Valida el módulo aislado, los aliases y delega el destino en la guarda heredada."""
    problems = []
    if settings_module != EXPECTED_SETTINGS_MODULE:
        problems.append(f"módulo de settings no autorizado: {settings_module!r}")
    aliases = set(databases)
    if aliases != set(ALLOWED_DATABASE_ALIASES):
        problems.append(f"aliases de base de datos inesperados: {sorted(aliases)}")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de pruebas desechable aislada inválida: "
            + "; ".join(problems)
        )
    validate_disposable_database(databases)
