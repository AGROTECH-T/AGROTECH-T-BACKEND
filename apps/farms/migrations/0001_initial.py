"""Crea las tablas iniciales del dominio de fincas.

Propósito: persistir fincas y membresías con su rol e integridad básica.
Contexto: depende de la migración vigente del modelo de cuentas.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Define el esquema inicial de fincas y membresías."""

    initial = True

    dependencies = [
        ("accounts", "0002_harden_sessions"),
    ]

    operations = [
        migrations.CreateModel(
            name="Farm",
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
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "farms",
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="FarmMembership",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "farm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="farms.farm",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="farm_memberships",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "farm_memberships",
                "ordering": ["farm_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("farm", "user"),
                        name="uniq_farm_membership_user",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("role__in", ["administrador", "operador", "consulta"])
                        ),
                        name="farms_membership_role_valid",
                    ),
                ],
            },
        ),
    ]
