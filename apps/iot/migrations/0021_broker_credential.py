"""FLESPI-01: credenciales de broker (proveedor MQTT externo) por gateway.

Solo crea la tabla ``iot_broker_credentials``; no lee, cambia ni borra datos
existentes ni toca ``iot_gateway_credentials`` ni ``iot_broker_identities``.
Ninguna columna guarda el secreto ni el ``token_id`` del proveedor.

La barrera de reversión es el primer paso al revertir (migración más nueva):
revertir por debajo de este punto borraría el historial de credenciales de
broker y, después, la evidencia protegida por iot.0020/0019.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q

from apps.iot.migrations._rollback_guard import noop, rollback_barrier

STATE_CHOICES = [
    ("pending_broker", "Pendiente del proveedor"),
    ("active", "Activa"),
    ("revoked", "Revocada"),
]
CAUSE_CHOICES = [
    ("admin", "Revocación administrativa"),
    ("compromised", "Comprometida"),
    ("rotated", "Reemplazada al finalizar la rotación"),
    ("abandoned", "Rotación abandonada"),
    ("gateway_deactivated", "Gateway desactivado"),
]
CAUSE_VALUES = ["admin", "compromised", "rotated", "abandoned", "gateway_deactivated"]
ORIGIN_CHOICES = [
    ("provider", "Proveedor registrado"),
    ("test_double", "Doble de pruebas"),
]
ORIGIN_VALUES = ["provider", "test_double"]


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("iot", "0020_broker_identity_overflow_evidence"),
    ]

    operations = [
        migrations.CreateModel(
            name="BrokerCredential",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("provider", models.CharField(default="flespi", max_length=16)),
                ("version", models.PositiveIntegerField()),
                ("correlation_id", models.UUIDField(editable=False, unique=True)),
                (
                    "state",
                    models.CharField(
                        choices=STATE_CHOICES, default="pending_broker", max_length=16
                    ),
                ),
                (
                    "pending_slot",
                    models.PositiveSmallIntegerField(blank=True, default=1, null=True),
                ),
                ("active_slot", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField()),
                ("confirmation_ref", models.UUIDField(blank=True, null=True, unique=True)),
                (
                    "confirmation_origin",
                    models.CharField(
                        blank=True, choices=ORIGIN_CHOICES, max_length=16, null=True
                    ),
                ),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "confirmed_process_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "secret_fingerprint",
                    models.CharField(blank=True, max_length=64, null=True, unique=True),
                ),
                ("key_version", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                (
                    "activated_process_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "activation_evidence_ref",
                    models.UUIDField(blank=True, null=True, unique=True),
                ),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revocation_cause",
                    models.CharField(
                        blank=True, choices=CAUSE_CHOICES, default="", max_length=20
                    ),
                ),
                (
                    "gateway",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="broker_credentials",
                        to="iot.gateway",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="accounts.user",
                    ),
                ),
                (
                    "activated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="accounts.user",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "iot_broker_credentials",
                "ordering": ["-version", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("gateway", "provider", "version"),
                        name="iot_bcred_version_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("gateway", "provider", "pending_slot"),
                        name="iot_bcred_pending_slot_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("gateway", "provider", "active_slot"),
                        name="iot_bcred_active_slot_uniq",
                    ),
                    models.CheckConstraint(
                        condition=Q(provider="flespi"),
                        name="iot_bcred_provider_ck",
                    ),
                    models.CheckConstraint(
                        condition=Q(version__gte=1),
                        name="iot_bcred_version_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(
                                confirmation_ref__isnull=True,
                                confirmation_origin__isnull=True,
                                confirmed_at__isnull=True,
                                confirmed_process_id__isnull=True,
                                secret_fingerprint__isnull=True,
                                key_version__isnull=True,
                            )
                            | Q(
                                confirmation_ref__isnull=False,
                                confirmation_origin__isnull=False,
                                confirmation_origin__in=ORIGIN_VALUES,
                                confirmed_at__isnull=False,
                                confirmed_process_id__isnull=False,
                                secret_fingerprint__isnull=False,
                                secret_fingerprint__regex=r"^[0-9a-f]{64}$",  # noqa: S106
                                key_version__isnull=False,
                                key_version__gte=1,
                            )
                        ),
                        name="iot_bcred_confirmation_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(
                                state="pending_broker",
                                pending_slot__isnull=False,
                                pending_slot=1,
                                active_slot__isnull=True,
                                activated_at__isnull=True,
                                activated_process_id__isnull=True,
                                activation_evidence_ref__isnull=True,
                                revoked_at__isnull=True,
                                revocation_cause="",
                            )
                            | Q(
                                state="active",
                                pending_slot__isnull=True,
                                active_slot__isnull=False,
                                active_slot=1,
                                activated_at__isnull=False,
                                confirmed_at__isnull=False,
                                revoked_at__isnull=True,
                                revocation_cause="",
                            )
                            | Q(
                                state="revoked",
                                pending_slot__isnull=True,
                                active_slot__isnull=True,
                                revoked_at__isnull=False,
                                revocation_cause__in=CAUSE_VALUES,
                            )
                        ),
                        name="iot_bcred_state_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            (~Q(revocation_cause="rotated") | Q(activated_at__isnull=False))
                            & (~Q(revocation_cause="abandoned") | Q(activated_at__isnull=True))
                        ),
                        name="iot_bcred_cause_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            (Q(confirmed_at__isnull=True) | Q(confirmed_at__gte=F("created_at")))
                            & (
                                Q(activated_at__isnull=True)
                                | Q(activated_at__gte=F("confirmed_at"))
                            )
                            & (Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("created_at")))
                        ),
                        name="iot_bcred_dates_ck",
                    ),
                ],
            },
        ),
        migrations.RunPython(noop, reverse_code=rollback_barrier("iot.0021")),
    ]
