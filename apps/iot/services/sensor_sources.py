"""Domain operations for physical IoT sensor sources (IOT-SOURCES-01)."""

import logging
from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.models import Coordinator, Sensor, SensorSource, SensorSourceRole
from apps.iot.services import sensors as sensor_services
from apps.iot.services._common import audit, clean_reason, lock_by_uid, parse_uid, required_text
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.iot.identifiers import IdentifierKind
from apps.trazabilidad.models import EventType

SOURCE_NOT_FOUND = "fuente física no encontrada"
COORDINATOR_NOT_FOUND = "coordinador no encontrado"
COORDINATOR_INACTIVE = "el coordinador está desactivado"
SOURCE_INACTIVE = "la fuente física está desactivada"
SOURCE_ALREADY_INACTIVE = "la fuente física ya está desactivada"
SOURCE_ALREADY_ACTIVE = "la fuente física ya está activa"
INVALID_SOURCE_UID = "fuente física no encontrada"
INVALID_ROLE = "rol de fuente no válido"
INVALID_OPTIONAL_TEXT = "texto de fuente no válido"
SOURCE_HAS_MEASUREMENTS = "la identidad de la fuente no se puede cambiar después de medir"

logger = logging.getLogger(__name__)

_UNSET = object()
_IDENTITY_FIELDS = ("name", "model", "manufacturer", "role")


def create_sensor_source(
    *,
    user: User,
    farm_id: int,
    coordinator_uid: str,
    name,
    role,
    model=None,
    manufacturer=None,
    description="",
) -> SensorSource:
    """Create a physical source in an active coordinator of an authorized farm."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        coordinator_uid = parse_uid(
            coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND
        )
        clean_name = required_text(name, "el nombre de la fuente es obligatorio", 100)
        clean_role = _validate_role(role)
        clean_model = _optional_text(model, 80)
        clean_manufacturer = _optional_text(manufacturer, 100)
        clean_description = _description(description)
        coordinator = lock_by_uid(
            Coordinator,
            farm_id=membership.farm_id,
            uid=coordinator_uid,
            not_found=COORDINATOR_NOT_FOUND,
        )
        if not coordinator.is_active:
            raise AppError(COORDINATOR_INACTIVE, 409)
        source = SensorSource.objects.create(
            farm_id=membership.farm_id,
            coordinator=coordinator,
            name=clean_name,
            model=clean_model,
            manufacturer=clean_manufacturer,
            role=clean_role,
            description=clean_description,
            created_by=user,
        )
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_SENSOR_CREATED,
            entity_type="iot_sensor_source",
            entity_id=source.uid,
            after=_snapshot(source),
            metadata={"resource": "sensor_source"},
        )
        return source


def list_sensor_sources(
    *, user: User, farm_id: int, coordinator_uid=None, include_inactive: bool = False
):
    """List only physical sources in the farm where the user is a member."""
    require_read_membership(user=user, farm_id=farm_id)
    queryset = SensorSource.objects.filter(farm_id=farm_id).select_related("coordinator")
    if coordinator_uid is not None:
        uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
        queryset = queryset.filter(coordinator__uid=uid)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def update_sensor_source(
    *,
    user: User,
    farm_id: int,
    source_uid,
    name=_UNSET,
    model=_UNSET,
    manufacturer=_UNSET,
    role=_UNSET,
    description=_UNSET,
) -> SensorSource:
    """Update source metadata with row locking and a local change snapshot."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        source, _coordinator = _lock_source(
            farm_id=membership.farm_id, source_uid=source_uid
        )
        if not source.is_active:
            raise AppError(SOURCE_INACTIVE, 409)

        changes = {}
        if name is not _UNSET:
            changes["name"] = required_text(name, "el nombre de la fuente es obligatorio", 100)
        if model is not _UNSET:
            changes["model"] = _optional_text(model, 80)
        if manufacturer is not _UNSET:
            changes["manufacturer"] = _optional_text(manufacturer, 100)
        if role is not _UNSET:
            changes["role"] = _validate_role(role)
        if description is not _UNSET:
            changes["description"] = _description(description)

        before = _snapshot(source)
        has_measurements = Sensor.objects.filter(
            source=source, measurements__isnull=False
        ).exists()
        if has_measurements and any(
            field in changes and changes[field] != getattr(source, field)
            for field in _IDENTITY_FIELDS
        ):
            raise AppError(SOURCE_HAS_MEASUREMENTS, 409)
        after_values = {**before, **changes}
        if after_values == before:
            return source
        for field, value in changes.items():
            setattr(source, field, value)
        with _source_change_actor(user.pk):
            source.save(update_fields=[*changes, "updated_at"])
        return source


def deactivate_sensor_source(*, user: User, farm_id: int, source_uid, reason=None) -> SensorSource:
    """Deactivate a source and its active logical sensors atomically."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        source, _coordinator = _lock_source(farm_id=membership.farm_id, source_uid=source_uid)
        if not source.is_active:
            raise AppError(SOURCE_ALREADY_INACTIVE, 409)
        clean = clean_reason(reason)
        now = timezone.now()
        children = list(
            Sensor.objects.select_for_update()
            .filter(source=source, is_active=True)
            .order_by("pk")
        )
        for sensor in children:
            sensor_services._deactivate_locked(
                membership=membership,
                user=user,
                sensor=sensor,
                now=now,
                reason=clean,
                cause="sensor_source_deactivated",
            )
        before = _snapshot(source)
        source.is_active = False
        source.deactivated_at = now
        source.deactivated_by = user
        source.save(
            update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"]
        )
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_SENSOR_DEACTIVATED,
            entity_type="iot_sensor_source",
            entity_id=source.uid,
            before=before,
            after=_snapshot(source),
            reason=clean,
            metadata={"resource": "sensor_source", "deactivated_sensors": len(children)},
        )
        return source


def reactivate_sensor_source(*, user: User, farm_id: int, source_uid, reason) -> SensorSource:
    """Reactivate only the source; its logical sensors remain inactive."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        source, coordinator = _lock_source(farm_id=membership.farm_id, source_uid=source_uid)
        if source.is_active:
            raise AppError(SOURCE_ALREADY_ACTIVE, 409)
        if not coordinator.is_active:
            raise AppError(COORDINATOR_INACTIVE, 409)
        clean = clean_reason(reason)
        before = _snapshot(source)
        source.is_active = True
        source.deactivated_at = None
        source.deactivated_by = None
        source.save(
            update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"]
        )
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_SENSOR_REACTIVATED,
            entity_type="iot_sensor_source",
            entity_id=source.uid,
            before=before,
            after=_snapshot(source),
            reason=clean,
            metadata={"resource": "sensor_source", "sensors_reactivated": 0},
        )
        return source


def _lock_source(*, farm_id: int, source_uid) -> tuple[SensorSource, Coordinator]:
    uid = _parse_source_uid(source_uid)
    coordinator_id = (
        SensorSource.objects.filter(farm_id=farm_id, uid=uid)
        .values_list("coordinator_id", flat=True)
        .first()
    )
    if coordinator_id is None:
        raise AppError(SOURCE_NOT_FOUND, 404)
    coordinator = Coordinator.objects.select_for_update().filter(
        pk=coordinator_id, farm_id=farm_id
    ).first()
    if coordinator is None:
        raise AppError(SOURCE_NOT_FOUND, 404)
    source = SensorSource.objects.select_for_update().filter(
        farm_id=farm_id, coordinator=coordinator, uid=uid
    ).first()
    if source is None:
        raise AppError(SOURCE_NOT_FOUND, 404)
    return source, coordinator


def _parse_source_uid(value) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise AppError(INVALID_SOURCE_UID, 404) from None


def _validate_role(value) -> str:
    if not isinstance(value, str) or value not in SensorSourceRole.values:
        raise AppError(INVALID_ROLE, 400)
    return value


def _optional_text(value, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AppError(INVALID_OPTIONAL_TEXT, 400)
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned or None


def _description(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AppError(INVALID_OPTIONAL_TEXT, 400)
    return value.strip()


def _snapshot(source: SensorSource) -> dict:
    return {
        "uid": str(source.uid),
        "coordinator_uid": source.coordinator.uid,
        "name": source.name,
        "model": source.model,
        "manufacturer": source.manufacturer,
        "role": source.role,
        "description": source.description,
        "is_active": source.is_active,
    }


@contextmanager
def _source_change_actor(user_id: int):
    """Expose the acting user to the source audit trigger for one operation.

    ``@agrotech_iot_source_actor_id`` is a MariaDB *session* (connection)
    variable: it is not transactional, a rollback does not undo it, and a
    persistent connection carries it into later requests. It is therefore
    cleared on every exit path, including a failed initial assignment and a
    failed protected operation. An exception raised by the protected code is
    always the one that propagates; a cleanup failure never replaces it.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET @agrotech_iot_source_actor_id = %s", [user_id])
        yield
    except BaseException:
        _clear_source_change_actor(propagate_errors=False)
        raise
    else:
        _clear_source_change_actor(propagate_errors=True)


def _reset_source_actor_variable(raw_connection) -> None:
    cursor = raw_connection.cursor()
    try:
        cursor.execute("SET @agrotech_iot_source_actor_id = NULL")
    finally:
        cursor.close()


def _clear_source_change_actor(*, propagate_errors: bool) -> None:
    # The DB-API connection is used directly on purpose: after a failed save
    # inside atomic(), Django sets needs_rollback and refuses every query
    # through connection.cursor(), yet this non-transactional session
    # variable must still be cleared.
    raw_connection = connection.connection
    if raw_connection is None:
        return  # No session was opened, so there is nothing to leak.
    try:
        _reset_source_actor_variable(raw_connection)
    except Exception:
        # The session may still hold a stale actor: discard it rather than let
        # a persistent connection attribute later writes to this user.
        logger.exception(
            "no se pudo limpiar el actor de auditoría de fuentes; se cierra la conexión"
        )
        try:
            connection.close()
        except Exception:
            logger.exception("no se pudo cerrar la conexión tras fallar la limpieza del actor")
        if propagate_errors:
            raise
