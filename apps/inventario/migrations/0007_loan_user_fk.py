"""Alinea el préstamo con la tabla de usuarios."""

from django.db import migrations


def point_at_users(apps, schema_editor):
    """Pasa created_by a la tabla users si todavía apunta a otra."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'inventory_product_loans'
              AND COLUMN_NAME = 'created_by_id'
            """
        )
        column = cursor.fetchone()
        if column is None:
            return
        if not str(column[0]).startswith("bigint"):
            cursor.execute(
                "ALTER TABLE inventory_product_loans MODIFY created_by_id bigint NOT NULL"
            )
        cursor.execute(
            """
            SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'inventory_product_loans'
              AND COLUMN_NAME = 'created_by_id'
              AND REFERENCED_TABLE_NAME IS NOT NULL
            """
        )
        for name, referenced in cursor.fetchall():
            if referenced != "users":
                cursor.execute(
                    f"ALTER TABLE inventory_product_loans DROP FOREIGN KEY `{name}`"
                )
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'inventory_product_loans'
              AND COLUMN_NAME = 'created_by_id'
              AND REFERENCED_TABLE_NAME = 'users'
            """
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                """
                ALTER TABLE inventory_product_loans
                ADD CONSTRAINT inventory_product_loans_created_by_id_fk_users
                FOREIGN KEY (created_by_id) REFERENCES users (id)
                """
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("inventario", "0006_product_loan"),
    ]

    operations = [
        migrations.RunPython(point_at_users, migrations.RunPython.noop),
    ]
