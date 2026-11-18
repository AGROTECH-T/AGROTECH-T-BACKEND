"""Add authenticated delivery identity and durable pause records."""

import uuid

import django.db.models.deletion
from django.db import migrations, models

from apps.iot.migrations._rollback_guard import noop, rollback_barrier


class Migration(migrations.Migration):
    dependencies = [
        ("iot", "0014_iot_mqtt_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="authenticated_coordinator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="authenticated_mqtt_deliveries",
                to="iot.coordinator",
            ),
        ),
        migrations.CreateModel(
            name="IotMqttDeliveryPause",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("reason_code", models.CharField(max_length=64)),
                ("is_active", models.BooleanField(default=True)),
                ("active_scope_key", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("started_process_id", models.CharField(blank=True, max_length=100, null=True)),
                ("resumed_at", models.DateTimeField(blank=True, null=True)),
                ("resumed_process_id", models.CharField(blank=True, max_length=100, null=True)),
                ("coordinator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mqtt_delivery_pauses", to="iot.coordinator")),
                ("resumed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("started_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
            ],
            options={
                "db_table": "iot_mqtt_delivery_pauses",
                "ordering": ["-started_at", "-id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(("is_active", True), ("active_scope_key__isnull", False), ("resumed_at__isnull", True))
                            | models.Q(("is_active", False), ("active_scope_key__isnull", True), ("resumed_at__isnull", False))
                        ),
                        name="iot_mqtt_pause_lifecycle_ck",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["coordinator", "is_active"], name="iot_mqtt_pause_coord_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IotMqttDeliveryPauseEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=16)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("process_id", models.CharField(blank=True, max_length=100, null=True)),
                ("reason_code", models.CharField(max_length=64)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.user")),
                ("pause", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="iot.iotmqttdeliverypause")),
            ],
            options={
                "db_table": "iot_mqtt_delivery_pause_events",
                "ordering": ["occurred_at", "id"],
                "default_manager_name": "objects",
                "base_manager_name": "objects",
                "indexes": [
                    models.Index(fields=["pause", "occurred_at"], name="iot_mqtt_pause_event_idx"),
                ],
            },
        ),
        # VALIDATION-02: first reverse step of the newest IoT migration. Without
        # it, `migrate iot <older>` drops the durable MQTT inbox (0015/0014)
        # before Django reaches the irreversible 0013/0011 and fails. Forward it
        # is a no-op, so databases where 0015 is already applied are unchanged.
        migrations.RunPython(noop, reverse_code=rollback_barrier("iot.0015")),
    ]
