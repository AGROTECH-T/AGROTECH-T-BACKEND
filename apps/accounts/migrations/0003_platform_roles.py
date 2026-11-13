"""Agrega los roles de plataforma superadmin y administrador.

Propósito: distinguir la cuenta sembrada que nombra administradores de las
cuentas que todavía esperan permiso.
Contexto: el registro no concede módulos; solo el superadmin nombra al administrador.
@author Cristian Deysdayr Jimenez
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Suma las banderas de plataforma en la cuenta."""

    dependencies = [("accounts", "0002_harden_sessions")]
    operations = [
        migrations.AddField(
            model_name="user",
            name="is_superadmin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="is_administrador",
            field=models.BooleanField(default=False),
        ),
    ]
