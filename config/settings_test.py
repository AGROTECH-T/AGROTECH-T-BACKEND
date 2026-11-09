"""Configuración de pruebas sobre MariaDB para AGROTECH-T-BACKEND.

Propósito: aislar las pruebas en una base autorizada por lista blanca, sin
depender de las variables generales ni de ``DATABASE_URL``.

Contexto: reutiliza la configuración general y fija de forma explícita el motor
y los límites del destino. El nombre, el host y el puerto pueden parametrizarse
por variables temporales, pero solo si coinciden con los valores autorizados.
Las credenciales se toman únicamente de ``TEST_DB_USER`` y ``TEST_DB_PASSWORD``,
que no tienen valores por defecto y nunca se escriben en archivos. Durante la
importación las credenciales son opcionales: ``build_test_database`` conserva los
valores tal cual (sin ``strip``) y devuelve ``None`` si faltan. La exigencia de
credenciales válidas vive en ``validate_test_credentials`` y se aplica desde
``conftest.py`` antes de preparar la base. La barrera ``validate_test_database``
rechaza cualquier destino no autorizado antes de que Django pueda abrir una
conexión.

@author Cristian Deysdayr Jimenez
"""
import os

from django.core.exceptions import ImproperlyConfigured

from config.settings import *  # noqa: F401,F403

TEST_DB_ENGINE = "django.db.backends.mysql"
DEFAULT_TEST_DB_NAME = "agrotech_farms_validation"
AUTHORIZED_TEST_DATABASES = frozenset({DEFAULT_TEST_DB_NAME})
PROTECTED_DATABASES = ("agrotech_db", "agrotech_t", "agrotech_farms_test")
DEFAULT_TEST_DB_HOST = "127.0.0.1"
DEFAULT_TEST_DB_PORT = "3307"
AUTHORIZED_TEST_DB_HOSTS = frozenset({"127.0.0.1", "localhost"})
AUTHORIZED_TEST_DB_PORTS = frozenset({"3307"})
EXPECTED_TEST_DB_USER = "agrotech_validation_user"
AUTHORIZED_TEST_DB_USERS = frozenset({EXPECTED_TEST_DB_USER})
EXPECTED_SETTINGS_MODULE = "config.settings_test"
ALLOWED_DATABASE_ALIASES = ("default",)


def _test_db_name() -> str:
    """Retorna el nombre de base solicitado por el entorno."""
    return os.getenv("TEST_DB_NAME", DEFAULT_TEST_DB_NAME)


def build_test_database() -> dict:
    """Construye la configuración MariaDB aislada de pruebas.

    El destino solo se acepta si ``validate_test_database`` lo autoriza. Las
    credenciales provienen del entorno del proceso y se conservan tal cual, sin
    ``strip``: si faltan quedan como ``None`` y su exigencia se aplica en
    ``validate_test_credentials`` antes de preparar la base.
    """
    name = _test_db_name()
    user = os.getenv("TEST_DB_USER")
    password = os.getenv("TEST_DB_PASSWORD")
    return {
        "ENGINE": TEST_DB_ENGINE,
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": os.getenv("TEST_DB_HOST", DEFAULT_TEST_DB_HOST),
        "PORT": os.getenv("TEST_DB_PORT", DEFAULT_TEST_DB_PORT),
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": name},
    }


def validate_test_database(databases: dict) -> None:
    """Rechaza destinos que no estén en la lista blanca autorizada.

    Se ejecuta al importar la configuración, antes de que Django abra una
    conexión. Comprueba motor, nombre, ``TEST.NAME``, host y puerto, y bloquea
    explícitamente las bases protegidas ``agrotech_db``, ``agrotech_t`` y
    ``agrotech_farms_test``.
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
    if name in PROTECTED_DATABASES or test_name in PROTECTED_DATABASES:
        problems.append("se detectó una base protegida")
    if name not in AUTHORIZED_TEST_DATABASES:
        problems.append(f"nombre de base no autorizado: {name!r}")
    if test_name not in AUTHORIZED_TEST_DATABASES:
        problems.append(f"TEST.NAME no autorizado: {test_name!r}")
    if name != test_name:
        problems.append(
            f"TEST.NAME ({test_name!r}) no puede desviarse de NAME ({name!r})"
        )
    user = default.get("USER")
    if user is not None and user not in AUTHORIZED_TEST_DB_USERS:
        problems.append(
            f"usuario de BD no autorizado: {user!r} "
            f"(esperado uno de {sorted(AUTHORIZED_TEST_DB_USERS)})"
        )
    if host not in AUTHORIZED_TEST_DB_HOSTS:
        problems.append(f"host no autorizado: {host!r}")
    if port not in AUTHORIZED_TEST_DB_PORTS:
        problems.append(f"puerto no autorizado: {port!r}")
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


def validate_test_credentials(user, password) -> None:
    """Exige credenciales válidas antes de preparar la base de pruebas.

    Rechaza usuario o contraseña ausentes, vacíos o compuestos solo por
    espacios, y exige que el usuario pertenezca a la lista blanca. No aplica
    ``strip``: conserva los valores tal cual, incluidos los espacios legítimos
    de la contraseña. Los mensajes nombran la variable y nunca exponen su
    valor.
    """
    problems = []
    if user is None or not str(user).strip():
        problems.append("TEST_DB_USER es obligatorio y no puede estar vacío")
    elif user not in AUTHORIZED_TEST_DB_USERS:
        problems.append(
            f"usuario de pruebas no autorizado: {user!r} "
            f"(esperado uno de {sorted(AUTHORIZED_TEST_DB_USERS)})"
        )
    if password is None or not str(password).strip():
        problems.append("TEST_DB_PASSWORD es obligatoria y no puede estar vacía")
    if problems:
        raise ImproperlyConfigured(
            "Credenciales de pruebas inválidas: " + "; ".join(problems)
        )


def validate_live_test_database(connection) -> None:
    """Verifica en solo lectura la base real, la cuenta y el puerto de conexión.

    La conexión debe apuntar a una base de la lista blanca y usar
    ``agrotech_validation_user``. Solo se ejecutan consultas ``SELECT``. Si el
    esquema no existe o no puede verificarse, se lanza
    ``ImproperlyConfigured`` antes de que pytest-django prepare la base.
    """
    settings_dict = connection.settings_dict
    validate_test_database({"default": settings_dict})
    validate_test_credentials(
        settings_dict.get("USER"), settings_dict.get("PASSWORD")
    )

    expected_name = settings_dict.get("NAME")
    expected_port = str(settings_dict.get("PORT", ""))

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
            [expected_name],
        )
        schema_exists = cursor.fetchone() is not None

    actual_user = str(current_user).rsplit("@", 1)[0].strip("'\"")
    problems = []
    if str(database) != expected_name:
        problems.append("la conexión no usa la base autorizada")
    if actual_user not in AUTHORIZED_TEST_DB_USERS:
        problems.append("la conexión no usa el usuario de pruebas autorizado")
    if str(server_port) != expected_port:
        problems.append("la conexión no usa el puerto autorizado")
    if not schema_exists:
        problems.append("el esquema de pruebas no existe")
    if problems:
        raise ImproperlyConfigured(
            "Verificación de la base de pruebas fallida: " + "; ".join(problems)
        )


DATABASES = {"default": build_test_database()}

validate_test_database(DATABASES)
