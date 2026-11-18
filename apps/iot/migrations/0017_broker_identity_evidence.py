"""BUILD-FLEPSI-02: evidencia de identidad del broker y eventos idempotentes.

- ``IotMqttDelivery``: cinco columnas nuevas. NULL/False/0 para todas las filas
  existentes (sin backfill): las entregas históricas quedan "no capturadas".
- ``IotMqttDeliveryEvent.dedupe_key`` (``ascii_bin``) con
  ``UNIQUE(delivery, dedupe_key)``; NULL para todos los eventos existentes
  (MariaDB admite varios NULL en un índice UNIQUE).

No cambia ``transport_key`` ni el AAD del cifrado. Solo añade columnas y
restricciones; no modifica ni borra datos.
"""

from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Length
from django.db.models.lookups import Exact

import apps.iot.models

PRESENCE_CHOICES = [
    ("absent", "Absent"),
    ("single", "Single"),
    ("multiple", "Multiple"),
    ("invalid", "Invalid"),
    ("oversized", "Oversized"),
]
DEDUPE_KEY_REGEX = (
    r"^bi:((mismatch|postfinal):v[0-9]{1,5}:[0-9a-f]{64}"
    r"|unverifiable:v[0-9]{1,5}|late|limit)$"
)


class Migration(migrations.Migration):
    dependencies = [("iot", "0016_measurement_correction_actor_required")]

    operations = [
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="broker_identity_presence",
            field=models.CharField(blank=True, choices=PRESENCE_CHOICES, max_length=16, null=True),
        ),
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="broker_token_fingerprint",
            field=apps.iot.models.BoundedBinaryField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="broker_token_key_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="broker_identity_conflict",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="iotmqttdelivery",
            name="broker_identity_overflow",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="iotmqttdeliveryevent",
            name="dedupe_key",
            field=models.CharField(blank=True, db_collation="ascii_bin", max_length=96, null=True),
        ),
        migrations.AddConstraint(
            model_name="iotmqttdelivery",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        broker_identity_presence__isnull=True,
                        broker_token_key_version__isnull=True,
                        broker_token_fingerprint__isnull=True,
                        broker_identity_conflict=False,
                        broker_identity_overflow=0,
                    )
                    | Q(
                        broker_identity_presence="single",
                        broker_token_key_version__gte=1,
                        broker_token_key_version__lte=65535,
                        broker_token_fingerprint__isnull=False,
                    )
                    | Q(
                        broker_identity_presence__in=(
                            "absent",
                            "multiple",
                            "invalid",
                            "oversized",
                        ),
                        broker_token_key_version__gte=1,
                        broker_token_key_version__lte=65535,
                        broker_token_fingerprint__isnull=True,
                    )
                ),
                name="iot_mqtt_broker_identity_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="iotmqttdelivery",
            constraint=models.CheckConstraint(
                condition=Q(broker_token_fingerprint__isnull=True)
                | Exact(Length("broker_token_fingerprint"), 32),
                name="iot_mqtt_broker_fp_len_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="iotmqttdeliveryevent",
            constraint=models.UniqueConstraint(
                fields=["delivery", "dedupe_key"], name="iot_mqtt_event_dedupe_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="iotmqttdeliveryevent",
            constraint=models.CheckConstraint(
                condition=Q(dedupe_key__isnull=True) | Q(dedupe_key__regex=DEDUPE_KEY_REGEX),
                name="iot_mqtt_event_dedupe_ck",
            ),
        ),
    ]
