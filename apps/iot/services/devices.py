"""Gateways y coordinadores IoT (IoT-02.2, DEC-13).

Propósito: registrar, consultar, desactivar y reactivar gateways y
coordinadores de una finca.

Reglas:
- Solo el administrador de la finca escribe (``access.lock_manage_membership``).
- Desactivar un gateway revoca sus autorizaciones vigentes.
- Desactivar un coordinador revoca sus autorizaciones vigentes y desactiva sus
  sensores activos.
- Reactivar es un procedimiento administrativo controlado: exige motivo, queda
  auditado y **no** restaura autorizaciones ni sensores; hay que volver a
  autorizar y reactivar cada sensor explícitamente.
- La finca de un dispositivo nunca cambia.
"""
from django.db import transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import LOCATION_LABEL_MAX_LENGTH, NAME_MAX_LENGTH, Coordinator, Gateway
from apps.iot.services import authorizations, sensors
from apps.iot.services._common import (
    audit,
    clean_reason,
    create_with_uid,
    lock_by_uid,
    optional_text,
    parse_uid,
    required_reason,
    required_text,
)
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType

GATEWAY_NOT_FOUND = "gateway no encontrado"
COORDINATOR_NOT_FOUND = "coordinador no encontrado"
NAME_REQUIRED = "el nombre del dispositivo es obligatorio"
ALREADY_INACTIVE = "el dispositivo ya está desactivado"
ALREADY_ACTIVE = "el dispositivo ya está activo"
CAUSE_GATEWAY_DEACTIVATED = "gateway_deactivated"
CAUSE_COORDINATOR_DEACTIVATED = "coordinator_deactivated"


class _DeviceSpec:
    """Diferencias entre gateway y coordinador para los casos de uso comunes."""

    def __init__(self, model, kind, entity_type, not_found, created, deactivated, reactivated):
        self.model = model
        self.kind = kind
        self.entity_type = entity_type
        self.not_found = not_found
        self.created = created
        self.deactivated = deactivated
        self.reactivated = reactivated


GATEWAY = _DeviceSpec(
    Gateway,
    IdentifierKind.GATEWAY,
    "iot_gateway",
    GATEWAY_NOT_FOUND,
    EventType.IOT_GATEWAY_CREATED,
    EventType.IOT_GATEWAY_DEACTIVATED,
    EventType.IOT_GATEWAY_REACTIVATED,
)
COORDINATOR = _DeviceSpec(
    Coordinator,
    IdentifierKind.COORDINATOR,
    "iot_coordinator",
    COORDINATOR_NOT_FOUND,
    EventType.IOT_COORDINATOR_CREATED,
    EventType.IOT_COORDINATOR_DEACTIVATED,
    EventType.IOT_COORDINATOR_REACTIVATED,
)


# --- Lectura ---


def list_gateways(*, user: User, farm_id: int, include_inactive: bool = False):
    """Gateways de la finca (por defecto, solo activos)."""
    return _list(GATEWAY, user=user, farm_id=farm_id, include_inactive=include_inactive)


def get_gateway(*, user: User, farm_id: int, uid: str) -> Gateway:
    """Gateway de la finca o 404."""
    return _get(GATEWAY, user=user, farm_id=farm_id, uid=uid)


def list_coordinators(*, user: User, farm_id: int, include_inactive: bool = False):
    """Coordinadores de la finca (por defecto, solo activos)."""
    return _list(COORDINATOR, user=user, farm_id=farm_id, include_inactive=include_inactive)


def get_coordinator(*, user: User, farm_id: int, uid: str) -> Coordinator:
    """Coordinador de la finca o 404."""
    return _get(COORDINATOR, user=user, farm_id=farm_id, uid=uid)


# --- Escritura ---


def create_gateway(*, user: User, farm_id: int, name: str, location_label=None) -> Gateway:
    """Registra un gateway con identificador ``GW-`` nuevo."""
    return _create(GATEWAY, user=user, farm_id=farm_id, name=name, location_label=location_label)


def create_coordinator(
    *, user: User, farm_id: int, name: str, location_label=None
) -> Coordinator:
    """Registra un coordinador con identificador ``CO-`` nuevo."""
    return _create(
        COORDINATOR, user=user, farm_id=farm_id, name=name, location_label=location_label
    )


def deactivate_gateway(*, user: User, farm_id: int, uid: str, reason=None) -> Gateway:
    """Desactiva el gateway y revoca sus autorizaciones vigentes."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = parse_uid(uid, GATEWAY.kind, GATEWAY.not_found)
        clean = clean_reason(reason)
        gateway = lock_by_uid(Gateway, farm_id=farm_id, uid=uid, not_found=GATEWAY.not_found)
        if not gateway.is_active:
            raise AppError(ALREADY_INACTIVE, 409)
        now = timezone.now()
        revoked = 0
        for row in authorizations.lock_active_authorizations(gateway=gateway):
            authorizations.revoke_locked(
                membership=membership,
                user=user,
                authorization=row,
                now=now,
                reason=clean,
                cause=CAUSE_GATEWAY_DEACTIVATED,
            )
            revoked += 1
        _deactivate(
            GATEWAY,
            gateway,
            membership=membership,
            user=user,
            now=now,
            reason=clean,
            metadata={"revoked_authorizations": revoked},
        )
        return gateway


def deactivate_coordinator(*, user: User, farm_id: int, uid: str, reason=None) -> Coordinator:
    """Desactiva el coordinador, revoca sus autorizaciones y desactiva sus sensores."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = parse_uid(uid, COORDINATOR.kind, COORDINATOR.not_found)
        clean = clean_reason(reason)
        coordinator = lock_by_uid(
            Coordinator, farm_id=farm_id, uid=uid, not_found=COORDINATOR.not_found
        )
        if not coordinator.is_active:
            raise AppError(ALREADY_INACTIVE, 409)
        now = timezone.now()
        revoked = 0
        for row in authorizations.lock_active_authorizations(coordinator=coordinator):
            authorizations.revoke_locked(
                membership=membership,
                user=user,
                authorization=row,
                now=now,
                reason=clean,
                cause=CAUSE_COORDINATOR_DEACTIVATED,
            )
            revoked += 1
        deactivated_sensors = sensors.deactivate_all_locked(
            membership=membership,
            user=user,
            coordinator=coordinator,
            now=now,
            reason=clean,
            cause=CAUSE_COORDINATOR_DEACTIVATED,
        )
        _deactivate(
            COORDINATOR,
            coordinator,
            membership=membership,
            user=user,
            now=now,
            reason=clean,
            metadata={
                "revoked_authorizations": revoked,
                "deactivated_sensors": deactivated_sensors,
            },
        )
        return coordinator


def reactivate_gateway(*, user: User, farm_id: int, uid: str, reason) -> Gateway:
    """Reactiva un gateway (motivo obligatorio; sin restaurar autorizaciones)."""
    return _reactivate(GATEWAY, user=user, farm_id=farm_id, uid=uid, reason=reason)


def reactivate_coordinator(*, user: User, farm_id: int, uid: str, reason) -> Coordinator:
    """Reactiva un coordinador (motivo obligatorio; sin restaurar sensores ni autorizaciones)."""
    return _reactivate(COORDINATOR, user=user, farm_id=farm_id, uid=uid, reason=reason)


# --- Internos ---


def _list(spec: _DeviceSpec, *, user, farm_id, include_inactive):
    require_read_membership(user=user, farm_id=farm_id)
    queryset = spec.model.objects.filter(farm_id=farm_id)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def _get(spec: _DeviceSpec, *, user, farm_id, uid):
    require_read_membership(user=user, farm_id=farm_id)
    uid = parse_uid(uid, spec.kind, spec.not_found)
    device = spec.model.objects.filter(farm_id=farm_id, uid=uid).first()
    if device is None:
        raise AppError(spec.not_found, 404)
    return device


def _create(spec: _DeviceSpec, *, user, farm_id, name, location_label):
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        clean_name = required_text(name, NAME_REQUIRED, NAME_MAX_LENGTH)
        clean_label = optional_text(location_label, LOCATION_LABEL_MAX_LENGTH)
        device = create_with_uid(
            spec.model,
            spec.kind,
            farm_id=membership.farm_id,
            name=clean_name,
            location_label=clean_label,
            created_by=user,
        )
        audit(
            membership=membership,
            user=user,
            event_type=spec.created,
            entity_type=spec.entity_type,
            entity_id=device.uid,
            after=_snapshot(device),
        )
        return device


def _deactivate(spec: _DeviceSpec, device, *, membership, user, now, reason, metadata):
    before = _snapshot(device)
    device.is_active = False
    device.deactivated_at = now
    device.deactivated_by = user
    device.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"])
    audit(
        membership=membership,
        user=user,
        event_type=spec.deactivated,
        entity_type=spec.entity_type,
        entity_id=device.uid,
        before=before,
        after=_snapshot(device),
        reason=reason,
        metadata=metadata,
    )


def _reactivate(spec: _DeviceSpec, *, user, farm_id, uid, reason):
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = parse_uid(uid, spec.kind, spec.not_found)
        clean = required_reason(reason)
        device = lock_by_uid(spec.model, farm_id=farm_id, uid=uid, not_found=spec.not_found)
        if device.is_active:
            raise AppError(ALREADY_ACTIVE, 409)
        before = _snapshot(device)
        device.is_active = True
        device.deactivated_at = None
        device.deactivated_by = None
        device.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "updated_at"])
        audit(
            membership=membership,
            user=user,
            event_type=spec.reactivated,
            entity_type=spec.entity_type,
            entity_id=device.uid,
            before=before,
            after=_snapshot(device),
            reason=clean,
            metadata={"restored_authorizations": 0, "restored_sensors": 0},
        )
        return device


def _snapshot(device) -> dict:
    return {
        "uid": device.uid,
        "name": device.name,
        "location_label": device.location_label,
        "is_active": device.is_active,
    }
