"""Durable global and per-coordinator MQTT delivery pauses."""

from __future__ import annotations

from django.db import DEFAULT_DB_ALIAS, IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.iot.models import (
    Coordinator,
    IotMqttDeliveryPause,
    IotMqttDeliveryPauseEvent,
)
from apps.iot.db_guard import require_mariadb

GLOBAL_SCOPE_KEY = "GLOBAL"


def pause_mqtt_deliveries(
    *,
    reason_code: str,
    coordinator_id: int | None = None,
    actor: User | None = None,
    process_id: str | None = None,
) -> IotMqttDeliveryPause:
    """Persist a pause; ``coordinator_id=None`` means global pause."""
    scope_key = _scope_key(coordinator_id)
    _validate_reason_code(reason_code)
    _validate_actor(actor=actor, process_id=process_id)
    require_mariadb(DEFAULT_DB_ALIAS)

    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        if coordinator_id is not None and not Coordinator.objects.filter(
            pk=coordinator_id
        ).exists():
            raise ValueError("coordinador de pausa no encontrado")
        existing = IotMqttDeliveryPause.objects.filter(
            active_scope_key=scope_key
        ).first()
        if existing is not None:
            return existing

        try:
            with transaction.atomic(using=DEFAULT_DB_ALIAS):
                pause = IotMqttDeliveryPause.objects.create(
                    coordinator_id=coordinator_id,
                    reason_code=reason_code,
                    is_active=True,
                    active_scope_key=scope_key,
                    started_by=actor,
                    started_process_id=process_id,
                )
                IotMqttDeliveryPauseEvent.objects.create(
                    pause=pause,
                    event_type="PAUSED",
                    actor=actor,
                    process_id=process_id,
                    reason_code=reason_code,
                )
        except IntegrityError:
            # A concurrent pause for this scope is idempotent. Other integrity
            # failures remain visible to the caller.
            existing = (
                IotMqttDeliveryPause.objects.select_for_update()
                .filter(active_scope_key=scope_key)
                .first()
            )
            if existing is None:
                raise
            return existing
        return pause


def resume_mqtt_deliveries(
    *,
    reason_code: str,
    coordinator_id: int | None = None,
    actor: User | None = None,
    process_id: str | None = None,
) -> IotMqttDeliveryPause | None:
    """Close the active pause for a scope and append a resume event."""
    scope_key = _scope_key(coordinator_id)
    _validate_actor(actor=actor, process_id=process_id)
    _validate_reason_code(reason_code)
    require_mariadb(DEFAULT_DB_ALIAS)

    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        pause = (
            IotMqttDeliveryPause.objects.select_for_update()
            .filter(active_scope_key=scope_key, is_active=True)
            .first()
        )
        if pause is None:
            return None
        pause.is_active = False
        pause.active_scope_key = None
        pause.resumed_at = timezone.now()
        pause.resumed_by = actor
        pause.resumed_process_id = process_id
        pause.save(
            update_fields=[
                "is_active",
                "active_scope_key",
                "resumed_at",
                "resumed_by",
                "resumed_process_id",
            ]
        )
        IotMqttDeliveryPauseEvent.objects.create(
            pause=pause,
            event_type="RESUMED",
            actor=actor,
            process_id=process_id,
            reason_code=reason_code,
        )
        return pause


def mqtt_delivery_pause_active(
    *, authenticated_coordinator_id: int | None, coordinator_candidate_id: int | None = None
) -> bool:
    """Check durable global and coordinator pauses.

    The candidate ID is only a conservative scheduling scope. It is never used
    as proof of identity or passed to ingestion as an authenticated principal.
    """
    if IotMqttDeliveryPause.objects.filter(
        active_scope_key=GLOBAL_SCOPE_KEY,
        is_active=True,
    ).exists():
        return True
    coordinator_id = authenticated_coordinator_id or coordinator_candidate_id
    if coordinator_id is None:
        return False
    return IotMqttDeliveryPause.objects.filter(
        active_scope_key=_scope_key(coordinator_id),
        is_active=True,
    ).exists()


def _scope_key(coordinator_id: int | None) -> str:
    if coordinator_id is None:
        return GLOBAL_SCOPE_KEY
    if type(coordinator_id) is not int or coordinator_id <= 0:
        raise ValueError("coordinador de pausa no válido")
    return f"COORDINATOR:{coordinator_id}"


def _validate_actor(*, actor: User | None, process_id: str | None) -> None:
    if actor is not None and not isinstance(actor, User):
        raise ValueError("actor de auditoría no válido")
    if actor is None and (not isinstance(process_id, str) or not process_id.strip()):
        raise ValueError("actor o proceso requerido para auditar la pausa")
    if process_id is not None and (not isinstance(process_id, str) or len(process_id) > 100):
        raise ValueError("identificador de proceso no válido")


def _validate_reason_code(reason_code: str) -> None:
    if (
        not isinstance(reason_code, str)
        or not reason_code
        or len(reason_code) > 64
        or not reason_code.isascii()
    ):
        raise ValueError("código de motivo no válido")


__all__ = [
    "GLOBAL_SCOPE_KEY",
    "mqtt_delivery_pause_active",
    "pause_mqtt_deliveries",
    "resume_mqtt_deliveries",
]
