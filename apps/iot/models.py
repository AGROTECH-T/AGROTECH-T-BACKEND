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
from django.db import models
from django.db.models import F, Q

from apps.accounts.models import User
from apps.farms.models import Farm
from apps.iot.catalog import MetricCode
from apps.iot.identifiers import IDENTIFIER_ALPHABET, RANDOM_PART_LENGTH, IdentifierKind

NAME_MAX_LENGTH = 100
LOCATION_LABEL_MAX_LENGTH = 100
UID_MAX_LENGTH = 13
METRIC_MAX_LENGTH = 40
ACTIVE_PAIR = 1


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


class Sensor(_Lifecycle):
    """Sensor físico conectado a un canal del coordinador.

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
        ]
        indexes = [
            models.Index(fields=["coordinator", "farm"], name="iot_sensor_coord_farm_idx"),
            models.Index(fields=["farm", "is_active"], name="iot_sn_farm_active_idx"),
        ]


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
        ]
        indexes = [
            models.Index(fields=["coordinator", "farm"], name="iot_cg_coord_farm_idx"),
            models.Index(fields=["gateway", "farm"], name="iot_cg_gw_farm_idx"),
        ]

    @property
    def is_active(self) -> bool:
        """La autorización está vigente."""
        return self.active_pair == ACTIVE_PAIR
