"""Modelos de dispositivos IoT (IoT-02.2, DEC-13).

Propósito: registrar gateways, coordinadores y sensores por finca, y las
autorizaciones históricas entre coordinadores y gateways.

Contexto:
- La clave primaria es ``BigAutoField`` (convención del proyecto). El
  identificador público ``uid`` (``apps.iot.identifiers``) es permanente.
- Toda entidad pertenece a una finca (``farm``, PROTECT): una finca con
  dispositivos no puede borrarse físicamente; las fincas se desactivan (DEC-01).
- El coordinador no tiene conectividad IP: solo habla con el gateway por LoRa.
- La pertenencia a la misma finca entre coordinador, gateway y sensor la
  garantiza la base de datos con FK compuestas ``(x_id, farm_id)`` creadas en
  ``migrations/0002_composite_farm_fks.py``. Django no las modela; por eso cada
  entidad referenciada declara ``UNIQUE (id, farm)`` y cada referencia declara
  un índice ``(x, farm)`` que la FK compuesta reutiliza.
- MariaDB no admite índices únicos parciales: la unicidad de "solo una fila
  activa" usa columnas auxiliares que valen NULL cuando la fila deja de estar
  activa (``active_channel``, ``active_pair``), igual que
  ``FarmInvitation.pending_email``. MariaDB admite varios NULL en un índice
  único.
- No hay modelo de grupos de sensores ni dependencia de avicultura.
"""
from decimal import Decimal
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Length
from django.db.models.lookups import Exact, GreaterThanOrEqual

from apps.accounts.models import User
from apps.farms.models import Farm
from apps.iot import lora_frame
from apps.iot.catalog import MetricCode
from apps.iot.identifiers import IDENTIFIER_ALPHABET, RANDOM_PART_LENGTH, IdentifierKind
from config.coordinator_key_encryption_key import MASTER_KEY_VERSION_MAX

NAME_MAX_LENGTH = 100
LOCATION_LABEL_MAX_LENGTH = 100
UID_MAX_LENGTH = 13
METRIC_MAX_LENGTH = 40
ACTIVE_PAIR = 1
SOURCE_NAME_MAX_LENGTH = 100
SOURCE_MODEL_MAX_LENGTH = 80
SOURCE_MANUFACTURER_MAX_LENGTH = 100
SOURCE_ROLE_MAX_LENGTH = 32


class BoundedBinaryField(models.BinaryField):
    """Use indexable VARBINARY columns for bounded values on MySQL/MariaDB."""

    def db_type(self, connection):
        if connection.vendor == "mysql" and self.max_length is not None:
            return f"varbinary({self.max_length})"
        return super().db_type(connection)


class _AppendOnlyQuerySet(models.QuerySet):
    """Block ordinary ORM paths that would rewrite append-only evidence."""

    def update(self, **kwargs):
        # Django's deletion Collector issues ``SET_NULL`` updates on related
        # querysets even when they match no rows. An update that matches
        # nothing rewrites nothing, so it is allowed as a no-op; any update
        # that would touch existing evidence is still refused.
        if not self.exists():
            return 0
        raise ValidationError("el historial IoT es inmutable")

    def delete(self):
        raise ValidationError("el historial IoT es inmutable")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("el historial IoT es inmutable")

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if ignore_conflicts or update_conflicts:
            raise ValidationError(
                "el historial IoT no admite upsert ni ignorar colisiones"
            )
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=False,
            update_conflicts=False,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class _AppendOnlyManager(models.Manager.from_queryset(_AppendOnlyQuerySet)):
    """Manager for rows that may be inserted but never rewritten or deleted."""


def _uid_regex(kind: IdentifierKind) -> str:
    """Expresión del formato canónico, usada en el CHECK de la base."""
    return f"^{kind.value}-[{IDENTIFIER_ALPHABET}]{{{RANDOM_PART_LENGTH}}}$"


def _uid_check(kind: IdentifierKind, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(uid__regex=_uid_regex(kind)), name=name)


def _lifecycle_check(name: str) -> models.CheckConstraint:
    """Activo sin datos de baja; inactivo siempre con fecha de baja."""
    return models.CheckConstraint(
        condition=(
            Q(is_active=True, deactivated_at__isnull=True, deactivated_by__isnull=True)
            | Q(is_active=False, deactivated_at__isnull=False)
        ),
        name=name,
    )


class _Lifecycle(models.Model):
    """Campos comunes de estado, alta y baja lógica."""

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    deactivated_at = models.DateTimeField(blank=True, null=True)
    deactivated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )

    class Meta:
        abstract = True


class Gateway(_Lifecycle):
    """ESP32 que recibe LoRa y transmite al servidor por Wi-Fi/MQTT."""

    uid = models.CharField(max_length=UID_MAX_LENGTH, unique=True)
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_gateways")
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    location_label = models.CharField(
        max_length=LOCATION_LABEL_MAX_LENGTH,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "iot_gateways"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=("id", "farm"), name="iot_gateway_id_farm_uniq"),
            _uid_check(IdentifierKind.GATEWAY, "iot_gateway_uid_format"),
            _lifecycle_check("iot_gateway_lifecycle_valid"),
        ]
        indexes = [
            models.Index(fields=["farm", "is_active"], name="iot_gw_farm_active_idx"),
        ]


class Coordinator(_Lifecycle):
    """ESP32 de campo con sensores cableados; se comunica solo por LoRa."""

    uid = models.CharField(max_length=UID_MAX_LENGTH, unique=True)
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_coordinators")
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    location_label = models.CharField(
        max_length=LOCATION_LABEL_MAX_LENGTH,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "iot_coordinators"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=("id", "farm"), name="iot_coord_id_farm_uniq"),
            _uid_check(IdentifierKind.COORDINATOR, "iot_coord_uid_format"),
            _lifecycle_check("iot_coord_lifecycle_valid"),
        ]
        indexes = [
            models.Index(fields=["farm", "is_active"], name="iot_co_farm_active_idx"),
        ]


class SensorSourceRole(models.TextChoices):
    """Rol descriptivo de una fuente física, sin política de failover."""

    PRIMARY = "primary", "Principal"
    BACKUP_COMPARISON = "backup_comparison", "Respaldo y comparación"


class SensorSource(_Lifecycle):
    """Componente físico que origina uno o varios sensores lógicos."""

    uid = models.UUIDField(default=uuid4, unique=True, editable=False)
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_sensor_sources")
    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="sensor_sources",
    )
    name = models.CharField(max_length=SOURCE_NAME_MAX_LENGTH)
    model = models.CharField(max_length=SOURCE_MODEL_MAX_LENGTH, blank=True, null=True)
    manufacturer = models.CharField(
        max_length=SOURCE_MANUFACTURER_MAX_LENGTH,
        blank=True,
        null=True,
    )
    role = models.CharField(
        max_length=SOURCE_ROLE_MAX_LENGTH,
        choices=SensorSourceRole.choices,
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "iot_sensor_sources"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("id", "coordinator", "farm"),
                name="iot_ss_id_coord_farm_uniq",
            ),
            models.CheckConstraint(
                condition=Q(role__isnull=True) | Q(role__in=SensorSourceRole.values),
                name="iot_ss_role_valid",
            ),
            _lifecycle_check("iot_ss_lifecycle_valid"),
        ]
        indexes = [
            models.Index(fields=["coordinator", "farm"], name="iot_ss_coord_farm_idx"),
            models.Index(fields=["farm", "is_active"], name="iot_ss_farm_active_idx"),
        ]

    def save(self, *args, **kwargs):
        """Protect identity/configuration through ordinary ORM saves."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "uid", "farm_id", "coordinator_id", "name", "model", "manufacturer", "role"
            ).first()
            if previous is not None:
                if UUID(str(previous["uid"])) != UUID(str(self.uid)):
                    raise ValidationError("el UID de la fuente es inmutable")
                if (previous["farm_id"], previous["coordinator_id"]) != (
                    self.farm_id,
                    self.coordinator_id,
                ):
                    raise ValidationError("la finca y el coordinador de la fuente son inmutables")
                has_measurements = Measurement.objects.filter(sensor__source_id=self.pk).exists()
                immutable_fields = ("name", "model", "manufacturer", "role")
                if has_measurements and any(
                    previous[field] != getattr(self, field) for field in immutable_fields
                ):
                    raise ValidationError("la identidad de una fuente con mediciones es inmutable")
        return super().save(*args, **kwargs)


class SensorSourceChange(models.Model):
    """Audited before/after snapshots of editable source metadata."""

    source = models.ForeignKey(
        SensorSource,
        on_delete=models.PROTECT,
        related_name="changes",
    )
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    before_data = models.JSONField()
    after_data = models.JSONField()

    objects = _AppendOnlyManager()

    class Meta:
        db_table = "iot_sensor_source_changes"
        ordering = ["-changed_at", "-id"]
        indexes = [models.Index(fields=["source", "changed_at"], name="iot_ss_change_time_idx")]


class Sensor(_Lifecycle):
    """Logical measurement endpoint connected to one coordinator channel.

    La unidad se obtiene de ``apps.iot.catalog`` a partir de ``metric``.
    ``active_channel`` vale ``channel`` mientras el sensor está activo y NULL
    cuando se desactiva; su índice único impide dos sensores activos en el mismo
    canal sin bloquear el historial. No hay límite máximo de canales (pendiente
    hasta conocer el cableado y el firmware).
    """

    uid = models.CharField(max_length=UID_MAX_LENGTH, unique=True)
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_sensors")
    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="sensors",
    )
    source = models.ForeignKey(
        SensorSource,
        on_delete=models.PROTECT,
        related_name="sensors",
        blank=True,
        null=True,
    )
    channel = models.PositiveSmallIntegerField()
    metric = models.CharField(max_length=METRIC_MAX_LENGTH, choices=MetricCode.choices)
    active_channel = models.PositiveSmallIntegerField(blank=True, null=True)

    class Meta:
        db_table = "iot_sensors"
        ordering = ["coordinator_id", "channel", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("coordinator", "active_channel"),
                name="iot_sensor_active_channel_uniq",
            ),
            _uid_check(IdentifierKind.SENSOR, "iot_sensor_uid_format"),
            models.CheckConstraint(
                condition=Q(metric__in=MetricCode.values),
                name="iot_sensor_metric_valid",
            ),
            _lifecycle_check("iot_sensor_lifecycle_valid"),
            models.CheckConstraint(
                # ``isnull=False`` explícito: en SQL un CHECK con resultado
                # NULL (desconocido) se considera cumplido.
                condition=(
                    Q(is_active=True, active_channel__isnull=False, active_channel=F("channel"))
                    | Q(is_active=False, active_channel__isnull=True)
                ),
                name="iot_sensor_active_channel_valid",
            ),
            # Destino de la FK compuesta de ``Measurement`` (IoT-04.3-B, iot.0008).
            models.UniqueConstraint(
                fields=("id", "coordinator", "channel", "metric"),
                name="iot_sensor_id_coord_ch_metric_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["coordinator", "farm"], name="iot_sensor_coord_farm_idx"),
            models.Index(fields=["farm", "is_active"], name="iot_sn_farm_active_idx"),
            models.Index(fields=["source", "coordinator", "farm"], name="iot_sn_source_fk_idx"),
        ]

    def save(self, *args, **kwargs):
        """Protect stable identity and historical measurement mapping."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "uid", "farm_id", "coordinator_id", "source_id", "channel", "metric"
            ).first()
            if previous is not None:
                if previous["uid"] != self.uid:
                    raise ValidationError("el UID del sensor es inmutable")
                if (previous["farm_id"], previous["coordinator_id"]) != (
                    self.farm_id,
                    self.coordinator_id,
                ):
                    raise ValidationError("la finca y el coordinador del sensor son inmutables")
                has_measurements = Measurement.objects.filter(sensor_id=self.pk).exists()
                protected_fields = ("source_id", "channel", "metric")
                if has_measurements and any(
                    previous[field] != getattr(self, field) for field in protected_fields
                ):
                    raise ValidationError(
                        "la identidad del sensor con mediciones es inmutable"
                    )
        return super().save(*args, **kwargs)


class CoordinatorGateway(models.Model):
    """Autorización histórica de un coordinador para enviar por un gateway.

    Las filas no se borran: revocar cierra el periodo (``revoked_at``) y pone
    ``active_pair`` a NULL. Reautorizar crea una fila nueva. Un coordinador
    puede tener varias autorizaciones vigentes con gateways de su misma finca.
    """

    farm = models.ForeignKey(
        Farm,
        on_delete=models.PROTECT,
        related_name="iot_coordinator_gateways",
    )
    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="gateway_authorizations",
    )
    gateway = models.ForeignKey(
        Gateway,
        on_delete=models.PROTECT,
        related_name="coordinator_authorizations",
    )
    authorized_at = models.DateTimeField()
    authorized_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    active_pair = models.PositiveSmallIntegerField(blank=True, null=True, default=ACTIVE_PAIR)

    class Meta:
        db_table = "iot_coordinator_gateways"
        ordering = ["-authorized_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("coordinator", "gateway", "active_pair"),
                name="iot_cg_active_pair_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        active_pair__isnull=False,
                        active_pair=ACTIVE_PAIR,
                        revoked_at__isnull=True,
                        revoked_by__isnull=True,
                    )
                    | Q(active_pair__isnull=True, revoked_at__isnull=False)
                ),
                name="iot_cg_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("authorized_at")),
                name="iot_cg_revoked_after_auth",
            ),
            # Destino de la FK compuesta de ``ReceivedFrame`` (IoT-04.3-B, iot.0008).
            models.UniqueConstraint(
                fields=("id", "coordinator", "gateway", "farm"),
                name="iot_cg_id_coord_gw_farm_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["coordinator", "farm"], name="iot_cg_coord_farm_idx"),
            models.Index(fields=["gateway", "farm"], name="iot_cg_gw_farm_idx"),
        ]

    @property
    def is_active(self) -> bool:
        """La autorización está vigente."""
        return self.active_pair == ACTIVE_PAIR


ACTIVE_SLOT = 1
FINGERPRINT_MAX_LENGTH = 64
REVOCATION_CAUSE_MAX_LENGTH = 20


class RevocationCause(models.TextChoices):
    """Motivo por el que una credencial de gateway dejó de ser válida."""

    ROTATED = "rotated", "Rotada"
    REISSUED = "reissued", "Reemitida"
    COMPROMISED = "compromised", "Comprometida"
    GATEWAY_DEACTIVATED = "gateway_deactivated", "Gateway desactivado"
    ADMIN = "admin", "Revocación administrativa"


class GatewayCredential(models.Model):
    """Credencial de un gateway para autenticarse ante el broker (IoT-03.1).

    Solo guarda la huella HMAC del secreto (``apps.iot.gateway_credentials``):
    el secreto no se persiste y no puede recuperarse. ``active_slot`` vale 1 en
    la credencial vigente y NULL en las revocadas; ``UNIQUE (gateway,
    active_slot)`` garantiza una sola vigente por gateway (MariaDB admite varios
    NULL) y conserva el historial. Una credencial revocada nunca vuelve a
    activarse. Sin ``farm``: la finca se obtiene del gateway. Sin caducidad ni
    ``last_used_at`` (IoT-05).
    """

    gateway = models.ForeignKey(Gateway, on_delete=models.PROTECT, related_name="credentials")
    fingerprint = models.CharField(max_length=FINGERPRINT_MAX_LENGTH, unique=True)
    key_version = models.PositiveSmallIntegerField()
    active_slot = models.PositiveSmallIntegerField(blank=True, null=True, default=ACTIVE_SLOT)
    issued_at = models.DateTimeField()
    issued_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    revocation_cause = models.CharField(
        max_length=REVOCATION_CAUSE_MAX_LENGTH,
        choices=RevocationCause.choices,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "iot_gateway_credentials"
        ordering = ["-issued_at", "-id"]
        constraints = [
            # También sirve de índice para buscar las credenciales de un gateway.
            models.UniqueConstraint(
                fields=("gateway", "active_slot"),
                name="iot_gwcred_active_slot_uniq",
            ),
            models.CheckConstraint(
                condition=Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="iot_gwcred_fingerprint_format",
            ),
            models.CheckConstraint(
                condition=Q(key_version__gte=1),
                name="iot_gwcred_key_version_valid",
            ),
            # ``isnull=False`` explícito: en SQL un CHECK con resultado NULL se
            # considera cumplido.
            models.CheckConstraint(
                condition=(
                    Q(
                        active_slot__isnull=False,
                        active_slot=ACTIVE_SLOT,
                        revoked_at__isnull=True,
                        revoked_by__isnull=True,
                        revocation_cause="",
                    )
                    | Q(
                        active_slot__isnull=True,
                        revoked_at__isnull=False,
                        revocation_cause__in=RevocationCause.values,
                    )
                ),
                name="iot_gwcred_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("issued_at")),
                name="iot_gwcred_revoked_after_issue",
            ),
        ]

    @property
    def is_active(self) -> bool:
        """La credencial está vigente (no revocada)."""
        return self.active_slot == ACTIVE_SLOT


COORDINATOR_CIPHERTEXT_LENGTH = 48
COORDINATOR_NONCE_LENGTH = 12
COORDINATOR_REVOCATION_CAUSE_MAX_LENGTH = 23
COORDINATOR_KEY_VERSION_MIN = 1
COORDINATOR_KEY_VERSION_MAX = 65535
BOOT_WINDOW_SIZE = 4


class CoordinatorRevocationCause(models.TextChoices):
    """Motivo por el que una credencial de coordinador dejó de ser válida.

    Enum independiente del de gateway (IoT-03.1): no se alteran sus
    restricciones ni su historial.
    """

    ROTATED = "rotated", "Rotada"
    REISSUED = "reissued", "Reemitida"
    COMPROMISED = "compromised", "Comprometida"
    COORDINATOR_DEACTIVATED = "coordinator_deactivated", "Coordinador desactivado"
    ADMIN = "admin", "Revocación administrativa"


class CoordinatorCredential(models.Model):
    """Clave HMAC de un coordinador, cifrada en reposo (IoT-03.2).

    Solo se guarda el ciphertext AES-256-GCM (48 bytes: 32 de clave + 16 de
    etiqueta GCM) y su nonce (12 bytes). La clave original no se persiste y no
    puede recuperarse sin la clave maestra. ``active_slot`` vale 1 en la
    credencial vigente y NULL en las revocadas; ``UNIQUE (coordinator,
    active_slot)`` garantiza una sola vigente por coordinador (MariaDB admite
    varios NULL) y conserva el historial. Una credencial revocada nunca vuelve
    a activarse. Sin ``farm``: la finca la determina el coordinador. Sin
    caducidad automática.
    """

    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="credentials",
    )
    ciphertext = models.BinaryField(editable=False)
    nonce = models.BinaryField(editable=False)
    master_key_version = models.PositiveSmallIntegerField()
    key_version = models.PositiveSmallIntegerField(blank=True, null=True)
    active_slot = models.PositiveSmallIntegerField(blank=True, null=True, default=ACTIVE_SLOT)
    issued_at = models.DateTimeField()
    issued_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    revocation_cause = models.CharField(
        max_length=COORDINATOR_REVOCATION_CAUSE_MAX_LENGTH,
        choices=CoordinatorRevocationCause.choices,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "iot_coordinator_credentials"
        ordering = ["-issued_at", "-id"]
        constraints = [
            # También sirve de índice para buscar la credencial de un coordinador.
            models.UniqueConstraint(
                fields=("coordinator", "active_slot"),
                name="iot_coordcred_active_slot_uniq",
            ),
            models.CheckConstraint(
                condition=Exact(Length("ciphertext"), Value(COORDINATOR_CIPHERTEXT_LENGTH)),
                name="iot_coordcred_ciphertext_length",
            ),
            models.CheckConstraint(
                condition=Exact(Length("nonce"), Value(COORDINATOR_NONCE_LENGTH)),
                name="iot_coordcred_nonce_length",
            ),
            models.CheckConstraint(
                condition=Q(
                    master_key_version__gte=1,
                    master_key_version__lte=MASTER_KEY_VERSION_MAX,
                ),
                name="iot_coordcred_key_version_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(key_version__isnull=True)
                    | Q(
                        key_version__gte=COORDINATOR_KEY_VERSION_MIN,
                        key_version__lte=COORDINATOR_KEY_VERSION_MAX,
                    )
                ),
                name="iot_coordcred_generation_range",
            ),
            models.UniqueConstraint(
                fields=("coordinator", "key_version"),
                name="iot_coordcred_generation_uniq",
            ),
            # ``isnull=False`` explícito: en SQL un CHECK con resultado NULL se
            # considera cumplido.
            models.CheckConstraint(
                condition=(
                    Q(
                        active_slot__isnull=False,
                        active_slot=ACTIVE_SLOT,
                        revoked_at__isnull=True,
                        revoked_by__isnull=True,
                        revocation_cause="",
                    )
                    | Q(
                        active_slot__isnull=True,
                        revoked_at__isnull=False,
                        revocation_cause__in=CoordinatorRevocationCause.values,
                    )
                ),
                name="iot_coordcred_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("issued_at")),
                name="iot_coordcred_revoked_after_issue",
            ),
            # Destino de la FK compuesta de ``ReceivedFrame`` (IoT-04.3-B, iot.0008).
            models.UniqueConstraint(
                fields=("coordinator", "id"),
                name="iot_coordcred_coord_id_uniq",
            ),
        ]

    @property
    def is_active(self) -> bool:
        """La credencial está vigente (no revocada)."""
        return self.active_slot == ACTIVE_SLOT


class CoordinatorKeyGeneration(models.Model):
    """Contador durable de generaciones de credencial por coordinador (C1).

    La generación identifica la clave HMAC emitida; no se confunde con
    ``master_key_version``, que identifica la clave maestra que cifra el
    reposo. El contador se crea al emitir la primera credencial, solo avanza y
    nunca se reinicia ni se reutiliza. ``updated_at`` lo fija el servicio en la
    misma transacción; no usa ``auto_now``.
    """

    coordinator = models.OneToOneField(
        Coordinator,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="key_generation",
    )
    last_key_version = models.PositiveSmallIntegerField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "iot_coordinator_key_generation"
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    last_key_version__gte=COORDINATOR_KEY_VERSION_MIN,
                    last_key_version__lte=COORDINATOR_KEY_VERSION_MAX,
                ),
                name="iot_coordkeygen_range",
            ),
        ]


class CoordinatorBootWindow(models.Model):
    """Arranques recientes aceptados de un coordinador (anti-replay, IoT-03.2).

    Ventana deslizante de hasta ``BOOT_WINDOW_SIZE`` (cuatro) ``boot_counter``:
    el mayor visto y los tres anteriores. Permite aceptar tramas retrasadas de
    un arranque dentro de la ventana y rechazar los que ya salieron de ella.
    No guarda ``seq``: el orden y los duplicados dentro de un arranque los
    resuelve la restricción única de mediciones de IoT-04.

    Se mantiene separado del ciclo de vida de ``Coordinator`` para no tocar su
    ``updated_at`` ni su auditoría administrativa. ``updated_at`` no es
    ``auto_now``: la fecha la fija el servicio en la transacción del llamador.
    """

    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="boot_windows",
    )
    boot_counter = models.PositiveBigIntegerField()
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        db_table = "iot_coordinator_boot_window"
        ordering = ["coordinator_id", "-boot_counter"]
        constraints = [
            models.UniqueConstraint(
                fields=("coordinator", "boot_counter"),
                name="iot_coordwindow_boot_uniq",
            ),
            models.CheckConstraint(
                condition=Q(boot_counter__gte=0),
                name="iot_coordwindow_boot_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["coordinator", "-boot_counter"],
                name="iot_coordwindow_recent_idx",
            ),
        ]


# --- Persistencia de telemetría (IoT-04.3-B, DEC-13 §16) ---

UINT16_MAX = 65535
UINT32_MAX = 4_294_967_295
INT16_MIN = -32768
INT16_MAX = 32767
FRAME_VERSION_V1 = lora_frame.FRAME_VERSION_V1
# Bytes fijos de una trama v1 sin lecturas: cabecera (25) + tag (32).
FRAME_V1_FIXED_BYTES = lora_frame.HEADER_BYTES + lora_frame.TAG_BYTES
FRAME_V1_READING_BYTES = lora_frame.READING_BYTES
VALUE_SCALE = Decimal("100")
SHA256_HEX_LENGTH = 64

# Centinelas aprobados (D-09); deben coincidir con el parser.
METRIC_INVALID_MARKERS = {
    MetricCode.TEMPERATURE: lora_frame.TEMPERATURE_INVALID_MARKER,
    MetricCode.RELATIVE_HUMIDITY: lora_frame.HUMIDITY_INVALID_MARKER,
}
if METRIC_INVALID_MARKERS != {  # pragma: no cover - invariante
    MetricCode.TEMPERATURE: INT16_MIN,
    MetricCode.RELATIVE_HUMIDITY: UINT16_MAX,
}:
    raise RuntimeError("los centinelas de D-09 no coinciden con el parser")


def _is_invalid_marker() -> Q:
    """``raw_value`` es el centinela aprobado de su métrica (D-09)."""
    return Q(metric=MetricCode.TEMPERATURE, raw_value=INT16_MIN) | Q(
        metric=MetricCode.RELATIVE_HUMIDITY, raw_value=UINT16_MAX
    )


class ReceivedFrame(models.Model):
    """Trama LoRa autenticada y aceptada (IoT-04.3-1, IoT-04.3-2).

    Solo existen tramas aceptadas: no hay columna de estado y las inválidas no
    se guardan aquí. ``raw_frame`` conserva los bytes exactos recibidos;
    ``signed_bytes`` y ``tag`` son cortes de ese campo y no se guardan aparte.
    No contiene secretos (el tag HMAC no lo es).

    Identidad de deduplicación (D-01): ``UNIQUE (coordinator, credential,
    boot_counter, seq)``. ``authorization`` es la fila de
    ``CoordinatorGateway`` vigente al aceptar la trama (IoT-04.3-6).

    Integridad entre filas: FK compuestas de ``iot.0008`` (Django no las
    modela). La de ``authorization`` garantiza coordinador, gateway y finca
    coherentes, por lo que no hay FK compuestas propias ``(gateway, farm)`` ni
    ``(coordinator, farm)``. Puede existir sin mediciones (IoT-04.3-10).

    Fechas: ``gateway_received_at``, ``gateway_clock_synced``, ``buffered``,
    ``rssi`` y ``snr`` los declara el gateway y no los firma el coordinador;
    ``server_received_at`` es el reloj del servidor; ``measured_at`` es la hora
    estimada de captura y ``measured_at_approximate`` indica que se usó la del
    servidor (la tolerancia es P-IOT-11, pendiente).
    """

    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_received_frames")
    gateway = models.ForeignKey(
        Gateway, on_delete=models.PROTECT, related_name="received_frames"
    )
    coordinator = models.ForeignKey(
        Coordinator, on_delete=models.PROTECT, related_name="received_frames"
    )
    credential = models.ForeignKey(
        CoordinatorCredential, on_delete=models.PROTECT, related_name="received_frames"
    )
    authorization = models.ForeignKey(
        CoordinatorGateway, on_delete=models.PROTECT, related_name="received_frames"
    )
    frame_version = models.PositiveSmallIntegerField()
    key_version = models.PositiveIntegerField()
    boot_counter = models.PositiveBigIntegerField()
    seq = models.PositiveBigIntegerField()
    reading_count = models.PositiveSmallIntegerField()
    raw_frame = models.BinaryField(editable=False)
    gateway_received_at = models.DateTimeField(blank=True, null=True)
    gateway_clock_synced = models.BooleanField()
    buffered = models.BooleanField()
    rssi = models.SmallIntegerField(blank=True, null=True)
    snr = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    server_received_at = models.DateTimeField()
    measured_at = models.DateTimeField()
    measured_at_approximate = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _AppendOnlyManager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("las tramas IoT aceptadas son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("las tramas IoT aceptadas no se pueden eliminar")

    class Meta:
        db_table = "iot_received_frames"
        ordering = ["-measured_at", "-id"]
        default_manager_name = "objects"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=("coordinator", "credential", "boot_counter", "seq"),
                name="iot_rframe_identity_uniq",
            ),
            # Destinos de las FK compuestas de Measurement y FrameConflict.
            models.UniqueConstraint(
                fields=("id", "coordinator", "farm", "measured_at"),
                name="iot_rframe_id_coord_farm_at_uniq",
            ),
            models.UniqueConstraint(fields=("id", "farm"), name="iot_rframe_id_farm_uniq"),
            models.CheckConstraint(
                condition=Q(frame_version=FRAME_VERSION_V1),
                name="iot_rframe_version_valid",
            ),
            models.CheckConstraint(
                condition=Q(key_version__gte=1, key_version__lte=UINT16_MAX),
                name="iot_rframe_key_version_valid",
            ),
            models.CheckConstraint(
                condition=Q(boot_counter__gte=0, boot_counter__lte=UINT32_MAX),
                name="iot_rframe_boot_counter_valid",
            ),
            models.CheckConstraint(
                condition=Q(seq__gte=0, seq__lte=UINT32_MAX),
                name="iot_rframe_seq_valid",
            ),
            # Sin máximo: 16 lecturas es un límite provisional del parser.
            models.CheckConstraint(
                condition=Q(reading_count__gte=1),
                name="iot_rframe_reading_count_valid",
            ),
            # Longitud del contrato v1: 25 + 5 × reading_count + 32.
            models.CheckConstraint(
                condition=~Q(frame_version=FRAME_VERSION_V1)
                | Exact(
                    Length("raw_frame"),
                    F("reading_count") * FRAME_V1_READING_BYTES + FRAME_V1_FIXED_BYTES,
                ),
                name="iot_rframe_v1_length_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["authorization", "coordinator", "gateway", "farm"],
                name="iot_rframe_auth_fk_idx",
            ),
            models.Index(fields=["farm", "created_at"], name="iot_rframe_farm_created_idx"),
        ]


class Measurement(models.Model):
    """Lectura guardada de una trama aceptada (IoT-04.3-B).

    Solo se guardan lecturas de sensores registrados, activos y con métrica
    coincidente (IoT-04.3-8); la lectura de un sensor desactivado no se guarda
    y sigue en ``ReceivedFrame.raw_frame``. ``raw_value`` es el entero recibido
    (int16 para temperatura, uint16 para humedad); ``value`` es
    ``raw_value / 100`` y vale NULL solo para el centinela aprobado de su
    métrica (D-09). Los rangos físicos y la política de centinelas siguen
    pendientes (P-IOT-28): aquí solo se exige la representación del tipo. La
    unidad sale del catálogo. ``farm``, ``coordinator`` y ``measured_at`` son
    copias de la trama garantizadas por FK compuesta (IoT-04.3-4).
    """

    uid = models.UUIDField(default=uuid4, unique=True, editable=False)
    frame = models.ForeignKey(
        ReceivedFrame, on_delete=models.PROTECT, related_name="measurements"
    )
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_measurements")
    coordinator = models.ForeignKey(
        Coordinator, on_delete=models.PROTECT, related_name="measurements"
    )
    sensor = models.ForeignKey(Sensor, on_delete=models.PROTECT, related_name="measurements")
    channel = models.PositiveSmallIntegerField()
    metric = models.CharField(max_length=METRIC_MAX_LENGTH, choices=MetricCode.choices)
    raw_value = models.IntegerField()
    value = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    is_invalid_marker = models.BooleanField()
    measured_at = models.DateTimeField()

    objects = _AppendOnlyManager()

    class Meta:
        db_table = "iot_measurements"
        ordering = ["-measured_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("frame", "channel"), name="iot_meas_frame_channel_uniq"
            ),
            models.CheckConstraint(condition=Q(channel__gte=1), name="iot_meas_channel_valid"),
            models.CheckConstraint(
                condition=Q(metric__in=MetricCode.values),
                name="iot_meas_metric_valid",
            ),
            # Representación del tipo (no rangos físicos): int16 / uint16.
            models.CheckConstraint(
                condition=Q(
                    metric=MetricCode.TEMPERATURE,
                    raw_value__gte=INT16_MIN,
                    raw_value__lte=INT16_MAX,
                )
                | Q(
                    metric=MetricCode.RELATIVE_HUMIDITY,
                    raw_value__gte=0,
                    raw_value__lte=10000,
                )
                | Q(metric=MetricCode.RELATIVE_HUMIDITY, raw_value=UINT16_MAX),
                name="iot_meas_raw_value_type",
            ),
            models.CheckConstraint(
                condition=Q(_is_invalid_marker(), is_invalid_marker=True)
                | Q(~_is_invalid_marker(), is_invalid_marker=False),
                name="iot_meas_marker_valid",
            ),
            models.CheckConstraint(
                condition=Q(is_invalid_marker=True, value__isnull=True)
                | Q(
                    is_invalid_marker=False,
                    value__isnull=False,
                    value=F("raw_value") / Value(VALUE_SCALE),
                ),
                name="iot_meas_value_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["sensor", "measured_at"], name="iot_meas_sensor_time_idx"),
            models.Index(fields=["farm", "measured_at"], name="iot_meas_farm_time_idx"),
            models.Index(
                fields=["frame", "coordinator", "farm", "measured_at"],
                name="iot_meas_frame_fk_idx",
            ),
            models.Index(
                fields=["sensor", "coordinator", "channel", "metric"],
                name="iot_meas_sensor_fk_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        """Allow ingestion inserts; reject every update of historical evidence."""
        if not self._state.adding:
            raise ValidationError("las mediciones registradas son inmutables")
        if kwargs.get("update_fields") is not None:
            raise ValidationError("una medición nueva no admite update_fields")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent ORM deletion; the database trigger also covers direct DML."""
        raise ValidationError("las mediciones registradas no se pueden eliminar")


class MeasurementCorrectionType(models.TextChoices):
    VALUE_ADJUSTMENT = "value_adjustment", "Corrección de valor"
    INVALIDATION = "invalidation", "Invalidación"


class MeasurementCorrection(models.Model):
    """Append-only, traceable correction of an immutable measurement."""

    uid = models.UUIDField(default=uuid4, unique=True, editable=False)
    measurement = models.ForeignKey(
        Measurement,
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    revision = models.PositiveIntegerField()
    previous_correction = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="successors",
        blank=True,
        null=True,
    )
    previous_revision = models.PositiveIntegerField(blank=True, null=True)
    value_before = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    corrected_value = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    correction_type = models.CharField(
        max_length=32,
        choices=MeasurementCorrectionType.choices,
    )
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="iot_measurement_corrections",
        blank=True,
        null=True,
    )
    process_id = models.CharField(max_length=100, blank=True, null=True)

    objects = _AppendOnlyManager()

    class Meta:
        db_table = "iot_measurement_corrections"
        ordering = ["measurement_id", "revision"]
        constraints = [
            models.UniqueConstraint(
                fields=("measurement", "revision"),
                name="iot_mcorr_meas_revision_uniq",
            ),
            models.UniqueConstraint(
                fields=("id", "measurement", "revision"),
                name="iot_mcorr_id_meas_rev_uniq",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="iot_mcorr_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        correction_type=MeasurementCorrectionType.VALUE_ADJUSTMENT,
                        corrected_value__isnull=False,
                    )
                    | Q(
                        correction_type=MeasurementCorrectionType.INVALIDATION,
                        corrected_value__isnull=True,
                    )
                ),
                name="iot_mcorr_type_value_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(created_by__isnull=False, process_id__isnull=True)
                    # process_id IS NOT NULL is explicit: NULL REGEXP is NULL and a
                    # CHECK only rejects FALSE (VALIDATION-02, iot.0016).
                    | Q(
                        created_by__isnull=True,
                        process_id__isnull=False,
                        process_id__regex=r".*\S.*",
                    )
                ),
                name="iot_mcorr_actor_required",
            ),
            models.CheckConstraint(
                condition=Q(reason__regex=r".*\S.*"),
                name="iot_mcorr_reason_nonblank",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        revision=1,
                        previous_correction__isnull=True,
                        previous_revision__isnull=True,
                    )
                    | Q(
                        revision__gt=1,
                        previous_correction__isnull=False,
                        previous_revision=F("revision") - Value(1),
                    )
                ),
                name="iot_mcorr_previous_revision_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["measurement", "-revision"],
                name="iot_mcorr_meas_rev_idx",
            ),
            models.Index(
                fields=["previous_correction", "measurement", "previous_revision"],
                name="iot_mcorr_prev_fk_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        """Permit only inserts; corrections are new facts, never edits."""
        if not self._state.adding:
            raise ValidationError("las correcciones registradas son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("las correcciones registradas no se pueden eliminar")


class MeasurementCorrectionSequence(models.Model):
    """Internal monotonic sequence lock maintained by a MariaDB trigger."""

    measurement = models.OneToOneField(
        Measurement,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="correction_sequence",
    )
    last_revision = models.PositiveIntegerField(default=0)

    objects = _AppendOnlyManager()

    class Meta:
        db_table = "iot_measurement_correction_sequences"
        constraints = [
            models.CheckConstraint(
                condition=Q(last_revision__gte=0),
                name="iot_mcorrseq_revision_nonnegative",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("la secuencia de correcciones es interna")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("la secuencia de correcciones es interna")


class FrameConflict(models.Model):
    """Conflicto de identidad: misma identidad, bytes distintos (IoT-04.3-9).

    No modifica la trama original. ``conflicting_raw_frame`` guarda los bytes
    recibidos y ``conflicting_sha256`` su huella en hexadecimal minúscula; el
    ``UNIQUE (frame, conflicting_sha256)`` evita registrar dos veces el mismo
    conflicto. Las FK compuestas de ``iot.0008`` exigen que la trama y el
    gateway que reporta el conflicto sean de ``farm``; no se exige que sea el
    mismo gateway que recibió la trama original. Sin secretos.
    """

    frame = models.ForeignKey(ReceivedFrame, on_delete=models.PROTECT, related_name="conflicts")
    gateway = models.ForeignKey(
        Gateway, on_delete=models.PROTECT, related_name="frame_conflicts"
    )
    farm = models.ForeignKey(Farm, on_delete=models.PROTECT, related_name="iot_frame_conflicts")
    conflicting_raw_frame = models.BinaryField(editable=False)
    conflicting_sha256 = models.CharField(max_length=SHA256_HEX_LENGTH)
    server_received_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _AppendOnlyManager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("los conflictos IoT son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("los conflictos IoT no se pueden eliminar")

    class Meta:
        db_table = "iot_frame_conflicts"
        ordering = ["-created_at", "-id"]
        default_manager_name = "objects"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=("frame", "conflicting_sha256"),
                name="iot_fconf_frame_sha_uniq",
            ),
            models.CheckConstraint(
                condition=Q(conflicting_sha256__regex=r"^[0-9a-f]{64}$"),
                name="iot_fconf_sha256_format",
            ),
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length("conflicting_raw_frame"), 1),
                name="iot_fconf_raw_not_empty",
            ),
        ]
        indexes = [
            models.Index(fields=["frame", "farm"], name="iot_fconf_frame_farm_idx"),
            models.Index(fields=["gateway", "farm"], name="iot_fconf_gw_farm_idx"),
        ]


class BrokerIdentityPresenceChoice(models.TextChoices):
    ABSENT = "absent", "Absent"
    SINGLE = "single", "Single"
    MULTIPLE = "multiple", "Multiple"
    INVALID = "invalid", "Invalid"
    OVERSIZED = "oversized", "Oversized"


# bi:<mismatch|postfinal|overflow>:v<version>:<64 hex> | bi:unverifiable:v<version> |
# bi:late | bi:limit | bi:overflowcap  (máx. 84 caracteres). BUILD-FLEPSI-02H
# añade overflow/overflowcap (iot.0020).
DEDUPE_KEY_REGEX = (
    r"^bi:((mismatch|postfinal|overflow):v[0-9]{1,5}:[0-9a-f]{64}"
    r"|unverifiable:v[0-9]{1,5}|late|limit|overflowcap)$"
)


class IotMqttDeliveryState(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    PROCESSING = "PROCESSING", "Processing"
    RETRY_PENDING = "RETRY_PENDING", "Retry pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DUPLICATE = "DUPLICATE", "Duplicate"
    REJECTED = "REJECTED", "Rejected"
    CONFLICT = "CONFLICT", "Conflict"
    DEAD_LETTER = "DEAD_LETTER", "Dead letter"
    RESOLVED = "RESOLVED", "Resolved"


class IotMqttDelivery(models.Model):
    """Durable MQTT transport envelope and its operational lifecycle.

    This model only stores shape and state; the code that owns each concern
    lives in services:

    - encryption and key selection: ``services.mqtt_payload_crypto``;
    - durable receipt and redelivery classification:
      ``services.mqtt_delivery_receipts``;
    - claims, leases and processing: ``services.mqtt_delivery_leases`` and
      ``services.mqtt_delivery_processing``;
    - retries and dead letters: ``services.mqtt_delivery_failures``;
    - manual PUBACK: ``mqtt.consumer``, only after a durable receipt.

    A coordinator FK is only a scheduling candidate; it does not attest that
    the coordinator or message has been authenticated. Authentication happens
    during ingestion and is recorded in ``authenticated_coordinator``.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    transport_key = BoundedBinaryField(max_length=32, unique=True)
    topic = models.CharField(max_length=512)
    qos = models.PositiveSmallIntegerField(default=1)
    received_at = models.DateTimeField()

    payload_ciphertext = models.BinaryField()
    payload_nonce = BoundedBinaryField(max_length=12)
    payload_tag = BoundedBinaryField(max_length=16)
    payload_key_version = models.CharField(max_length=64)
    payload_sha256 = BoundedBinaryField(max_length=32)
    payload_size = models.PositiveIntegerField()

    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="mqtt_deliveries",
        blank=True,
        null=True,
    )
    authenticated_coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="authenticated_mqtt_deliveries",
        blank=True,
        null=True,
    )
    state = models.CharField(
        max_length=20,
        choices=IotMqttDeliveryState.choices,
        default=IotMqttDeliveryState.RECEIVED,
    )
    result_category = models.CharField(max_length=32, blank=True, null=True)
    result_code = models.CharField(max_length=64, blank=True, null=True)
    reason_code = models.CharField(max_length=64, blank=True, null=True)

    attempt_count = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(blank=True, null=True)

    lease_owner = models.CharField(max_length=100, blank=True, null=True)
    lease_token = models.UUIDField(blank=True, null=True)
    lease_expires_at = models.DateTimeField(blank=True, null=True)

    last_error_code = models.CharField(max_length=64, blank=True, null=True)
    last_error_safe = models.CharField(max_length=1000, blank=True, null=True)

    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    resolution_code = models.CharField(max_length=64, blank=True, null=True)

    # BUILD-FLEPSI-02: evidencia de identidad del broker (MQTT 5 ``token_id``).
    # NULL = no capturada (entregas históricas o modo off); nunca se rellena
    # después. Nunca se guarda el ``token_id``: solo su huella HMAC versionada.
    broker_identity_presence = models.CharField(
        max_length=16,
        choices=BrokerIdentityPresenceChoice.choices,
        blank=True,
        null=True,
    )
    broker_token_fingerprint = BoundedBinaryField(max_length=32, blank=True, null=True)
    broker_token_key_version = models.PositiveIntegerField(blank=True, null=True)
    broker_identity_conflict = models.BooleanField(default=False)
    broker_identity_overflow = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "iot_mqtt_deliveries"
        ordering = ["received_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qos=1),
                name="iot_mqtt_delivery_qos_one",
            ),
            models.CheckConstraint(
                condition=Exact(Length("transport_key"), 32),
                name="iot_mqtt_tkey_len_ck",
            ),
            models.CheckConstraint(
                condition=Exact(Length("payload_nonce"), 12),
                name="iot_mqtt_nonce_len_ck",
            ),
            models.CheckConstraint(
                condition=Exact(Length("payload_tag"), 16),
                name="iot_mqtt_tag_len_ck",
            ),
            models.CheckConstraint(
                condition=Exact(Length("payload_sha256"), 32),
                name="iot_mqtt_sha_len_ck",
            ),
            models.CheckConstraint(
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
            models.CheckConstraint(
                condition=Q(broker_token_fingerprint__isnull=True)
                | Exact(Length("broker_token_fingerprint"), 32),
                name="iot_mqtt_broker_fp_len_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["state", "next_attempt_at", "received_at"],
                name="iot_mqtt_state_next_idx",
            ),
            models.Index(
                fields=["lease_expires_at"],
                name="iot_mqtt_lease_exp_idx",
            ),
            models.Index(
                fields=["coordinator", "received_at", "id"],
                name="iot_mqtt_coord_rx_idx",
            ),
            models.Index(
                fields=["resolved_at"],
                name="iot_mqtt_resolved_idx",
            ),
        ]


class IotMqttDeliveryEvent(models.Model):
    """Append-only operational event associated with an MQTT delivery."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    delivery = models.ForeignKey(
        IotMqttDelivery,
        on_delete=models.CASCADE,
        related_name="events",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=40)
    from_state = models.CharField(
        max_length=20,
        choices=IotMqttDeliveryState.choices,
        blank=True,
        null=True,
    )
    to_state = models.CharField(
        max_length=20,
        choices=IotMqttDeliveryState.choices,
        blank=True,
        null=True,
    )
    attempt_number = models.PositiveSmallIntegerField(blank=True, null=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    worker_id = models.CharField(max_length=100, blank=True, null=True)
    lease_token = models.UUIDField(blank=True, null=True)
    result_code = models.CharField(max_length=64, blank=True, null=True)
    safe_detail = models.CharField(max_length=1000, blank=True, null=True)
    correlation_id = models.UUIDField(blank=True, null=True)
    # BUILD-FLEPSI-02: idempotencia de eventos de identidad del broker.
    # NULL para todos los demás eventos (y los históricos).
    dedupe_key = models.CharField(
        max_length=96, blank=True, null=True, db_collation="ascii_bin"
    )

    objects = _AppendOnlyManager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("los eventos de entrega MQTT son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("los eventos de entrega MQTT no se pueden eliminar")

    class Meta:
        db_table = "iot_mqtt_delivery_events"
        ordering = ["occurred_at", "id"]
        default_manager_name = "objects"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["delivery", "dedupe_key"],
                name="iot_mqtt_event_dedupe_uniq",
            ),
            models.CheckConstraint(
                condition=Q(dedupe_key__isnull=True)
                | Q(dedupe_key__regex=DEDUPE_KEY_REGEX),
                name="iot_mqtt_event_dedupe_v2_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["delivery", "occurred_at"],
                name="iot_mqtt_event_time_idx",
            ),
        ]


class IotMqttDeliveryPause(models.Model):
    """Durable global or per-coordinator pause for MQTT delivery processing."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    coordinator = models.ForeignKey(
        Coordinator,
        on_delete=models.PROTECT,
        related_name="mqtt_delivery_pauses",
        blank=True,
        null=True,
    )
    reason_code = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    active_scope_key = models.CharField(max_length=64, unique=True, blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    started_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    started_process_id = models.CharField(max_length=100, blank=True, null=True)
    resumed_at = models.DateTimeField(blank=True, null=True)
    resumed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    resumed_process_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "iot_mqtt_delivery_pauses"
        ordering = ["-started_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        active_scope_key__isnull=False,
                        resumed_at__isnull=True,
                    )
                    | models.Q(
                        is_active=False,
                        active_scope_key__isnull=True,
                        resumed_at__isnull=False,
                    )
                ),
                name="iot_mqtt_pause_lifecycle_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["coordinator", "is_active"],
                name="iot_mqtt_pause_coord_idx",
            ),
        ]


class IotMqttDeliveryPauseEvent(models.Model):
    """Append-only audit history for durable MQTT delivery pauses."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    pause = models.ForeignKey(
        IotMqttDeliveryPause,
        on_delete=models.PROTECT,
        related_name="events",
    )
    event_type = models.CharField(max_length=16)
    occurred_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
    )
    process_id = models.CharField(max_length=100, blank=True, null=True)
    reason_code = models.CharField(max_length=64)

    objects = _AppendOnlyManager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("los eventos de pausa MQTT son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("los eventos de pausa MQTT no se pueden eliminar")

    class Meta:
        db_table = "iot_mqtt_delivery_pause_events"
        ordering = ["occurred_at", "id"]
        default_manager_name = "objects"
        base_manager_name = "objects"
        indexes = [
            models.Index(
                fields=["pause", "occurred_at"],
                name="iot_mqtt_pause_event_idx",
            ),
        ]


class BrokerIdentityRevocationCause(models.TextChoices):
    ROTATION = "rotation", "Rotation"
    COMPROMISE = "compromise", "Compromise"
    DECOMMISSION = "decommission", "Decommission"
    ADMIN = "admin", "Administrative"


class BrokerIdentity(models.Model):
    """Identidad esperada de un gateway en el broker (BUILD-FLEPSI-02).

    No guarda el token ni su ``token_id``: solo huellas por versión de clave
    (``BrokerIdentityFingerprint``). Un único slot activo por gateway y
    proveedor; la revocación conserva fechas, proceso y causa.
    """

    id = models.BigAutoField(primary_key=True)
    gateway = models.ForeignKey(
        Gateway, on_delete=models.PROTECT, related_name="broker_identities"
    )
    provider = models.CharField(max_length=16, default="flespi")
    active_slot = models.PositiveSmallIntegerField(blank=True, null=True, default=ACTIVE_SLOT)
    registered_at = models.DateTimeField()
    registered_process_id = models.CharField(max_length=100)
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_process_id = models.CharField(max_length=100, blank=True, null=True)
    revocation_cause = models.CharField(
        max_length=16,
        choices=BrokerIdentityRevocationCause.choices,
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "iot_broker_identities"
        ordering = ["-registered_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["gateway", "provider", "active_slot"],
                name="iot_bid_active_slot_uniq",
            ),
            models.CheckConstraint(
                condition=Q(provider="flespi"),
                name="iot_bid_provider_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        active_slot=ACTIVE_SLOT,
                        revoked_at__isnull=True,
                        revoked_process_id__isnull=True,
                        revocation_cause__isnull=True,
                    )
                    | Q(
                        active_slot__isnull=True,
                        revoked_at__isnull=False,
                        revoked_process_id__isnull=False,
                        revocation_cause__in=BrokerIdentityRevocationCause.values,
                    )
                ),
                name="iot_bid_state_ck",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True)
                | Q(revoked_at__gte=F("registered_at")),
                name="iot_bid_revoked_after_reg_ck",
            ),
        ]

    @property
    def is_active(self) -> bool:
        return self.active_slot == ACTIVE_SLOT


class BrokerIdentityFingerprint(models.Model):
    """Huella HMAC de una identidad para una versión de clave. Append-only.

    ``UNIQUE(provider, key_version, fingerprint)`` impide asociar el mismo
    token a dos identidades en esa versión, incluso tras revocar.
    """

    id = models.BigAutoField(primary_key=True)
    identity = models.ForeignKey(
        BrokerIdentity, on_delete=models.PROTECT, related_name="fingerprints"
    )
    provider = models.CharField(max_length=16, default="flespi")
    key_version = models.PositiveIntegerField()
    fingerprint = BoundedBinaryField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    created_process_id = models.CharField(max_length=100)

    objects = _AppendOnlyManager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("las huellas de identidad del broker son inmutables")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("las huellas de identidad del broker no se pueden eliminar")

    class Meta:
        db_table = "iot_broker_identity_fingerprints"
        ordering = ["key_version", "id"]
        default_manager_name = "objects"
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["identity", "key_version"],
                name="iot_bidfp_identity_version_uniq",
            ),
            models.UniqueConstraint(
                fields=["provider", "key_version", "fingerprint"],
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
                condition=Q(provider="flespi"),
                name="iot_bidfp_provider_ck",
            ),
        ]


# --- FLESPI-01: credenciales emitidas por un proveedor MQTT externo ---------

BROKER_PROVIDER_FLESPI = "flespi"
BROKER_PROCESS_ID_MAX_LENGTH = 100
BROKER_STATE_MAX_LENGTH = 16
BROKER_CAUSE_MAX_LENGTH = 20
BROKER_ORIGIN_MAX_LENGTH = 16
PENDING_SLOT = 1


class BrokerCredentialState(models.TextChoices):
    PENDING_BROKER = "pending_broker", "Pendiente del proveedor"
    ACTIVE = "active", "Activa"
    REVOKED = "revoked", "Revocada"


class BrokerCredentialRevocationCause(models.TextChoices):
    ADMIN = "admin", "Revocación administrativa"
    COMPROMISED = "compromised", "Comprometida"
    ROTATED = "rotated", "Reemplazada al finalizar la rotación"
    ABANDONED = "abandoned", "Rotación abandonada"
    GATEWAY_DEACTIVATED = "gateway_deactivated", "Gateway desactivado"


class BrokerConfirmationOrigin(models.TextChoices):
    PROVIDER = "provider", "Proveedor registrado"
    TEST_DOUBLE = "test_double", "Doble de pruebas"


class BrokerCredential(models.Model):
    """Credencial MQTT de un gateway emitida por un proveedor externo (FLESPI-01).

    Django es la autoridad del estado; el proveedor solo confirma. Estados:
    ``pending_broker`` -> ``active`` -> ``revoked`` (o ``pending_broker`` ->
    ``revoked``). ``revoked`` es terminal: una credencial nueva es otra fila con
    ``version`` mayor.

    - Coexistencia (ADR-FLESPI-00): como mucho una pendiente
      (``pending_slot``) y una activa (``active_slot``) por gateway y
      proveedor; MariaDB no tiene índices únicos parciales y admite varios NULL.
    - Sin secretos: ``secret_fingerprint`` es la huella de dominio
      ``agrotech:gw-broker-cred:v1``. No se guarda el ``token_id`` del
      proveedor (política de BUILD-FLESPI-02); ``correlation_id`` es un UUID
      propio, público y no secreto.
    - Sin ``farm``: la finca se obtiene del gateway, nunca del cliente.
    - Independiente de ``GatewayCredential`` (la ingesta sigue exigiéndola) y
      de ``BrokerIdentity`` (observación del ``token_id`` en MQTT).
    """

    id = models.BigAutoField(primary_key=True)
    gateway = models.ForeignKey(
        Gateway, on_delete=models.PROTECT, related_name="broker_credentials"
    )
    provider = models.CharField(max_length=16, default=BROKER_PROVIDER_FLESPI)
    version = models.PositiveIntegerField()
    correlation_id = models.UUIDField(unique=True, editable=False)
    state = models.CharField(
        max_length=BROKER_STATE_MAX_LENGTH,
        choices=BrokerCredentialState.choices,
        default=BrokerCredentialState.PENDING_BROKER,
    )
    pending_slot = models.PositiveSmallIntegerField(blank=True, null=True, default=PENDING_SLOT)
    active_slot = models.PositiveSmallIntegerField(blank=True, null=True)
    created_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="+", blank=True, null=True
    )
    # Confirmación del proveedor: todos NULL o todos con valor.
    confirmation_ref = models.UUIDField(blank=True, null=True, unique=True)
    confirmation_origin = models.CharField(
        max_length=BROKER_ORIGIN_MAX_LENGTH,
        choices=BrokerConfirmationOrigin.choices,
        blank=True,
        null=True,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True)
    confirmed_process_id = models.CharField(
        max_length=BROKER_PROCESS_ID_MAX_LENGTH, blank=True, null=True
    )
    secret_fingerprint = models.CharField(
        max_length=FINGERPRINT_MAX_LENGTH, blank=True, null=True, unique=True
    )
    key_version = models.PositiveSmallIntegerField(blank=True, null=True)
    # Activación: automática (proceso, primera credencial) o al finalizar una
    # rotación (administrador + evidencia de uso de la credencial nueva).
    activated_at = models.DateTimeField(blank=True, null=True)
    activated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="+", blank=True, null=True
    )
    activated_process_id = models.CharField(
        max_length=BROKER_PROCESS_ID_MAX_LENGTH, blank=True, null=True
    )
    activation_evidence_ref = models.UUIDField(blank=True, null=True, unique=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="+", blank=True, null=True
    )
    revocation_cause = models.CharField(
        max_length=BROKER_CAUSE_MAX_LENGTH,
        choices=BrokerCredentialRevocationCause.choices,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "iot_broker_credentials"
        ordering = ["-version", "-id"]
        constraints = [
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
                condition=Q(provider=BROKER_PROVIDER_FLESPI),
                name="iot_bcred_provider_ck",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="iot_bcred_version_ck",
            ),
            # ``isnull`` explícito: en SQL un CHECK con resultado NULL se
            # considera cumplido.
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
                        confirmation_origin__in=BrokerConfirmationOrigin.values,
                        confirmed_at__isnull=False,
                        confirmed_process_id__isnull=False,
                        secret_fingerprint__isnull=False,
                        secret_fingerprint__regex=r"^[0-9a-f]{64}$",  # noqa: S106 - formato
                        key_version__isnull=False,
                        key_version__gte=1,
                    )
                ),
                name="iot_bcred_confirmation_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state=BrokerCredentialState.PENDING_BROKER,
                        pending_slot__isnull=False,
                        pending_slot=PENDING_SLOT,
                        active_slot__isnull=True,
                        activated_at__isnull=True,
                        activated_process_id__isnull=True,
                        activation_evidence_ref__isnull=True,
                        revoked_at__isnull=True,
                        revocation_cause="",
                    )
                    | Q(
                        state=BrokerCredentialState.ACTIVE,
                        pending_slot__isnull=True,
                        active_slot__isnull=False,
                        active_slot=ACTIVE_SLOT,
                        activated_at__isnull=False,
                        confirmed_at__isnull=False,
                        revoked_at__isnull=True,
                        revocation_cause="",
                    )
                    | Q(
                        state=BrokerCredentialState.REVOKED,
                        pending_slot__isnull=True,
                        active_slot__isnull=True,
                        revoked_at__isnull=False,
                        revocation_cause__in=BrokerCredentialRevocationCause.values,
                    )
                ),
                name="iot_bcred_state_ck",
            ),
            # ``rotated`` solo para una credencial que estuvo activa;
            # ``abandoned`` solo para una que nunca se activó.
            models.CheckConstraint(
                condition=(
                    (
                        ~Q(revocation_cause=BrokerCredentialRevocationCause.ROTATED)
                        | Q(activated_at__isnull=False)
                    )
                    & (
                        ~Q(revocation_cause=BrokerCredentialRevocationCause.ABANDONED)
                        | Q(activated_at__isnull=True)
                    )
                ),
                name="iot_bcred_cause_ck",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(confirmed_at__isnull=True) | Q(confirmed_at__gte=F("created_at")))
                    & (Q(activated_at__isnull=True) | Q(activated_at__gte=F("confirmed_at")))
                    & (Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("created_at")))
                ),
                name="iot_bcred_dates_ck",
            ),
        ]

    @property
    def is_pending(self) -> bool:
        return self.state == BrokerCredentialState.PENDING_BROKER

    @property
    def is_active(self) -> bool:
        return self.state == BrokerCredentialState.ACTIVE

    @property
    def is_revoked(self) -> bool:
        return self.state == BrokerCredentialState.REVOKED

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_ref is not None
