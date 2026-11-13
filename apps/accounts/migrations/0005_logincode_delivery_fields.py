"""Añade los campos de reserva de entrega a LoginCode.

Propósito: soportar el envío de OTP de verificación con reserva y confirmación
condicionada a un nonce, sin exponer el resultado tardío de un proveedor.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Incorpora la reserva de entrega al código de acceso."""

    dependencies = [
        ("accounts", "0004_session_closed_at_session_created_at_session_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="logincode",
            name="delivery_pending",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="logincode",
            name="delivery_reserved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="logincode",
            name="delivery_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="logincode",
            name="delivery_nonce",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
