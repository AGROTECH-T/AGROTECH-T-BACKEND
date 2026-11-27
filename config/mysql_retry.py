"""Reintento de conexión a MariaDB.

Propósito: Layerbase reinicia el enlace al despertar y PyMySQL lo ve como corte.
Contexto: el primer uso tras el reposo no debe responder 500.
@author Cristian Deysdayr Jimenez
"""

import time

import pymysql

LOST_CONNECTION = {2006, 2013}
ATTEMPTS = 4
WAIT_SECONDS = (6, 7, 8)


def open_mariadb(connect):
    """Abre la conexión y repite solo si el servidor cortó el enlace.

    @param connect: función sin argumentos que devuelve la conexión.
    @returns La conexión abierta.
    @raises pymysql.err.OperationalError: otro fallo, o el corte tras los intentos.
    """
    for attempt in range(ATTEMPTS):
        try:
            return connect()
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            if code not in LOST_CONNECTION or attempt == ATTEMPTS - 1:
                raise
            time.sleep(WAIT_SECONDS[attempt])
    return None
