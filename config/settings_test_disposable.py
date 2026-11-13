"""Configuración de pruebas sobre una MariaDB desechable por ejecución.

Propósito: aislar la validación de permisos en un contenedor efímero, con
nombres derivados de un identificador de ejecución y sin reutilizar las
credenciales ni el destino de ``config.settings_test``.

Contexto: la configuración solo se carga si el orquestador confirma la
ejecución y entrega un identificador y una contraseña por variables de
entorno. El destino queda fijado a ``127.0.0.1:3317`` y a un nombre que no
puede salir de la lista blanca dinámica.

@author Cristian Deysdayr Jimenez
"""
import os
import re

from django.core.exceptions import ImproperlyConfigured

from config.settings import *  # noqa: F401,F403

TEST_DB_ENGINE = "django.db.backends.mysql"
EXPECTED_SETTINGS_MODULE = "config.settings_test_disposable"
DEFAULT_TEST_DB_HOST = "127.0.0.1"
DEFAULT_TEST_DB_PORT = "3317"
EXPECTED_SERVER_PORT = "3306"
EXPECTED_STORAGE_ENGINE = "InnoDB"
EXPECTED_TEST_DB_USER = "agrotech_perm_validation"
AUTHORIZED_TEST_DB_USERS = frozenset({EXPECTED_TEST_DB_USER})
AUTHORIZED_TEST_DB_HOSTS = frozenset({DEFAULT_TEST_DB_HOST})
AUTHORIZED_TEST_DB_PORTS = frozenset({DEFAULT_TEST_DB_PORT})
ALLOWED_DATABASE_ALIASES = ("default",)
TEST_DB_NAME_PREFIX = "agrotech_permtest_"
TEST_DB_HOSTNAME_PREFIX = "agrotech-permtest-"
RUN_ID_PATTERN = r"^[0-9a-f]{12}$"
PROTECTED_DATABASES = (
    "agrotech_db",
    "agrotech_t",
    "agrotech_farms_test",
    "agrotech_farms_validation",
)
DISPOSABLE_CONFIRM_ENV = "AGROTECH_DISPOSABLE_CONFIRM"
DISPOSABLE_RUN_ID_ENV = "AGROTECH_DISPOSABLE_RUN_ID"
DISPOSABLE_DB_CREDENTIAL_ENV = "AGROTECH_DISPOSABLE_DB_PASSWORD"

_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def disposable_database_name(run_id: str) -> str:
    """Retorna el nombre de base autorizado para el identificador dado."""
    return f"{TEST_DB_NAME_PREFIX}{run_id}"


def disposable_container_hostname(run_id: str) -> str:
    """Retorna el hostname autorizado del contenedor desechable."""
    return f"{TEST_DB_HOSTNAME_PREFIX}{run_id}"


def _required_environment_value(name: str) -> str:
    """Obtiene una variable obligatoria sin exponer su valor en errores."""
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ImproperlyConfigured(
            f"La variable de entorno {name} es obligatoria y no puede estar vacía."
        )
    return value


def _required_run_id() -> str:
    """Valida y retorna el identificador de ejecución autorizado."""
    run_id = _required_environment_value(DISPOSABLE_RUN_ID_ENV)
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise ImproperlyConfigured(
            f"{DISPOSABLE_RUN_ID_ENV} debe ser un identificador de 12 "
            "caracteres hexadecimales en minúscula."
        )
    return run_id


def _require_confirmation() -> None:
    """Exige la confirmación explícita del orquestador."""
    if os.getenv(DISPOSABLE_CONFIRM_ENV) != "1":
        raise ImproperlyConfigured(
            f"{DISPOSABLE_CONFIRM_ENV} debe ser '1' para usar la base desechable."
        )


def validate_disposable_runtime_environment() -> None:
    """Valida las variables necesarias antes de abrir una conexión."""
    _require_confirmation()
    _required_run_id()
    _required_environment_value(DISPOSABLE_DB_CREDENTIAL_ENV)


def build_test_database() -> dict:
    """Construye la configuración MariaDB desechable autorizada."""
    _require_confirmation()
    run_id = _required_run_id()
    password = _required_environment_value(DISPOSABLE_DB_CREDENTIAL_ENV)
    name = disposable_database_name(run_id)
    return {
        "ENGINE": TEST_DB_ENGINE,
        "NAME": name,
        "USER": EXPECTED_TEST_DB_USER,
        "PASSWORD": password,
        "HOST": DEFAULT_TEST_DB_HOST,
        "PORT": DEFAULT_TEST_DB_PORT,
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": name},
    }


def _is_authorized_database_name(name) -> bool:
    """Comprueba que el nombre sea el prefijo autorizado más un run ID."""
    if not isinstance(name, str) or not name.startswith(TEST_DB_NAME_PREFIX):
        return False
    return _RUN_ID_RE.fullmatch(name[len(TEST_DB_NAME_PREFIX) :]) is not None


def validate_disposable_database(databases: dict) -> None:
    """Rechaza destinos que no pertenezcan al contenedor desechable."""
    default = databases.get("default") or {}
    test = default.get("TEST") or {}
    name = default.get("NAME")
    test_name = test.get("NAME")
    host = default.get("HOST")
    port = str(default.get("PORT", ""))
    user = default.get("USER")
    password = default.get("PASSWORD")
    problems = []
    if default.get("ENGINE") != TEST_DB_ENGINE:
        problems.append(f"motor inesperado: {default.get('ENGINE')!r}")
    if name in PROTECTED_DATABASES or test_name in PROTECTED_DATABASES:
        problems.append("se detectó una base protegida")
    if not _is_authorized_database_name(name):
        problems.append(f"nombre de base no autorizado: {name!r}")
    if not _is_authorized_database_name(test_name):
        problems.append(f"TEST.NAME no autorizado: {test_name!r}")
    if name != test_name:
        problems.append(
            f"TEST.NAME ({test_name!r}) no puede desviarse de NAME ({name!r})"
        )
    if user not in AUTHORIZED_TEST_DB_USERS:
        problems.append(f"usuario de BD no autorizado: {user!r}")
    if not isinstance(password, str) or not password.strip():
        problems.append("la contraseña de la base desechable no puede estar vacía")
    if host not in AUTHORIZED_TEST_DB_HOSTS:
        problems.append(f"host no autorizado: {host!r}")
    if port not in AUTHORIZED_TEST_DB_PORTS:
        problems.append(f"puerto no autorizado: {port!r}")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de pruebas desechable inválida: " + "; ".join(problems)
        )


def validate_disposable_environment(settings_module, databases: dict) -> None:
    """Valida el módulo, los aliases y el destino desechable."""
    problems = []
    if settings_module != EXPECTED_SETTINGS_MODULE:
        problems.append(f"módulo de settings no autorizado: {settings_module!r}")
    aliases = set(databases)
    if aliases != set(ALLOWED_DATABASE_ALIASES):
        problems.append(f"aliases de base de datos inesperados: {sorted(aliases)}")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de pruebas desechable inválida: " + "; ".join(problems)
        )
    validate_disposable_database(databases)


def validate_disposable_credentials(user) -> None:
    """Exige la cuenta dedicada de validación de permisos."""
    if user not in AUTHORIZED_TEST_DB_USERS:
        raise ImproperlyConfigured(
            f"El usuario de pruebas debe ser {EXPECTED_TEST_DB_USER}."
        )


def validate_live_disposable_database(connection) -> None:
    """Verifica en solo lectura la identidad real del contenedor desechable."""
    settings_dict = connection.settings_dict
    validate_disposable_database({"default": settings_dict})
    validate_disposable_credentials(settings_dict.get("USER"))

    run_id = _required_run_id()
    expected_name = disposable_database_name(run_id)
    expected_hostname = disposable_container_hostname(run_id)
    configured_name = settings_dict.get("NAME")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DATABASE(), CURRENT_USER(), @@port, @@hostname, "
            "@@default_storage_engine"
        )
        row = cursor.fetchone()
        if not row or len(row) < 5:
            raise ImproperlyConfigured(
                "No se pudo verificar la identidad de la base desechable."
            )
        database, current_user, server_port, hostname, storage_engine = row[:5]
        cursor.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME = %s",
            [expected_name],
        )
        schema_exists = cursor.fetchone() is not None

    actual_user = str(current_user).rsplit("@", 1)[0].strip("'\"")
    problems = []
    if configured_name != expected_name:
        problems.append("la configuración no usa la base del identificador actual")
    if str(database) != expected_name:
        problems.append("la conexión no usa la base autorizada")
    if actual_user not in AUTHORIZED_TEST_DB_USERS:
        problems.append("la conexión no usa el usuario de pruebas autorizado")
    if str(server_port) != EXPECTED_SERVER_PORT:
        problems.append("el servidor no usa el puerto interno autorizado")
    if str(hostname) != expected_hostname:
        problems.append("la conexión no apunta al contenedor autorizado")
    if str(storage_engine).lower() != EXPECTED_STORAGE_ENGINE.lower():
        problems.append("el motor de almacenamiento no es InnoDB")
    if not schema_exists:
        problems.append("el esquema de pruebas no existe")
    if problems:
        raise ImproperlyConfigured(
            "Verificación de la base desechable fallida: " + "; ".join(problems)
        )


DATABASES = {"default": build_test_database()}

validate_disposable_database(DATABASES)


# ---------------------------------------------------------------------------
# Redis desechable opcional (F10.3.4-A). Solo con AGROTECH_DISPOSABLE_REDIS=1,
# que el orquestador define con -Validate -WithRedis. El destino es fijo
# (127.0.0.1:16379, base 0) y la contraseña es la de la ejecución; REDIS_URL
# no se usa. Sin la marca, la caché heredada no cambia (LocMemCache).
# ---------------------------------------------------------------------------

DISPOSABLE_REDIS_ENABLED_ENV = "AGROTECH_DISPOSABLE_REDIS"
DISPOSABLE_REDIS_CREDENTIAL_ENV = "AGROTECH_DISPOSABLE_REDIS_PASSWORD"
DEFAULT_TEST_REDIS_HOST = "127.0.0.1"
DEFAULT_TEST_REDIS_PORT = "16379"
EXPECTED_REDIS_SERVER_PORT = "6379"
EXPECTED_REDIS_VERSION = "7.4.12"
TEST_REDIS_CACHE_DB = 0
TEST_REDIS_MARKER_DB = 1
REDIS_MARKER_KEY = "agrotech:disposable:run-id"
REDIS_CACHE_BACKEND = "django_redis.cache.RedisCache"
REDIS_CLIENT_CLASS = "django_redis.client.DefaultClient"
REDIS_SOCKET_TIMEOUT_SECONDS = 5


def disposable_redis_enabled() -> bool:
    """Indica si la ejecución usa el Redis desechable; rechaza valores ambiguos."""
    value = os.getenv(DISPOSABLE_REDIS_ENABLED_ENV)
    if value is None or value == "":
        return False
    if value != "1":
        raise ImproperlyConfigured(
            f"{DISPOSABLE_REDIS_ENABLED_ENV} solo admite '1' o estar vacía."
        )
    return True


def disposable_redis_location() -> str:
    """Ubicación fija de la caché en el Redis desechable."""
    return f"redis://{DEFAULT_TEST_REDIS_HOST}:{DEFAULT_TEST_REDIS_PORT}/{TEST_REDIS_CACHE_DB}"


def build_test_caches():
    """Construye la caché Redis desechable o devuelve ``None`` si no se pidió."""
    if not disposable_redis_enabled():
        return None
    _require_confirmation()
    _required_run_id()
    password = _required_environment_value(DISPOSABLE_REDIS_CREDENTIAL_ENV)
    return {
        "default": {
            "BACKEND": REDIS_CACHE_BACKEND,
            "LOCATION": disposable_redis_location(),
            "OPTIONS": {
                "CLIENT_CLASS": REDIS_CLIENT_CLASS,
                "PASSWORD": password,
                "SOCKET_CONNECT_TIMEOUT": REDIS_SOCKET_TIMEOUT_SECONDS,
                "SOCKET_TIMEOUT": REDIS_SOCKET_TIMEOUT_SECONDS,
            },
        }
    }


def validate_disposable_cache(caches: dict, ignore_exceptions=False) -> None:
    """Con Redis desechable, rechaza cualquier caché distinta del destino fijo.

    ``ignore_exceptions`` es ``DJANGO_REDIS_IGNORE_EXCEPTIONS``: debe ser falso
    para que un fallo de Redis no se oculte como un fallo de caché silencioso.
    """
    if not disposable_redis_enabled():
        return
    problems = []
    if set(caches or {}) != {"default"}:
        problems.append(f"aliases de caché inesperados: {sorted(caches or {})}")
    default = (caches or {}).get("default") or {}
    options = default.get("OPTIONS") or {}
    if default.get("BACKEND") != REDIS_CACHE_BACKEND:
        problems.append(f"backend de caché inesperado: {default.get('BACKEND')!r}")
    if default.get("LOCATION") != disposable_redis_location():
        problems.append("ubicación de caché distinta del Redis desechable")
    if options.get("CLIENT_CLASS") != REDIS_CLIENT_CLASS:
        problems.append("cliente de caché inesperado")
    password = options.get("PASSWORD")
    if not isinstance(password, str) or not password.strip():
        problems.append("la contraseña del Redis desechable no puede estar vacía")
    if options.get("IGNORE_EXCEPTIONS") or ignore_exceptions:
        problems.append("los errores de Redis no pueden ignorarse")
    if problems:
        raise ImproperlyConfigured(
            "Configuración de caché desechable inválida: " + "; ".join(problems)
        )


def validate_live_disposable_redis(client_factory=None) -> None:
    """Verifica en solo lectura que el Redis conectado es el de la ejecución.

    Comprueba versión, puerto interno y la marca con el RunId que el
    orquestador escribe en la base 1 tras validar el contenedor. La conexión
    usa el destino fijo y la contraseña de la ejecución; no lee ni escribe la
    base de la caché.
    """
    run_id = _required_run_id()
    password = _required_environment_value(DISPOSABLE_REDIS_CREDENTIAL_ENV)
    if client_factory is None:
        import redis

        def client_factory():
            return redis.Redis(
                host=DEFAULT_TEST_REDIS_HOST,
                port=int(DEFAULT_TEST_REDIS_PORT),
                db=TEST_REDIS_MARKER_DB,
                password=password,
                socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            )

    client = client_factory()
    try:
        info = client.info("server")
        marker = client.get(REDIS_MARKER_KEY)
    except Exception as exc:  # noqa: BLE001 - se informa sin detalles sensibles
        raise ImproperlyConfigured(
            f"No se pudo verificar el Redis desechable ({type(exc).__name__})."
        ) from None
    finally:
        client.close()
    if isinstance(marker, bytes):
        marker = marker.decode("utf-8", "replace")
    problems = []
    if str(info.get("redis_version")) != EXPECTED_REDIS_VERSION:
        problems.append("la versión de Redis no es la esperada")
    if str(info.get("tcp_port")) != EXPECTED_REDIS_SERVER_PORT:
        problems.append("Redis no usa el puerto interno autorizado")
    if marker != run_id:
        problems.append("la marca de identidad de Redis no corresponde a esta ejecución")
    if problems:
        raise ImproperlyConfigured(
            "Verificación del Redis desechable fallida: " + "; ".join(problems)
        )


_DISPOSABLE_CACHES = build_test_caches()
if _DISPOSABLE_CACHES is not None:
    CACHES = _DISPOSABLE_CACHES
    validate_disposable_cache(CACHES)
