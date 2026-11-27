"""Base de datos única del backend.

Propósito: MariaDB en local, Postgres si el entorno trae DATABASE_URL y SQLite en pruebas.
Contexto: DB_ENGINE=sqlite solo lo define pytest. Render inyecta DATABASE_URL.
@author Cristian Deysdayr Jimenez
"""
import os
import ssl
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


def database(base_dir: Path) -> dict:
    """Arma la configuración del backend default.

    @param base_dir: raíz del proyecto, carpeta del archivo SQLite de pruebas.
    @returns dict de opciones de DATABASES['default'].
    """
    engine = os.getenv("DB_ENGINE", "django.db.backends.mysql")
    if engine in {"sqlite", "django.db.backends.sqlite3"}:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": base_dir / "test.sqlite3",
        }
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres"):
        return _postgres(url)
    if engine == "mysql":
        engine = "django.db.backends.mysql"
    return _mysql(engine)


def _mysql(engine: str) -> dict:
    """Arma MariaDB con las variables DB_*.

    @param engine: backend de Django ya normalizado.
    @returns dict de conexión MariaDB.
    """
    # BUILD-FLEPSI-02K (K-H1): explicit READ COMMITTED. The IoT broker identity
    # dedupe/limits check "exists/count" after taking a row lock; that is only
    # correct if each statement sees the latest committed data (under
    # REPEATABLE READ an earlier read would pin an older snapshot). It equals
    # Django's MySQL default, so the effective behaviour does not change.
    # La MariaDB gratis de Layerbase tarda unos 20 s en despertar y PyMySQL
    # corta la conexión a los 10. Sin esta espera el primer uso devuelve 500.
    options = {
        "charset": "utf8mb4",
        "isolation_level": "read committed",
        "connect_timeout": 30,
    }
    ssl_options = _mariadb_ssl_options()
    if ssl_options is not None:
        options["ssl"] = ssl_options
    elif os.getenv("DB_SSL", "").lower() in {"1", "true", "yes"}:
        # Legacy flag kept unchanged. With PyMySQL an empty dict is treated as
        # "no SSL options": TLS is only opportunistic (PREFERRED, no
        # certificate verification, plaintext fallback). Use DB_SSL_MODE to
        # enforce TLS.
        options["ssl"] = {}
    return {
        "ENGINE": "config.mysql",
        "NAME": os.getenv("DB_NAME", "agrotech_t"),
        "USER": os.getenv("DB_USER", "agrotech_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": options,
    }


SSL_MODES = ("required", "verify_identity")


def _mariadb_ssl_options() -> dict | None:
    """Opciones TLS explícitas para PyMySQL a partir de ``DB_SSL_MODE``.

    - vacío: sin cambios (se aplica el comportamiento heredado de ``DB_SSL``).
    - ``required``: exige TLS (sin TLS no hay conexión), sin verificar el
      certificado del servidor.
    - ``verify_identity``: exige TLS, verifica la cadena con ``DB_SSL_CA`` o,
      si no se define, con los certificados del sistema, y verifica el host.

    @returns dict para ``OPTIONS["ssl"]`` o ``None`` si no se pidió modo.
    @raises ImproperlyConfigured: modo desconocido o sin CA disponible.
    """
    mode = os.getenv("DB_SSL_MODE", "").strip().lower()
    if not mode:
        return None
    if mode not in SSL_MODES:
        raise ImproperlyConfigured(
            f"DB_SSL_MODE debe ser uno de: {', '.join(SSL_MODES)}."
        )
    if mode == "required":
        # A non-empty dict makes PyMySQL require TLS; verify_mode False keeps
        # the certificate unverified on purpose for this mode.
        return {"verify_mode": False, "check_hostname": False}
    ca_file = os.getenv("DB_SSL_CA", "").strip()
    if ca_file:
        if not Path(ca_file).is_file():
            raise ImproperlyConfigured("DB_SSL_CA no apunta a un archivo existente.")
        return {"ca": ca_file, "verify_mode": True, "check_hostname": True}
    paths = ssl.get_default_verify_paths()
    if paths.cafile and Path(paths.cafile).is_file():
        return {"ca": paths.cafile, "verify_mode": True, "check_hostname": True}
    if paths.capath and Path(paths.capath).is_dir():
        return {"capath": paths.capath, "verify_mode": True, "check_hostname": True}
    raise ImproperlyConfigured(
        "DB_SSL_MODE=verify_identity requiere DB_SSL_CA o certificados del sistema."
    )


def _postgres(url: str) -> dict:
    """Traduce DATABASE_URL al diccionario de Django.

    @param url: URL postgresql del entorno, sin escribirla en el código.
    @returns dict de conexión Postgres.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    options = {"sslmode": "require"} if "." in host else {}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": host,
        "PORT": str(parsed.port or 5432),
        "OPTIONS": options,
    }
