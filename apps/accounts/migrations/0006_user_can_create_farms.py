"""Añade la capacidad global de crear fincas a la cuenta.

Propósito: separar el permiso de crear fincas del rol de membresía (DEC-11).
Contexto: todas las cuentas existentes quedan con False; la concesión es
explícita mediante ``manage.py grant_farm_creation``. Reversible: revertir
elimina la columna.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Incorpora ``can_create_farms`` con un valor por defecto seguro."""

    dependencies = [
        ("accounts", "0005_logincode_delivery_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="can_create_farms",
            field=models.BooleanField(default=False),
        ),
    ]
