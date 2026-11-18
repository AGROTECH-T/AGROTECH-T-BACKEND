"""Add physical sensor sources without inferring legacy provenance."""

import uuid

import django.db.models.deletion
from django.db import migrations, models

from apps.iot.migrations._composite_fk import (
    ForeignKeySpec,
    add_foreign_keys,
    drop_foreign_keys,
)


SOURCE_FKS = (
    ForeignKeySpec(
        "iot_ss_coord_farm_fk",
        "iot_sensor_sources",
        ("coordinator_id", "farm_id"),
        "iot_coordinators",
        ("id", "farm_id"),
    ),
    ForeignKeySpec(
        "iot_sensor_source_coord_farm_fk",
        "iot_sensors",
        ("source_id", "coordinator_id", "farm_id"),
        "iot_sensor_sources",
        ("id", "coordinator_id", "farm_id"),
    ),
)


def add_source_fks(apps, schema_editor):
    add_foreign_keys(apps, schema_editor, SOURCE_FKS, migration="iot.0010")


def drop_source_fks(apps, schema_editor):
    drop_foreign_keys(apps, schema_editor, SOURCE_FKS, migration="iot.0010")


class Migration(migrations.Migration):
    # MariaDB auto-commits DDL; the FK helper safely resumes partial progress.
    atomic = False

    dependencies = [("iot", "0009_coordinator_key_generation")]

    operations = [
        migrations.CreateModel(
            name="SensorSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deactivated_at", models.DateTimeField(blank=True, null=True)),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("model", models.CharField(blank=True, max_length=80, null=True)),
                ("manufacturer", models.CharField(blank=True, max_length=100, null=True)),
                ("role", models.CharField(blank=True, choices=[("primary", "Principal"), ("backup_comparison", "Respaldo y comparación")], max_length=32, null=True)),
                ("description", models.TextField(blank=True, default="")),
                ("coordinator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sensor_sources", to="iot.coordinator")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("deactivated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("farm", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="iot_sensor_sources", to="farms.farm")),
            ],
            options={"db_table": "iot_sensor_sources", "ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="SensorSourceChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("before_data", models.JSONField()),
                ("after_data", models.JSONField()),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="changes", to="iot.sensorsource")),
            ],
            options={"db_table": "iot_sensor_source_changes", "ordering": ["-changed_at", "-id"]},
        ),
        migrations.AddField(
            model_name="sensor",
            name="source",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sensors", to="iot.sensorsource"),
        ),
        migrations.AddIndex(
            model_name="sensorsource",
            index=models.Index(fields=["coordinator", "farm"], name="iot_ss_coord_farm_idx"),
        ),
        migrations.AddIndex(
            model_name="sensorsource",
            index=models.Index(fields=["farm", "is_active"], name="iot_ss_farm_active_idx"),
        ),
        migrations.AddIndex(
            model_name="sensor",
            index=models.Index(fields=["source", "coordinator", "farm"], name="iot_sn_source_fk_idx"),
        ),
        migrations.AddIndex(
            model_name="sensorsourcechange",
            index=models.Index(fields=["source", "changed_at"], name="iot_ss_change_time_idx"),
        ),
        migrations.AddConstraint(
            model_name="sensorsource",
            constraint=models.UniqueConstraint(fields=("id", "coordinator", "farm"), name="iot_ss_id_coord_farm_uniq"),
        ),
        migrations.AddConstraint(
            model_name="sensorsource",
            constraint=models.CheckConstraint(condition=models.Q(("role__isnull", True), models.Q(("role__in", ["primary", "backup_comparison"])), _connector="OR"), name="iot_ss_role_valid"),
        ),
        migrations.AddConstraint(
            model_name="sensorsource",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("deactivated_at__isnull", True), ("deactivated_by__isnull", True), ("is_active", True)), models.Q(("deactivated_at__isnull", False), ("is_active", False)), _connector="OR"), name="iot_ss_lifecycle_valid"),
        ),
        migrations.RunPython(add_source_fks, drop_source_fks, atomic=False),
    ]
