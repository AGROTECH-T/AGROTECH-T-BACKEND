"""Base de datos única del backend.

Propósito: elegir MariaDB en runtime y SQLite cuando las pruebas lo piden.
Contexto: DB_ENGINE=sqlite solo lo define pytest.
@author Cristian Deysdayr Jimenez
"""
import os
from pathlib import Path


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
    if engine == "mysql":
        engine = "django.db.backends.mysql"
    return {
        "ENGINE": engine,
        "NAME": os.getenv("DB_NAME", "agrotech_t"),
        "USER": os.getenv("DB_USER", "agrotech_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
    }
