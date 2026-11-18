"""Composite foreign keys that bind IoT persistence rows to one farm/device."""

from django.db import migrations

from apps.iot.migrations._composite_fk import (
    ForeignKeySpec,
    add_foreign_keys,
    drop_foreign_keys,
)

PERSISTENCE_FKS = (
    ForeignKeySpec(
        "iot_rf_coord_cred_fk",
        "iot_received_frames",
        ("coordinator_id", "credential_id"),
        "iot_coordinator_credentials",
        ("coordinator_id", "id"),
    ),
    ForeignKeySpec(
        "iot_rf_auth_fk",
        "iot_received_frames",
        ("authorization_id", "coordinator_id", "gateway_id", "farm_id"),
        "iot_coordinator_gateways",
        ("id", "coordinator_id", "gateway_id", "farm_id"),
    ),
    ForeignKeySpec(
        "iot_meas_frame_fk",
        "iot_measurements",
        ("frame_id", "coordinator_id", "farm_id", "measured_at"),
        "iot_received_frames",
        ("id", "coordinator_id", "farm_id", "measured_at"),
    ),
    ForeignKeySpec(
        "iot_meas_sensor_fk",
        "iot_measurements",
        ("sensor_id", "coordinator_id", "channel", "metric"),
        "iot_sensors",
        ("id", "coordinator_id", "channel", "metric"),
    ),
    ForeignKeySpec(
        "iot_fc_frame_farm_fk",
        "iot_frame_conflicts",
        ("frame_id", "farm_id"),
        "iot_received_frames",
        ("id", "farm_id"),
    ),
    ForeignKeySpec(
        "iot_fc_gateway_farm_fk",
        "iot_frame_conflicts",
        ("gateway_id", "farm_id"),
        "iot_gateways",
        ("id", "farm_id"),
    ),
)


def add_persistence_fks(apps, schema_editor):
    add_foreign_keys(apps, schema_editor, PERSISTENCE_FKS, migration="iot.0008")


def drop_persistence_fks(apps, schema_editor):
    drop_foreign_keys(apps, schema_editor, PERSISTENCE_FKS, migration="iot.0008")


class Migration(migrations.Migration):
    # MariaDB DDL autocommits; helper supports safe retry after partial progress.
    atomic = False
    dependencies = [("iot", "0007_iot_frame_measurement_conflict")]
    operations = [migrations.RunPython(add_persistence_fks, drop_persistence_fks, atomic=False)]
