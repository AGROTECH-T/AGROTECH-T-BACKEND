"""BUILD-FLEPSI-02: registro de identidades de broker con huellas por versión.

Solo crea dos tablas nuevas; no toca datos existentes. Ninguna columna guarda
el token ni su ``token_id``.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q
from django.db.models.functions import Length
from django.db.models.lookups import Exact

import apps.iot.models

REVOCATION_CHOICES = [
    ("rotation", "Rotation"),
    ("compromise", "Compromise"),
    ("decommission", "Decommission"),
    ("admin", "Administrative"),
]


class Migration(migrations.Migration):
    dependencies = [("iot", "0017_broker_identity_evidence")]

    operations = [
        migrations.CreateModel(
            name="BrokerIdentity",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("provider", models.CharField(default="flespi", max_length=16)),
                (
                    "active_slot",
                    models.PositiveSmallIntegerField(blank=True, default=1, null=True),
                ),
                ("registered_at", models.DateTimeField()),
                ("registered_process_id", models.CharField(max_length=100)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revoked_process_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "revocation_cause",
                    models.CharField(
                        blank=True, choices=REVOCATION_CHOICES, max_length=16, null=True
                    ),
                ),
                (
                    "gateway",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="broker_identities",
                        to="iot.gateway",
                    ),
                ),
            ],
            options={
                "db_table": "iot_broker_identities",
                "ordering": ["-registered_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("gateway", "provider", "active_slot"),
                        name="iot_bid_active_slot_uniq",
                    ),
                    models.CheckConstraint(
                        condition=Q(provider="flespi"), name="iot_bid_provider_ck"
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(
                                active_slot=1,
                                revoked_at__isnull=True,
                                revoked_process_id__isnull=True,
                                revocation_cause__isnull=True,
                            )
                            | Q(
                                active_slot__isnull=True,
                                revoked_at__isnull=False,
                                revoked_process_id__isnull=False,
                                revocation_cause__in=[
                                    "rotation",
                                    "compromise",
                                    "decommission",
                                    "admin",
                                ],
                            )
                        ),
                        name="iot_bid_state_ck",
                    ),
                    models.CheckConstraint(
                        condition=Q(revoked_at__isnull=True)
                        | Q(revoked_at__gte=F("registered_at")),
                        name="iot_bid_revoked_after_reg_ck",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BrokerIdentityFingerprint",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("provider", models.CharField(default="flespi", max_length=16)),
                ("key_version", models.PositiveIntegerField()),
                ("fingerprint", apps.iot.models.BoundedBinaryField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_process_id", models.CharField(max_length=100)),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fingerprints",
                        to="iot.brokeridentity",
                    ),
                ),
            ],
            options={
                "db_table": "iot_broker_identity_fingerprints",
                "ordering": ["key_version", "id"],
                "default_manager_name": "objects",
                "base_manager_name": "objects",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("identity", "key_version"),
                        name="iot_bidfp_identity_version_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("provider", "key_version", "fingerprint"),
                        name="iot_bidfp_provider_fp_uniq",
                    ),
                    models.CheckConstraint(
                        condition=Q(key_version__gte=1, key_version__lte=65535),
                        name="iot_bidfp_version_ck",
                    ),
                    models.CheckConstraint(
                        condition=Exact(Length("fingerprint"), 32),
                        name="iot_bidfp_len_ck",
                    ),
                    models.CheckConstraint(
                        condition=Q(provider="flespi"), name="iot_bidfp_provider_ck"
                    ),
                ],
            },
        ),
    ]
