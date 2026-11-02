"""Configuración de pruebas sobre MariaDB para AGROTECH-T-BACKEND.

Propósito: aislar las pruebas en la base autorizada ``agrotech_farms_test``
sin depender de las variables generales ni de ``DATABASE_URL``.

Contexto: reutiliza la configuración general y fija de forma explícita el
motor, el nombre de base, el ``TEST.NAME``, el host y el puerto. Las
credenciales se toman únicamente de las variables temporales ``TEST_DB_USER``
y ``TEST_DB_PASSWORD``, que no tienen valores por defecto y nunca se escriben
en archivos. La barrera ``validate_test_database`` rechaza cualquier destino
distinto del autorizado antes de que Django pueda abrir una conexión.

@author Cristian Deysdayr Jimenez
"""
import os

from django.core.exceptions import ImproperlyConfigured

from config.settings import *  # noqa: F401,F403

TEST_DB_ENGINE = "django.db.backends.mysql"
TEST_DB_NAME = "agrotech_farms_test"
TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = "3307"
EXPECTED_TEST_DB_USER = "agrotech_test_user"
PROTECTED_DATABASES = ("agrotech_db", "agrotech_t")
EXPECTED_SETTINGS_MODULE = "config.settings_test"
ALLOWED_DATABASE_ALIASES = ("default",)


def build_test_database() -> dict:
    """Construye la configuración MariaDB aislada de pruebas.

    Los valores de destino están fijos; solo las credenciales provienen del
    entorno del proceso y quedan sin definir si no se suministran.
    """
    return {
        "ENGINE": TEST_DB_ENGINE,
        "NAME": TEST_DB_NAME,
        "USER": os.getenv("TEST_DB_USER"),
        "PASSWORD": os.getenv("TEST_DB_PASSWORD"),
        "HOST": TEST_DB_HOST,
        "PORT": TEST_DB_PORT,
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": TEST_DB_NAME},
    }


def validate_test_database(databases: dict) -> None:
    """Rechaza destinos distintos de la base de pruebas autorizada.

    Se ejecuta al importar la configuración, antes de que Django abra una
    conexión. Comprueba motor, nombre, ``TEST.NAME``, host y puerto, y bloquea
    explícitamente las bases protegidas ``agrotech_db`` y ``agrotech_t``.
    """
    default = databases.get("default") or {}
    test = default.get("TEST") or {}
    name = default.get("NAME")
    test_name = test.get("NAME")
    host = default.get("HOST")
    port = str(default.get("PORT", ""))
    problems = []
    if default.get("ENGINE") != TEST_DB_ENGINE:
        problems.append(f"motor inesperado: {default.get('ENGINE')!r}")
    if name != TEST_DB_NAME:
        problems.append(f"nombre de base no autorizado: {name!r}")
    if test_name != TEST_DB_NAME:
        problems.append(f"TEST.NAME no autorizado: {test_name!r}")
    if name in PROTECTED_DATABASES or test_name in PROTECTED_DATABASES:
        problems.append("se detectó una base protegida")
    if host != TEST_DB_HOST:
        problems.append(f"host inesperado: {host!r}")
    if port != TEST_DB_PORT:
        problems.append(f"puerto inesperado: {port!r}")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de pruebas inválida: " + "; ".join(problems)
        )


def validate_test_environment(settings_module, databases: dict) -> None:
    """Valida el módulo de settings y el conjunto de aliases permitidos.

    Complementa ``validate_test_database`` para la guarda de pytest: exige el
    módulo ``config.settings_test`` y que solo exista el alias ``default``.
    """
    problems = []
    if settings_module != EXPECTED_SETTINGS_MODULE:
        problems.append(f"módulo de settings no autorizado: {settings_module!r}")
    aliases = set(databases)
    if aliases != set(ALLOWED_DATABASE_ALIASES):
        problems.append(f"aliases de base de datos inesperados: {sorted(aliases)}")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de pruebas inválida: " + "; ".join(problems)
        )
    validate_test_database(databases)


def validate_test_credentials(user) -> None:
    """Exige que la conexión use exclusivamente la cuenta de pruebas."""
    if user != EXPECTED_TEST_DB_USER:
        raise ImproperlyConfigured(
            "El usuario de pruebas debe ser agrotech_test_user."
        )


def validate_live_test_database(connection) -> None:
    """Verifica en solo lectura la base real, la cuenta y el puerto de conexión.

    La conexión debe apuntar a ``agrotech_farms_test`` y usar
    ``agrotech_test_user``. Solo se ejecutan consultas ``SELECT``. Si el
    esquema no existe o no puede verificarse, se lanza
    ``ImproperlyConfigured`` antes de que pytest-django prepare la base.
    """
    validate_test_database({"default": connection.settings_dict})
    validate_test_credentials(connection.settings_dict.get("USER"))

    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), CURRENT_USER(), @@port")
        row = cursor.fetchone()
        if not row or len(row) < 3:
            raise ImproperlyConfigured(
                "No se pudo verificar la identidad de la base de pruebas."
            )
        database, current_user, server_port = row[:3]
        cursor.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME = %s",
            [TEST_DB_NAME],
        )
        schema_exists = cursor.fetchone() is not None

    actual_user = str(current_user).rsplit("@", 1)[0].strip("'\"")
    problems = []
    if str(database) != TEST_DB_NAME:
        problems.append("la conexión no usa la base autorizada")
    if actual_user != EXPECTED_TEST_DB_USER:
        problems.append("la conexión no usa el usuario de pruebas autorizado")
    if str(server_port) != TEST_DB_PORT:
        problems.append("la conexión no usa el puerto autorizado")
    if not schema_exists:
        problems.append("el esquema de pruebas no existe")
    if problems:
        raise ImproperlyConfigured(
            "Verificación de la base de pruebas fallida: " + "; ".join(problems)
        )


DATABASES = {"default": build_test_database()}

validate_test_database(DATABASES)
