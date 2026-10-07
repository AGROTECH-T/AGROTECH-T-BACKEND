"""Endurece cuentas y reemplaza tokens persistidos por huellas.

@author Cristian Deysdayr Jimenez
"""

from django.db import migrations, models


def clear_sessions(apps, schema_editor) -> None:
    """Invalida sesiones antiguas que almacenaban el token completo."""
    apps.get_model("accounts", "Session").objects.all().delete()


class Migration(migrations.Migration):
    """Actualiza el esquema de seguridad de autenticación."""

    dependencies = [("accounts", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="user",
            name="failed_login_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="user",
            name="locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(clear_sessions, migrations.RunPython.noop),
        migrations.RemoveField(model_name="session", name="token"),
        migrations.AddField(
            model_name="session",
            name="token_hash",
            field=models.CharField(max_length=64, unique=True),
        ),
    ]
