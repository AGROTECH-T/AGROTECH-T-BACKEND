"""Selección de base de datos según el entorno.

Propósito: comprobar que DATABASE_URL no pisa SQLite ni MariaDB local.
Contexto: Render define DATABASE_URL; las pruebas definen DB_ENGINE=sqlite.
@author Cristian Deysdayr Jimenez
"""
from pathlib import Path

from config.database import database


def test_sqlite_ignores_database_url(monkeypatch, tmp_path: Path):
    """La suite sigue en SQLite aunque exista una URL de Postgres."""
    monkeypatch.setenv("DB_ENGINE", "sqlite")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/agrotech")
    chosen = database(tmp_path)
    assert chosen["ENGINE"] == "django.db.backends.sqlite3"


def test_database_url_selects_postgres(monkeypatch, tmp_path: Path):
    """Una URL externa exige SSL y no deja la clave en el código."""
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:secret@db.example.com:5432/agrotech_t",
    )
    chosen = database(tmp_path)
    assert chosen["ENGINE"] == "django.db.backends.postgresql"
    assert chosen["NAME"] == "agrotech_t"
    assert chosen["HOST"] == "db.example.com"
    assert chosen["OPTIONS"]["sslmode"] == "require"


def test_mariadb_ssl_comes_only_from_env(monkeypatch, tmp_path: Path):
    """TLS se activa con DB_SSL y no queda un host escrito en el código."""
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_SSL", "true")
    monkeypatch.setenv("DB_HOST", "db.example")
    chosen = database(tmp_path)
    assert chosen["OPTIONS"]["ssl"] == {}
    assert chosen["HOST"] == "db.example"


def test_without_url_keeps_mariadb(monkeypatch, tmp_path: Path):
    """Sin DATABASE_URL el runtime local sigue en MariaDB."""
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_NAME", "agrotech_t")
    chosen = database(tmp_path)
    assert chosen["ENGINE"] == "django.db.backends.mysql"
    assert chosen["OPTIONS"]["charset"] == "utf8mb4"
