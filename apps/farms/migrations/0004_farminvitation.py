"""Crea el modelo de invitaciones a fincas.

Propósito: persistir invitaciones de un solo uso con token hasheado, estados
aprobados, reserva de entrega e índices para las operaciones de FARMS-108.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Define el esquema de FarmInvitation."""

    dependencies = [
        ("farms", "0003_farmmembership_left_at_farmmembership_revoked_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="FarmInvitation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("email", models.EmailField(max_length=254)),
                ("pending_email", models.EmailField(blank=True, max_length=254, null=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("administrador", "Administrador"),
                            ("operador", "Operador"),
                            ("consulta", "Consulta"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendiente"),
                            ("accepted", "Aceptada"),
                            ("cancelled", "Cancelada"),
                            ("expired", "Expirada"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("token_hash", models.CharField(blank=True, default="", max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("delivery_pending", models.BooleanField(default=False)),
                ("delivery_reserved_at", models.DateTimeField(blank=True, null=True)),
                ("delivery_nonce", models.UUIDField(blank=True, null=True)),
                ("delivery_confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "farm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="farms.farm",
                    ),
                ),
                (
                    "invited_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_invitations",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "farm_invitations",
                "ordering": ["-created_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("farm", "pending_email"),
                        name="uniq_farm_invitation_pending_email",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("role__in", ["administrador", "operador", "consulta"])
                        ),
                        name="farms_invitation_role_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                ["pending", "accepted", "cancelled", "expired"],
                            )
                        ),
                        name="farms_invitation_status_valid",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["token_hash"], name="farms_invitation_token_idx"
                    ),
                    models.Index(
                        fields=["status", "expires_at"],
                        name="farms_inv_stat_exp_idx",
                    ),
                    models.Index(
                        fields=["farm", "status"],
                        name="farms_inv_farm_stat_idx",
                    ),
                ],
            },
        ),
    ]
