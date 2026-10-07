"""Crea las tablas iniciales de autenticación.

@author Cristian Deysdayr Jimenez
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Define el esquema inicial."""

    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="User",
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
                ("first_name", models.CharField(max_length=40)),
                ("last_name", models.CharField(max_length=40)),
                ("identification", models.CharField(max_length=15, unique=True)),
                ("phone", models.CharField(blank=True, max_length=15)),
                ("correo", models.EmailField(blank=True, max_length=254)),
                ("password_hash", models.CharField(max_length=255)),
            ],
            options={"db_table": "users"},
        ),
        migrations.CreateModel(
            name="LoginCode",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="accounts.user",
                    ),
                ),
                ("code_hash", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("channel", models.CharField(max_length=10)),
                ("sent_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "login_codes"},
        ),
        migrations.CreateModel(
            name="Session",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="accounts.user",
                    ),
                ),
                ("last_seen", models.DateTimeField()),
                ("token", models.TextField()),
            ],
            options={"db_table": "sessions"},
        ),
    ]
