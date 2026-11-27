"""Reintento cuando MariaDB corta la conexión.

Propósito: no tratar el despertar del servidor como un fallo definitivo.
Contexto: el primer enlace tras el reposo responde con el código 2013.
@author Cristian Deysdayr Jimenez
"""

import pymysql
import pytest

from config.mysql_retry import open_mariadb


def test_retries_a_lost_connection(monkeypatch):
    """Un corte transitorio se vuelve a intentar hasta abrir."""
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        if calls["n"] < 3:
            raise pymysql.err.OperationalError(2013, "Lost connection")
        return "abierta"

    monkeypatch.setattr("config.mysql_retry.time.sleep", lambda _seconds: None)
    assert open_mariadb(connect) == "abierta"
    assert calls["n"] == 3


def test_access_denied_is_not_retried():
    """Una clave rechazada no espera los reintentos del despertar."""

    def connect():
        raise pymysql.err.OperationalError(1045, "Access denied")

    with pytest.raises(pymysql.err.OperationalError):
        open_mariadb(connect)
