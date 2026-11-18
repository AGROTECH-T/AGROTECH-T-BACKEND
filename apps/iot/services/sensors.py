"""Sensores IoT (IoT-02.2, DEC-13).

Propósito: registrar, consultar, desactivar y reactivar sensores conectados a
un coordinador.

Reglas:
- La métrica debe existir en ``apps.iot.catalog``; la unidad se deriva de ella.
- Un canal solo puede tener un sensor activo por coordinador (lo garantiza
  ``UNIQUE (coordinator, active_channel)`` en la base).
- Crear o reactivar exige el coordinador activo. Reactivar exige motivo y una
  configuración válida (métrica vigente y canal libre).
- Orden de bloqueo: Farm → FarmMembership → Coordinator → Sensor.
- El canal no tiene máximo funcional (pendiente hasta conocer el cableado y el
  firmware): se valida contra el rango del tipo de columna en el motor activo,
  que informa Django (``connection.ops.integer_field_range``). En MariaDB,
  ``PositiveSmallIntegerField`` es ``smallint UNSIGNED``: 0 a 65535.
"""
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.catalog import is_known_metric, metric_unit
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import Coordinator, Sensor, SensorSource
from apps.iot.services._common import (
    audit,
    clean_reason,
    create_with_uid,
    lock_by_uid,
    parse_uid,
    required_reason,
)
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType

ENTITY_TYPE = "iot_sensor"
SENSOR_NOT_FOUND = "sensor no encontrado"
COORDINATOR_NOT_FOUND = "coordinador no encontrado"
INVALID_METRIC = "métrica no válida"
INVALID_CHANNEL = "canal no válido"
CHANNEL_IN_USE = "ya hay un sensor activo en ese canal del coordinador"
COORDINATOR_INACTIVE = "el coordinador está desactivado"
SOURCE_REQUIRED = "la fuente física es obligatoria"
ALREADY_INACTIVE = "el sensor ya está desactivado"
ALREADY_ACTIVE = "el sensor ya está activo"
CHANNEL_FIELD_TYPE = "PositiveSmallIntegerField"


def list_sensors(
    *, user: User, farm_id: int, coordinator_uid=None, include_inactive: bool = False
):
    """Sensores de la finca, opcionalmente de un coordinador."""
    require_read_membership(user=user, farm_id=farm_id)
    queryset = Sensor.objects.filter(farm_id=farm_id).select_related("coordinator", "source")
    if coordinator_uid is not None:
        uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
        queryset = queryset.filter(coordinator__uid=uid)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def get_sensor(*, user: User, farm_id: int, uid: str) -> Sensor:
    """Sensor de la finca o 404."""
    require_read_membership(user=user, farm_id=farm_id)
    uid = parse_uid(uid, IdentifierKind.SENSOR, SENSOR_NOT_FOUND)
    sensor = Sensor.objects.filter(farm_id=farm_id, uid=uid).select_related(
        "coordinator", "source"
    ).first()
    if sensor is None:
        raise AppError(SENSOR_NOT_FOUND, 404)
    return sensor


def sensor_unit(sensor: Sensor) -> str:
    """Unidad del sensor, derivada del catálogo."""
    return metric_unit(sensor.metric)


def create_sensor(
    *, user: User, farm_id: int, coordinator_uid: str, channel, metric, source_uid=None
) -> Sensor:
    """Registra un sensor lógico en un canal libre y fuente física activa."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        coordinator_uid = parse_uid(
            coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND
        )
        clean_channel = _validate_channel(channel)
        if not is_known_metric(metric):
            raise AppError(INVALID_METRIC, 400)
        coordinator = lock_by_uid(
            Coordinator, farm_id=farm_id, uid=coordinator_uid, not_found=COORDINATOR_NOT_FOUND
        )
        if not coordinator.is_active:
            raise AppError(COORDINATOR_INACTIVE, 409)
        if source_uid is None:
            raise AppError(SOURCE_REQUIRED, 400)
        source = _lock_source_for_sensor(
            source_uid=source_uid, coordinator=coordinator, farm_id=membership.farm_id
        )
        try:
            sensor = create_with_uid(
                Sensor,
                IdentifierKind.SENSOR,
                farm_id=membership.farm_id,
                coordinator=coordinator,
                source=source,
                channel=clean_channel,
                metric=metric,
                active_channel=clean_channel,
                created_by=user,
            )
        except IntegrityError:
            raise AppError(CHANNEL_IN_USE, 409) from None
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_SENSOR_CREATED,
            entity_type=ENTITY_TYPE,
            entity_id=sensor.uid,
            after=_snapshot(sensor),
        )
        return sensor


def deactivate_sensor(*, user: User, farm_id: int, uid: str, reason=None) -> Sensor:
    """Desactiva un sensor y libera su canal."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = parse_uid(uid, IdentifierKind.SENSOR, SENSOR_NOT_FOUND)
        clean = clean_reason(reason)
        coordinator = _lock_sensor_coordinator(farm_id=farm_id, uid=uid)
        sensor = _lock_sensor(farm_id=farm_id, uid=uid, coordinator=coordinator)
        if not sensor.is_active:
            raise AppError(ALREADY_INACTIVE, 409)
        _deactivate_locked(
            membership=membership, user=user, sensor=sensor, now=timezone.now(), reason=clean
        )
        return sensor


def reactivate_sensor(*, user: User, farm_id: int, uid: str, reason) -> Sensor:
    """Reactiva un sensor si su coordinador está activo y su canal está libre."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = parse_uid(uid, IdentifierKind.SENSOR, SENSOR_NOT_FOUND)
        clean = required_reason(reason)
        coordinator = _lock_sensor_coordinator(farm_id=farm_id, uid=uid)
        sensor = _lock_sensor(farm_id=farm_id, uid=uid, coordinator=coordinator)
        if sensor.is_active:
            raise AppError(ALREADY_ACTIVE, 409)
        if not coordinator.is_active:
            raise AppError(COORDINATOR_INACTIVE, 409)
        if not is_known_metric(sensor.metric):
            raise AppError(INVALID_METRIC, 409)
        before = _snapshot(sensor)
        sensor.is_active = True
        sensor.active_channel = sensor.channel
        sensor.deactivated_at = None
        sensor.deactivated_by = None
        try:
            with transaction.atomic():
                sensor.save(
                    update_fields=[
                        "is_active",
                        "active_channel",
                        "deactivated_at",
                        "deactivated_by",
                        "updated_at",
                    ]
                )
        except IntegrityError:
            raise AppError(CHANNEL_IN_USE, 409) from None
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_SENSOR_REACTIVATED,
            entity_type=ENTITY_TYPE,
            entity_id=sensor.uid,
            before=before,
            after=_snapshot(sensor),
            reason=clean,
        )
        return sensor


def deactivate_all_locked(*, membership, user, coordinator, now, reason="", cause=None) -> int:
    """Desactiva los sensores activos de un coordinador ya bloqueado (uso interno)."""
    rows = list(
        Sensor.objects.select_for_update()
        .filter(coordinator=coordinator, is_active=True)
        .order_by("pk")
    )
    for sensor in rows:
        _deactivate_locked(
            membership=membership, user=user, sensor=sensor, now=now, reason=reason, cause=cause
        )
    return len(rows)


def _deactivate_locked(*, membership, user, sensor, now, reason="", cause=None) -> None:
    before = _snapshot(sensor)
    sensor.is_active = False
    sensor.active_channel = None
    sensor.deactivated_at = now
    sensor.deactivated_by = user
    sensor.save(
        update_fields=[
            "is_active",
            "active_channel",
            "deactivated_at",
            "deactivated_by",
            "updated_at",
        ]
    )
    audit(
        membership=membership,
        user=user,
        event_type=EventType.IOT_SENSOR_DEACTIVATED,
        entity_type=ENTITY_TYPE,
        entity_id=sensor.uid,
        before=before,
        after=_snapshot(sensor),
        reason=reason,
        metadata={"cause": cause} if cause else None,
    )


def _lock_sensor_coordinator(*, farm_id: int, uid: str) -> Coordinator:
    """Bloquea primero el coordinador del sensor (orden Coordinator → Sensor)."""
    coordinator_id = (
        Sensor.objects.filter(farm_id=farm_id, uid=uid)
        .values_list("coordinator_id", flat=True)
        .first()
    )
    if coordinator_id is None:
        raise AppError(SENSOR_NOT_FOUND, 404)
    return Coordinator.objects.select_for_update().get(pk=coordinator_id, farm_id=farm_id)


def _lock_source_for_sensor(*, source_uid, coordinator: Coordinator, farm_id: int) -> SensorSource:
    """Validate source scope and activity under the coordinator lock."""
    from uuid import UUID

    try:
        uid = source_uid if isinstance(source_uid, UUID) else UUID(str(source_uid))
    except (TypeError, ValueError, AttributeError):
        raise AppError("fuente física no encontrada", 404) from None
    source = SensorSource.objects.select_for_update().filter(
        uid=uid, coordinator=coordinator, farm_id=farm_id
    ).first()
    if source is None:
        raise AppError("fuente física no encontrada", 404)
    if not source.is_active:
        raise AppError("la fuente física está desactivada", 409)
    return source


def _lock_sensor(*, farm_id: int, uid: str, coordinator: Coordinator) -> Sensor:
    sensor = lock_by_uid(Sensor, farm_id=farm_id, uid=uid, not_found=SENSOR_NOT_FOUND)
    sensor.coordinator = coordinator
    return sensor


def channel_range() -> tuple[int, int]:
    """Rango del tipo de columna de ``channel`` en el motor activo.

    No es un límite de hardware: es lo que admite la columna. En MariaDB
    devuelve (0, 65535).
    """
    return connection.ops.integer_field_range(CHANNEL_FIELD_TYPE)


def _validate_channel(channel) -> int:
    if isinstance(channel, bool) or not isinstance(channel, int):
        raise AppError(INVALID_CHANNEL, 400)
    low, high = channel_range()
    if channel < low or channel > high:
        raise AppError(INVALID_CHANNEL, 400)
    return channel


def _snapshot(sensor: Sensor) -> dict:
    return {
        "uid": sensor.uid,
        "coordinator_uid": sensor.coordinator.uid,
        "source_uid": str(sensor.source.uid) if sensor.source_id else None,
        "channel": sensor.channel,
        "metric": sensor.metric,
        "is_active": sensor.is_active,
    }
