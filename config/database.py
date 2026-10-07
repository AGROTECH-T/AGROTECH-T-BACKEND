"""Base de datos única del backend.

Propósito: MariaDB en local, Postgres si el entorno trae DATABASE_URL y SQLite en pruebas.
Contexto: DB_ENGINE=sqlite solo lo define pytest. Render inyecta DATABASE_URL.
@author Cristian Deysdayr Jimenez
"""
import os
from pathlib import Path
from urllib.parse import unquote, urlparse


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
    options = {"charset": "utf8mb4"}
    if os.getenv("DB_SSL", "").lower() in {"1", "true", "yes"}:
        options["ssl"] = {}
    return {
        "ENGINE": engine,
        "NAME": os.getenv("DB_NAME", "agrotech_t"),
        "USER": os.getenv("DB_USER", "agrotech_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": options,
    }


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
