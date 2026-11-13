"""Exigencia de MariaDB para las operaciones IoT (IoT-02.1, DEC-13).

Propósito: el módulo IoT está aprobado solo sobre MariaDB (restricciones e
índices que se definirán en IoT-02.2 y siguientes). ``require_mariadb`` falla de
forma cerrada si la conexión no es MariaDB.

Contexto: no modifica ``config/database.py`` ni crea routers o aliases. No se
ejecuta al importar ni al arrancar Django, así que el resto del backend puede
seguir arrancando sobre otro motor (por ejemplo, Postgres con
``DATABASE_URL``). Solo la invocarán los servicios IoT de escritura y el futuro
consumidor MQTT.

Precedente: ``apps.accounts.services.auth_service`` consulta
``connection.vendor`` para omitir un bloqueo fuera de MariaDB/MySQL. Aquí el
criterio es el contrario: fuera de MariaDB no se continúa. Se lanza
``ImproperlyConfigured`` (como ``apps.farms.invitation_codes`` cuando falta su
clave) porque es un error de configuración del despliegue, no del cliente.
"""
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections

MARIADB_REQUIRED_MESSAGE = "el módulo IoT requiere MariaDB como base de datos"


def require_mariadb(using: str = DEFAULT_DB_ALIAS) -> None:
    """Exige que la conexión ``using`` sea MariaDB.

    Comprueba ``vendor == "mysql"`` y, solo entonces, ``mysql_is_mariadb``
    (consultarlo puede abrir la conexión para leer la versión del servidor).
    No modifica la conexión ni la configuración.
    """
    connection = connections[using]
    if connection.vendor != "mysql":
        raise ImproperlyConfigured(MARIADB_REQUIRED_MESSAGE)
    if getattr(connection, "mysql_is_mariadb", False) is not True:
        raise ImproperlyConfigured(MARIADB_REQUIRED_MESSAGE)
