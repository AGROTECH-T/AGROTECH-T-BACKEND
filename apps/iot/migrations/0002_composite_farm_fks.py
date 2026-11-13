# Escrita a mano (BUILD IoT-02.2, idempotente desde IoT-02.2-R2; DEC-13 §11).
"""FK compuestas que impiden relacionar dispositivos de fincas distintas.

Django 5.2 no modela claves foráneas compuestas. Esta migración las crea con
SQL sobre las columnas que ya existen:

- ``iot_sensors (coordinator_id, farm_id)`` → ``iot_coordinators (id, farm_id)``
- ``iot_coordinator_gateways (coordinator_id, farm_id)`` → ``iot_coordinators (id, farm_id)``
- ``iot_coordinator_gateways (gateway_id, farm_id)`` → ``iot_gateways (id, farm_id)``

Requisitos que deja ``0001_initial``: ``UNIQUE (id, farm_id)`` en las tablas
referenciadas (``iot_coord_id_farm_uniq``, ``iot_gateway_id_farm_uniq``) e
índices ``(x_id, farm_id)`` en las tablas que referencian, que InnoDB reutiliza
para la FK (``apps/iot/tests/test_model_support.py`` vigila que existan).
``ON UPDATE RESTRICT`` impide cambiar la finca de un dispositivo referenciado;
``ON DELETE RESTRICT`` coincide con el PROTECT del ORM.

Motor: el SQL es estándar y se ejecuta en MariaDB (motor aprobado para IoT) y
en PostgreSQL, para no romper ``migrate`` del resto del backend si se despliega
allí (las operaciones IoT siguen bloqueadas por ``require_mariadb``). En SQLite
no se ejecuta: no admite ``ALTER TABLE ... ADD CONSTRAINT``.

Idempotencia (``atomic=False``: MariaDB no revierte DDL). Cada FK se busca en
``information_schema`` limitado al esquema actual, a su tabla y a su nombre:

- Ausente: al aplicar se crea; al revertir se omite.
- Presente con la definición esperada (columnas, tabla y columnas
  referenciadas): al aplicar se omite; al revertir se elimina.
- Presente con otra definición: se detiene con ``RuntimeError`` sin crear ni
  borrar nada, porque esa restricción no pertenece a esta migración.

Una restricción homónima en otra tabla no se confunde con la propia: la
búsqueda filtra por tabla. En MariaDB los nombres de FK son únicos por esquema,
así que crear la propia fallaría por nombre duplicado y la ajena no se toca.

Recuperación tras una ejecución parcial: si una ejecución anterior falló
después de crear solo algunas FK, la migración no quedó registrada en
``django_migrations``. Basta con volver a ejecutar ``migrate``: las FK ya
creadas se reconocen y solo se crean las que faltan. Una migración no
registrada no puede revertirse con ``migrate iot 0001``; si se desea deshacer
el estado parcial, primero se completa con ``migrate`` y después se revierte.
Si se detiene por una definición distinta, hay que inspeccionar a mano esa
restricción antes de reintentar.
"""
from django.db import migrations

COMPOSITE_FKS = (
    ("iot_sensor_coord_farm_fk", "iot_sensors", "coordinator_id", "iot_coordinators"),
    ("iot_cg_coord_farm_fk", "iot_coordinator_gateways", "coordinator_id", "iot_coordinators"),
    ("iot_cg_gw_farm_fk", "iot_coordinator_gateways", "gateway_id", "iot_gateways"),
)
SUPPORTED_VENDORS = frozenset({"mysql", "postgresql"})
# Consultas fijas por motor, limitadas al esquema actual, a la tabla y al nombre.
FK_EXISTS_SQL = {
    "mysql": (
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND CONSTRAINT_NAME = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'"
    ),
    "postgresql": (
        "SELECT COUNT(*) FROM information_schema.table_constraints "
        "WHERE constraint_schema = current_schema() AND table_name = %s "
        "AND constraint_name = %s AND constraint_type = 'FOREIGN KEY'"
    ),
}
FK_COLUMNS_SQL = {
    "mysql": (
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND CONSTRAINT_NAME = %s ORDER BY ORDINAL_POSITION"
    ),
    "postgresql": (
        "SELECT column_name FROM information_schema.key_column_usage "
        "WHERE constraint_schema = current_schema() AND table_name = %s "
        "AND constraint_name = %s ORDER BY ordinal_position"
    ),
}


def expected_definition(vendor: str, column: str, target: str) -> list:
    """Definición esperada de la FK, en el formato de ``fk_definition``."""
    if vendor == "mysql":
        return [(column, target, "id"), ("farm_id", target, "farm_id")]
    return [(column,), ("farm_id",)]


def fk_definition(connection, table: str, name: str):
    """Columnas de la FK ``name`` de ``table`` en el esquema actual, o ``None``.

    En MariaDB/MySQL incluye la tabla y la columna referenciadas de cada
    columna; en PostgreSQL, solo las columnas propias.
    """
    vendor = connection.vendor
    with connection.cursor() as cursor:
        cursor.execute(FK_EXISTS_SQL[vendor], [table, name])
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(FK_COLUMNS_SQL[vendor], [table, name])
        return [tuple(row) for row in cursor.fetchall()]


def _state(connection, name, table, column, target) -> str:
    """``absent``, ``ours`` o ``foreign`` para la FK indicada."""
    found = fk_definition(connection, table, name)
    if found is None:
        return "absent"
    if found == expected_definition(connection.vendor, column, target):
        return "ours"
    return "foreign"


def _refuse(name: str, table: str):
    raise RuntimeError(
        f"La restricción {name} de {table} existe con una definición distinta; "
        "no pertenece a iot.0002. Revísela manualmente antes de reintentar."
    )


def add_composite_fks(apps, schema_editor):
    """Crea las FK compuestas que falten, en orden."""
    connection = schema_editor.connection
    if connection.vendor not in SUPPORTED_VENDORS:
        return
    quote = schema_editor.quote_name
    for name, table, column, target in COMPOSITE_FKS:
        state = _state(connection, name, table, column, target)
        if state == "ours":
            continue
        if state == "foreign":
            _refuse(name, table)
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(name)} "
            f"FOREIGN KEY ({quote(column)}, {quote('farm_id')}) "
            f"REFERENCES {quote(target)} ({quote('id')}, {quote('farm_id')}) "
            "ON UPDATE RESTRICT ON DELETE RESTRICT"
        )


def drop_composite_fks(apps, schema_editor):
    """Elimina en orden inverso solo las FK propias que existan."""
    connection = schema_editor.connection
    vendor = connection.vendor
    if vendor not in SUPPORTED_VENDORS:
        return
    quote = schema_editor.quote_name
    drop = "DROP FOREIGN KEY" if vendor == "mysql" else "DROP CONSTRAINT"
    for name, table, column, target in reversed(COMPOSITE_FKS):
        state = _state(connection, name, table, column, target)
        if state == "absent":
            continue
        if state == "foreign":
            _refuse(name, table)
        schema_editor.execute(f"ALTER TABLE {quote(table)} {drop} {quote(name)}")


class Migration(migrations.Migration):

    dependencies = [
        ('iot', '0001_initial'),
    ]

    operations = [
        # atomic=False: MariaDB no admite DDL transaccional; Django prohíbe
        # ejecutar DDL dentro de una transacción en esos motores.
        migrations.RunPython(add_composite_fks, drop_composite_fks, atomic=False, elidable=False),
    ]
