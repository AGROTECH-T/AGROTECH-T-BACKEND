"""Agrega la huella del código de invitación (F10.3.1).

Propósito: añadir ``farm_invitations.code_hash`` (VARCHAR(64) NULL UNIQUE).
Las invitaciones existentes quedan con NULL y no se tocan sus datos ni
``token_hash``. Reversible: deshacerla elimina solo la columna y su índice.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Añade FarmInvitation.code_hash."""

    dependencies = [
        ("farms", "0004_farminvitation"),
    ]

    operations = [
        migrations.AddField(
            model_name="farminvitation",
            name="code_hash",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
