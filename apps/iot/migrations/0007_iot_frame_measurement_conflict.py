"""Persistence tables for accepted frames, readings, and identity conflicts."""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q, Value
from django.db.models.functions import Length
from django.db.models.lookups import Exact, GreaterThanOrEqual


class Migration(migrations.Migration):
    dependencies = [("farms", "0001_initial"), ("iot", "0006_iot_persistence_parent_uniques")]

    operations = [
        migrations.CreateModel(
            name="ReceivedFrame",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("frame_version", models.PositiveSmallIntegerField()),
                ("key_version", models.PositiveIntegerField()),
                ("boot_counter", models.PositiveBigIntegerField()),
                ("seq", models.PositiveBigIntegerField()),
                ("reading_count", models.PositiveSmallIntegerField()),
                ("raw_frame", models.BinaryField(editable=False)),
                ("gateway_received_at", models.DateTimeField(blank=True, null=True)),
                ("gateway_clock_synced", models.BooleanField()),
                ("buffered", models.BooleanField()),
                ("rssi", models.SmallIntegerField(blank=True, null=True)),
                ("snr", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("server_received_at", models.DateTimeField()),
                ("measured_at", models.DateTimeField()),
                ("measured_at_approximate", models.BooleanField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("authorization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="received_frames", to="iot.coordinatorgateway")),
                ("coordinator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="received_frames", to="iot.coordinator")),
                ("credential", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="received_frames", to="iot.coordinatorcredential")),
                ("farm", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="iot_received_frames", to="farms.farm")),
                ("gateway", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="received_frames", to="iot.gateway")),
            ],
            options={
                "db_table": "iot_received_frames",
                "ordering": ["-measured_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(fields=("coordinator", "credential", "boot_counter", "seq"), name="iot_rframe_identity_uniq"),
                    models.UniqueConstraint(fields=("id", "coordinator", "farm", "measured_at"), name="iot_rframe_id_coord_farm_at_uniq"),
                    models.UniqueConstraint(fields=("id", "farm"), name="iot_rframe_id_farm_uniq"),
                    models.CheckConstraint(condition=Q(frame_version=1), name="iot_rframe_version_valid"),
                    models.CheckConstraint(condition=Q(key_version__gte=1, key_version__lte=65535), name="iot_rframe_key_version_valid"),
                    models.CheckConstraint(condition=Q(boot_counter__gte=0, boot_counter__lte=4294967295), name="iot_rframe_boot_counter_valid"),
                    models.CheckConstraint(condition=Q(seq__gte=0, seq__lte=4294967295), name="iot_rframe_seq_valid"),
                    models.CheckConstraint(condition=Q(reading_count__gte=1), name="iot_rframe_reading_count_valid"),
                    models.CheckConstraint(
                        condition=~Q(frame_version=1) | Exact(Length("raw_frame"), F("reading_count") * 5 + 57),
                        name="iot_rframe_v1_length_valid",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["authorization", "coordinator", "gateway", "farm"], name="iot_rframe_auth_fk_idx"),
                    models.Index(fields=["farm", "created_at"], name="iot_rframe_farm_created_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Measurement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.PositiveSmallIntegerField()),
                ("metric", models.CharField(choices=[("temperature", "Temperatura"), ("relative_humidity", "Humedad relativa")], max_length=40)),
                ("raw_value", models.IntegerField()),
                ("value", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("is_invalid_marker", models.BooleanField()),
                ("measured_at", models.DateTimeField()),
                ("coordinator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="measurements", to="iot.coordinator")),
                ("farm", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="iot_measurements", to="farms.farm")),
                ("frame", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="measurements", to="iot.receivedframe")),
                ("sensor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="measurements", to="iot.sensor")),
            ],
            options={
                "db_table": "iot_measurements",
                "ordering": ["-measured_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(fields=("frame", "channel"), name="iot_meas_frame_channel_uniq"),
                    models.CheckConstraint(condition=Q(channel__gte=1), name="iot_meas_channel_valid"),
                    models.CheckConstraint(condition=Q(metric__in=["temperature", "relative_humidity"]), name="iot_meas_metric_valid"),
                    models.CheckConstraint(
                        condition=(Q(metric="temperature", raw_value__gte=-32768, raw_value__lte=32767)
                                   | Q(metric="relative_humidity", raw_value__gte=0, raw_value__lte=10000)
                                   | Q(metric="relative_humidity", raw_value=65535)),
                        name="iot_meas_raw_value_type",
                    ),
                    models.CheckConstraint(
                        condition=(Q(Q(metric="temperature", raw_value=-32768)
                                     | Q(metric="relative_humidity", raw_value=65535), is_invalid_marker=True)
                                   | Q(~(Q(metric="temperature", raw_value=-32768)
                                        | Q(metric="relative_humidity", raw_value=65535)), is_invalid_marker=False)),
                        name="iot_meas_marker_valid",
                    ),
                    models.CheckConstraint(
                        condition=(Q(is_invalid_marker=True, value__isnull=True)
                                   | Q(is_invalid_marker=False, value__isnull=False, value=F("raw_value") / Value(100))),
                        name="iot_meas_value_valid",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["sensor", "measured_at"], name="iot_meas_sensor_time_idx"),
                    models.Index(fields=["farm", "measured_at"], name="iot_meas_farm_time_idx"),
                    models.Index(fields=["frame", "coordinator", "farm", "measured_at"], name="iot_meas_frame_fk_idx"),
                    models.Index(fields=["sensor", "coordinator", "channel", "metric"], name="iot_meas_sensor_fk_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="FrameConflict",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("conflicting_raw_frame", models.BinaryField(editable=False)),
                ("conflicting_sha256", models.CharField(max_length=64)),
                ("server_received_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("farm", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="iot_frame_conflicts", to="farms.farm")),
                ("frame", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="conflicts", to="iot.receivedframe")),
                ("gateway", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="frame_conflicts", to="iot.gateway")),
            ],
            options={
                "db_table": "iot_frame_conflicts",
                "ordering": ["-created_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(fields=("frame", "conflicting_sha256"), name="iot_fconf_frame_sha_uniq"),
                    models.CheckConstraint(condition=Q(conflicting_sha256__regex="^[0-9a-f]{64}$"), name="iot_fconf_sha256_format"),
                    models.CheckConstraint(condition=GreaterThanOrEqual(Length("conflicting_raw_frame"), 1), name="iot_fconf_raw_not_empty"),
                ],
                "indexes": [
                    models.Index(fields=["frame", "farm"], name="iot_fconf_frame_farm_idx"),
                    models.Index(fields=["gateway", "farm"], name="iot_fconf_gw_farm_idx"),
                ],
            },
        ),
    ]
