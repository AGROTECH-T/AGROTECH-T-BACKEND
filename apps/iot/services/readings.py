"""Consulta de solo lectura de mediciones IoT (BUILD E3.1).

Propósito: exponer el historial de mediciones de una finca con autorización de
lectura, filtros validados y un intervalo temporal acotado.

Reglas:
- La finca y la membresía se validan con ``require_read_membership``: el
  ``farm_id`` recibido del cliente nunca autoriza por sí solo.
- Un ``sensor_uid`` debe existir en la misma finca; un sensor de otra finca
  responde 404.
- El intervalo efectivo nunca es abierto: si faltan fechas se usa una ventana
  predeterminada y, en cualquier caso, no puede superar el máximo aprobado.
- Las fechas son ISO-8601 con zona horaria; la inicial no puede ser posterior
  a la final.
- Solo se ordena por los campos de ``ORDERINGS``.
- La consulta es de solo lectura: devuelve un ``QuerySet`` y no escribe nada.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db.models import Prefetch

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import Measurement, MeasurementCorrection, Sensor
from apps.iot.services._common import parse_uid
from apps.iot.services.access import require_read_membership

DEFAULT_WINDOW = timedelta(days=7)
MAX_WINDOW = timedelta(days=31)

INVALID_DATE = "fecha no válida; use ISO-8601 con zona horaria"
INVALID_RANGE = "la fecha inicial no puede ser posterior a la final"
RANGE_TOO_LARGE = "el intervalo de consulta no puede superar los 31 días"
INVALID_ORDERING = "ordenamiento no permitido"
SENSOR_NOT_FOUND = "sensor no encontrado"

DEFAULT_ORDERING = "-measured_at"
ORDERINGS = {
    "measured_at": ("measured_at", "id"),
    "-measured_at": ("-measured_at", "-id"),
    "server_received_at": ("frame__server_received_at", "id"),
    "-server_received_at": ("frame__server_received_at", "-id"),
}


def list_readings(
    *,
    user: User,
    farm_id: int,
    sensor_uid=None,
    date_from=None,
    date_to=None,
    ordering=None,
    now=None,
):
    """Mediciones de la finca que cumplen los filtros autorizados.

    Args:
        user: Cuenta autenticada; debe tener membresía activa en la finca.
        farm_id: Finca consultada; limita todas las filas.
        sensor_uid: Identificador público opcional del sensor de la finca.
        date_from: Inicio ISO-8601 con zona horaria (inclusive).
        date_to: Fin ISO-8601 con zona horaria (inclusive).
        ordering: Uno de ``ORDERINGS``; por defecto ``-measured_at``.
        now: Reloj de referencia para la ventana predeterminada (pruebas).

    Returns:
        ``QuerySet`` de ``Measurement`` de la finca, ordenado y sin efectos
        secundarios.

    Raises:
        AppError: 404 si no hay membresía, el sensor no existe o es de otra
            finca; 400 si una fecha, el intervalo o el ordenamiento no son
            válidos.
    """
    require_read_membership(user=user, farm_id=farm_id)
    queryset = Measurement.objects.filter(farm_id=farm_id).select_related(
        "sensor", "frame"
    ).prefetch_related(
        Prefetch(
            "corrections",
            queryset=MeasurementCorrection.objects.select_related("created_by").order_by("revision"),
            to_attr="_prefetched_corrections",
        )
    )

    if sensor_uid not in (None, ""):
        uid = parse_uid(sensor_uid, IdentifierKind.SENSOR, SENSOR_NOT_FOUND)
        if not Sensor.objects.filter(farm_id=farm_id, uid=uid).exists():
            raise AppError(SENSOR_NOT_FOUND, 404)
        queryset = queryset.filter(sensor__uid=uid)

    start, end = _window(date_from=date_from, date_to=date_to, now=now)
    queryset = queryset.filter(measured_at__gte=start, measured_at__lte=end)
    return queryset.order_by(*_ordering(ordering))


def _window(*, date_from, date_to, now=None):
    """Calcula el intervalo efectivo y lo acota a la política aprobada."""
    reference = now or timezone.now()
    start = _parse_moment(date_from)
    end = _parse_moment(date_to)
    if end is None:
        end = reference
    if start is None:
        start = end - DEFAULT_WINDOW
    if start > end:
        raise AppError(INVALID_RANGE, 400)
    if end - start > MAX_WINDOW:
        raise AppError(RANGE_TOO_LARGE, 400)
    return start, end


def _parse_moment(value):
    """Convierte una fecha ISO-8601 con zona horaria o rechaza la entrada."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AppError(INVALID_DATE, 400)
    try:
        moment = parse_datetime(value)
    except (TypeError, ValueError):
        moment = None
    if moment is None or timezone.is_naive(moment):
        raise AppError(INVALID_DATE, 400)
    return moment


def _ordering(value):
    """Valida el ordenamiento contra la lista cerrada de campos permitidos."""
    key = (value or "").strip() or DEFAULT_ORDERING
    if key not in ORDERINGS:
        raise AppError(INVALID_ORDERING, 400)
    return ORDERINGS[key]
