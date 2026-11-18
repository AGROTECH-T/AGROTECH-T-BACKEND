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


def _mariadb_env(monkeypatch):
    monkeypatch.delenv("DB_ENGINE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_SSL", raising=False)
    monkeypatch.delenv("DB_SSL_MODE", raising=False)
    monkeypatch.delenv("DB_SSL_CA", raising=False)


def test_without_ssl_variables_has_no_ssl_option(monkeypatch, tmp_path: Path):
    """Sin DB_SSL ni DB_SSL_MODE no se agrega configuración TLS."""
    _mariadb_env(monkeypatch)
    chosen = database(tmp_path)
    assert "ssl" not in chosen["OPTIONS"]


def test_ssl_mode_required_forces_tls_without_verification(monkeypatch, tmp_path: Path):
    """required: dict no vacío, así PyMySQL exige TLS; no verifica certificado."""
    _mariadb_env(monkeypatch)
    monkeypatch.setenv("DB_SSL_MODE", "required")
    monkeypatch.setenv("DB_SSL", "true")
    ssl_options = database(tmp_path)["OPTIONS"]["ssl"]
    assert ssl_options == {"verify_mode": False, "check_hostname": False}


def test_ssl_mode_verify_identity_uses_explicit_ca(monkeypatch, tmp_path: Path):
    """verify_identity con DB_SSL_CA verifica cadena y nombre de host."""
    _mariadb_env(monkeypatch)
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder", encoding="ascii")
    monkeypatch.setenv("DB_SSL_MODE", "VERIFY_IDENTITY")
    monkeypatch.setenv("DB_SSL_CA", str(ca))
    ssl_options = database(tmp_path)["OPTIONS"]["ssl"]
    assert ssl_options == {"ca": str(ca), "verify_mode": True, "check_hostname": True}


def test_ssl_mode_verify_identity_falls_back_to_system_store(monkeypatch, tmp_path: Path):
    """Sin DB_SSL_CA usa el almacén del sistema y sigue verificando el host."""
    import ssl as ssl_module
    from types import SimpleNamespace

    _mariadb_env(monkeypatch)
    bundle = tmp_path / "system-ca.pem"
    bundle.write_text("placeholder", encoding="ascii")
    monkeypatch.setattr(
        ssl_module,
        "get_default_verify_paths",
        lambda: SimpleNamespace(cafile=str(bundle), capath=None),
    )
    monkeypatch.setenv("DB_SSL_MODE", "verify_identity")
    ssl_options = database(tmp_path)["OPTIONS"]["ssl"]
    assert ssl_options == {"ca": str(bundle), "verify_mode": True, "check_hostname": True}


def test_ssl_mode_rejects_unknown_value_and_missing_ca(monkeypatch, tmp_path: Path):
    """Valores desconocidos o CA inexistente fallan cerrados al arrancar."""
    import pytest
    from django.core.exceptions import ImproperlyConfigured

    _mariadb_env(monkeypatch)
    monkeypatch.setenv("DB_SSL_MODE", "preferred")
    with pytest.raises(ImproperlyConfigured):
        database(tmp_path)
    monkeypatch.setenv("DB_SSL_MODE", "verify_identity")
    monkeypatch.setenv("DB_SSL_CA", str(tmp_path / "missing.pem"))
    with pytest.raises(ImproperlyConfigured):
        database(tmp_path)


def test_pymysql_treats_required_options_as_mandatory_tls(monkeypatch, tmp_path: Path):
    """Contrato con el driver real: required exige TLS; el legado no."""
    import pymysql

    _mariadb_env(monkeypatch)
    monkeypatch.setenv("DB_SSL_MODE", "required")
    required = database(tmp_path)["OPTIONS"]["ssl"]
    connection = pymysql.connections.Connection(defer_connect=True, ssl=required)
    assert connection._ssl_required is True

    legacy = pymysql.connections.Connection(defer_connect=True, ssl={})
    assert legacy._ssl_required is False
