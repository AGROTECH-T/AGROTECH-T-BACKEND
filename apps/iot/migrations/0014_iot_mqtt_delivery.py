"""Add durable MQTT delivery and event tables."""

import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Length
from django.db.models.lookups import Exact

import apps.iot.models


class Migration(migrations.Migration):
    dependencies = [
        ("iot", "0013_iot_ingestion_history_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="IotMqttDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("transport_key", apps.iot.models.BoundedBinaryField(max_length=32, unique=True)),
                ("topic", models.CharField(max_length=512)),
                ("qos", models.PositiveSmallIntegerField(default=1)),
                ("received_at", models.DateTimeField()),
                ("payload_ciphertext", models.BinaryField()),
                ("payload_nonce", apps.iot.models.BoundedBinaryField(max_length=12)),
                ("payload_tag", apps.iot.models.BoundedBinaryField(max_length=16)),
                ("payload_key_version", models.CharField(max_length=64)),
                ("payload_sha256", apps.iot.models.BoundedBinaryField(max_length=32)),
                ("payload_size", models.PositiveIntegerField()),
                ("state", models.CharField(choices=[("RECEIVED", "Received"), ("PROCESSING", "Processing"), ("RETRY_PENDING", "Retry pending"), ("ACCEPTED", "Accepted"), ("DUPLICATE", "Duplicate"), ("REJECTED", "Rejected"), ("CONFLICT", "Conflict"), ("DEAD_LETTER", "Dead letter"), ("RESOLVED", "Resolved")], default="RECEIVED", max_length=20)),
                ("result_category", models.CharField(blank=True, max_length=32, null=True)),
                ("result_code", models.CharField(blank=True, max_length=64, null=True)),
                ("reason_code", models.CharField(blank=True, max_length=64, null=True)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("lease_owner", models.CharField(blank=True, max_length=100, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, max_length=64, null=True)),
                ("last_error_safe", models.CharField(blank=True, max_length=1000, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_code", models.CharField(blank=True, max_length=64, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("coordinator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mqtt_deliveries", to="iot.coordinator")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
            ],
            options={
                "db_table": "iot_mqtt_deliveries",
                "ordering": ["received_at", "id"],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("qos", 1)), name="iot_mqtt_delivery_qos_one"),
                    models.CheckConstraint(condition=Exact(Length("transport_key"), 32), name="iot_mqtt_tkey_len_ck"),
                    models.CheckConstraint(condition=Exact(Length("payload_nonce"), 12), name="iot_mqtt_nonce_len_ck"),
                    models.CheckConstraint(condition=Exact(Length("payload_tag"), 16), name="iot_mqtt_tag_len_ck"),
                    models.CheckConstraint(condition=Exact(Length("payload_sha256"), 32), name="iot_mqtt_sha_len_ck"),
                ],
                "indexes": [
                    models.Index(fields=["state", "next_attempt_at", "received_at"], name="iot_mqtt_state_next_idx"),
                    models.Index(fields=["lease_expires_at"], name="iot_mqtt_lease_exp_idx"),
                    models.Index(fields=["coordinator", "received_at", "id"], name="iot_mqtt_coord_rx_idx"),
                    models.Index(fields=["resolved_at"], name="iot_mqtt_resolved_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IotMqttDeliveryEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("event_type", models.CharField(max_length=40)),
                ("from_state", models.CharField(blank=True, choices=[("RECEIVED", "Received"), ("PROCESSING", "Processing"), ("RETRY_PENDING", "Retry pending"), ("ACCEPTED", "Accepted"), ("DUPLICATE", "Duplicate"), ("REJECTED", "Rejected"), ("CONFLICT", "Conflict"), ("DEAD_LETTER", "Dead letter"), ("RESOLVED", "Resolved")], max_length=20, null=True)),
                ("to_state", models.CharField(blank=True, choices=[("RECEIVED", "Received"), ("PROCESSING", "Processing"), ("RETRY_PENDING", "Retry pending"), ("ACCEPTED", "Accepted"), ("DUPLICATE", "Duplicate"), ("REJECTED", "Rejected"), ("CONFLICT", "Conflict"), ("DEAD_LETTER", "Dead letter"), ("RESOLVED", "Resolved")], max_length=20, null=True)),
                ("attempt_number", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("worker_id", models.CharField(blank=True, max_length=100, null=True)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("result_code", models.CharField(blank=True, max_length=64, null=True)),
                ("safe_detail", models.CharField(blank=True, max_length=1000, null=True)),
                ("correlation_id", models.UUIDField(blank=True, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("delivery", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="iot.iotmqttdelivery")),
            ],
            options={
                "db_table": "iot_mqtt_delivery_events",
                "ordering": ["occurred_at", "id"],
                "default_manager_name": "objects",
                "base_manager_name": "objects",
                "indexes": [
                    models.Index(fields=["delivery", "occurred_at"], name="iot_mqtt_event_time_idx"),
                ],
            },
        ),
    ]
