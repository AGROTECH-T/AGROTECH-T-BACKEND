"""Galpones y lotes de pollo de engorde.

Propósito: guardar el ciclo, la bitácora y los avisos de cada galpón.
Contexto: la postura queda fuera; las cifras de una finca no son regla universal.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from django.db import models

from apps.accounts.models import User
from apps.farms.models import Farm


class HouseStatus(models.TextChoices):
    """Si el galpón tiene un lote abierto o está libre."""

    DISPONIBLE = "disponible", "Disponible"
    OCUPADO = "ocupado", "Ocupado"


class FeedMode(models.TextChoices):
    """Cómo se entrega el alimento en ese galpón."""

    VOLUNTAD = "voluntad", "A voluntad"
    MEDIDO = "medido", "Medido"


class House(models.Model):
    """Galpón de una finca, con medidas y límites propios."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="houses")
    name = models.CharField(max_length=80)
    location = models.CharField(max_length=120, blank=True, default="")
    length_m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    width_m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    usual_birds = models.PositiveIntegerField(null=True, blank=True)
    equipment = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=HouseStatus.choices,
        default=HouseStatus.DISPONIBLE,
    )
    bale_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    bales_per_drum = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feed_mode = models.CharField(
        max_length=12,
        choices=FeedMode.choices,
        blank=True,
        default="",
    )
    cycle_days = models.PositiveIntegerField(null=True, blank=True)
    mortality_alert = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "avi_houses"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=("farm", "name"), name="uniq_avi_house_name"),
            models.CheckConstraint(
                condition=models.Q(status__in=HouseStatus.values),
                name="avi_house_status_valid",
            ),
        ]

    @property
    def area_m2(self):
        """Área útil a partir del largo y el ancho, si ambos se conocen."""
        if self.length_m is None or self.width_m is None:
            return None
        return (self.length_m * self.width_m).quantize(Decimal("0.01"))


class LotStage(models.TextChoices):
    """Etapa del alimento y del ciclo."""

    INICIO = "inicio", "Inicio"
    LEVANTE = "levante", "Levante"
    ENGORDE = "engorde", "Engorde"


class LotStatus(models.TextChoices):
    """Si el ciclo sigue abierto."""

    ABIERTO = "abierto", "Abierto"
    CERRADO = "cerrado", "Cerrado"


class Lot(models.Model):
    """Un ciclo de engorde dentro de un galpón."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="lots")
    house = models.ForeignKey(House, on_delete=models.PROTECT, related_name="lots")
    code = models.CharField(max_length=40)
    arrived_at = models.DateTimeField()
    received = models.PositiveIntegerField()
    dead_on_arrival = models.PositiveIntegerField(default=0)
    discarded_on_arrival = models.PositiveIntegerField(default=0)
    live_count = models.PositiveIntegerField()
    supplier = models.CharField(max_length=120, blank=True, default="")
    documents = models.CharField(max_length=200, blank=True, default="")
    initial_age_days = models.PositiveIntegerField(default=0)
    initial_weight = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
    )
    stage = models.CharField(max_length=12, choices=LotStage.choices, default=LotStage.INICIO)
    levante_on = models.DateField(null=True, blank=True)
    engorde_on = models.DateField(null=True, blank=True)
    feed_type = models.CharField(max_length=80, blank=True, default="")
    reception_notes = models.TextField(blank=True, default="")
    receiver_name = models.CharField(max_length=80)
    status = models.CharField(
        max_length=12,
        choices=LotStatus.choices,
        default=LotStatus.ABIERTO,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    close_summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="avi_lots")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "avi_lots"
        ordering = ["-arrived_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=("farm", "code"), name="uniq_avi_lot_code"),
            models.CheckConstraint(
                condition=models.Q(status__in=LotStatus.values),
                name="avi_lot_status_valid",
            ),
        ]


class EventKind(models.TextChoices):
    """Tipos de la bitácora. Mortalidad, descarte, aislamiento y salida van aparte."""

    RONDA = "ronda", "Ronda"
    MORTALIDAD = "mortalidad", "Mortalidad"
    DESCARTE = "descarte", "Descarte"
    AISLAMIENTO = "aislamiento", "Aislamiento"
    SALIDA = "salida", "Salida"
    PESO = "peso", "Peso"
    ALIMENTO = "alimento", "Alimento"
    AGUA = "agua", "Agua"
    AMBIENTE = "ambiente", "Ambiente y cama observada"
    CAMA = "cama", "Manejo de cama"
    SANIDAD = "sanidad", "Sanidad"
    LIMPIEZA = "limpieza", "Limpieza y bioseguridad"
    AJUSTE = "ajuste", "Ajuste de saldo"


class EventOrigin(models.TextChoices):
    """De dónde salió el dato."""

    MANUAL = "manual", "Observación manual"
    MEDICION = "medicion", "Medición"
    SENSOR = "sensor", "Sensor"
    AJUSTE = "ajuste", "Ajuste"


class AttentionLevel(models.TextChoices):
    """Qué tan pronto hay que mirarlo."""

    INFO = "informativo", "Informativo"
    REVISAR = "revisar", "Por revisar"
    URGENTE = "urgente", "Urgente"


class AttentionState(models.TextChoices):
    """Seguimiento del evento."""

    ABIERTO = "abierto", "Abierto"
    ATENCION = "atencion", "En atención"
    RESUELTO = "resuelto", "Resuelto"


class LotEvent(models.Model):
    """Un hecho de la bitácora: qué se vio, qué se hizo y cómo quedó."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=EventKind.choices)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    responsible = models.CharField(max_length=80)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="avi_events")
    observed_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True, default="")
    place = models.CharField(max_length=80, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    origin = models.CharField(
        max_length=12,
        choices=EventOrigin.choices,
        default=EventOrigin.MANUAL,
    )
    attention = models.CharField(
        max_length=16,
        choices=AttentionLevel.choices,
        default=AttentionLevel.INFO,
    )
    state = models.CharField(
        max_length=12,
        choices=AttentionState.choices,
        default=AttentionState.ABIERTO,
    )
    action = models.CharField(max_length=200, blank=True, default="")
    assignee = models.CharField(max_length=80, blank=True, default="")
    follow_up = models.DateField(null=True, blank=True)
    estimated = models.BooleanField(default=False)
    confirmed = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "avi_events"
        ordering = ["-occurred_at", "-id"]


class EventCorrection(models.Model):
    """Conserva el dato anterior cuando alguien corrige un evento."""

    event = models.ForeignKey(LotEvent, on_delete=models.CASCADE, related_name="corrections")
    previous = models.JSONField()
    reason = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="avi_corrections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "avi_event_corrections"
        ordering = ["-created_at", "-id"]


class AlertState(models.TextChoices):
    """Si el aviso sigue pendiente."""

    ABIERTA = "abierta", "Abierta"
    CERRADA = "cerrada", "Cerrada"


class LotAlert(models.Model):
    """Aviso para atender. No diagnostica ni receta."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="alerts")
    house = models.ForeignKey(House, on_delete=models.CASCADE, related_name="alerts")
    motive = models.CharField(max_length=200)
    assignee = models.CharField(max_length=80, blank=True, default="")
    state = models.CharField(
        max_length=12,
        choices=AlertState.choices,
        default=AlertState.ABIERTA,
    )
    follow_up = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "avi_alerts"
        ordering = ["-created_at", "-id"]
