"""Backend de MariaDB usado por Django.

Propósito: repetir la conexión cuando el servidor corta el enlace al despertar.
Contexto: ENGINE config.mysql.
@author Cristian Deysdayr Jimenez
"""

from django.db.backends.mysql.base import DatabaseWrapper as MysqlDatabaseWrapper

from config.mysql_retry import open_mariadb


class DatabaseWrapper(MysqlDatabaseWrapper):
    """Misma conexión de Django, con reintento ante un corte de MariaDB."""

    def get_new_connection(self, conn_params):
        """@returns conexión nueva, reintentada si el servidor la corta."""
        parent = super()
        return open_mariadb(lambda: parent.get_new_connection(conn_params))
