"""Asegura created_at en sesiones para que el login pueda crear la fila.

Propósito: MariaDB en modo estricto rechaza el INSERT si created_at no tiene valor.
Contexto: la tabla local ya podía traer esa columna; los tests la necesitan nueva.
@author Cristian Deysdayr Jimenez
"""

from django.db import migrations, models


def ensure_created_at(apps, schema_editor) -> None:
    """Crea la columna si falta; en MariaDB le pone valor por defecto."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        names = {
            column.name
            for column in connection.introspection.get_table_description(cursor, "sessions")
        }
    if "created_at" in names:
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE sessions MODIFY created_at datetime(6) "
                    "NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
                )
        return
    field = models.DateTimeField(auto_now_add=True)
    field.set_attributes_from_name("created_at")
    schema_editor.add_field(apps.get_model("accounts", "Session"), field)


class Migration(migrations.Migration):
    """Deja created_at en el modelo y en la tabla de sesiones."""

    dependencies = [("accounts", "0003_platform_roles")]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="session",
                    name="created_at",
                    field=models.DateTimeField(auto_now_add=True),
                ),
            ],
            database_operations=[
                migrations.RunPython(ensure_created_at, migrations.RunPython.noop),
            ],
        ),
    ]
