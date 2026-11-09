"""Añade la baja lógica y la trazabilidad de desactivación de fincas.

Propósito: conservar fincas y relaciones históricas al desactivarlas.
Contexto: migración aditiva y compatible con registros existentes.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Incorpora estado activo y responsable de la desactivación."""

    dependencies = [
        ("farms", "0001_initial"),
        ("accounts", "0002_harden_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="farm",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="farm",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="farm",
            name="deactivated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deactivated_farms",
                to="accounts.user",
            ),
        ),
    ]
