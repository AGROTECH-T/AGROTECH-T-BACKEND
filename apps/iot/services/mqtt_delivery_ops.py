"""Operator inspection and dead-letter resolution for MQTT deliveries (HARDEN-01).

Inspection helpers only read. ``resolve_dead_letter`` is the single write
path for a ``DEAD_LETTER`` row and acts on exactly one delivery:

- ``requeue``: ``DEAD_LETTER`` -> ``RETRY_PENDING`` (due now) with a fresh
  attempt budget; the previous count stays in the event log.
- ``discard``: ``DEAD_LETTER`` -> ``RESOLVED``; frees the FIFO lane without
  processing it. The row, its ciphertext and its history are kept.

Neither action deletes data and both append an ``IotMqttDeliveryEvent``.
No payload, ciphertext or key material is ever returned by these helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.iot.db_guard import require_mariadb
from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_processing import DeliveryNotFoundError

_PENDING_STATES = (State.RECEIVED, State.PROCESSING, State.RETRY_PENDING, State.DEAD_LETTER)
LISTABLE_STATES = frozenset(
    {State.RETRY_PENDING, State.DEAD_LETTER, State.PROCESSING, State.RECEIVED}
)
MAX_LIST_LIMIT = 500


class ResolutionAction(StrEnum):
    REQUEUE = "requeue"
    DISCARD = "discard"


class DeadLetterResolutionError(ValueError):
    """The requested resolution is not allowed for the delivery's state."""


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    id: UUID
    state: str
    topic: str
    received_at: datetime
    attempt_count: int
    next_attempt_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    lane_coordinator_id: int | None
    authenticated: bool
    result_code: str | None
    reason_code: str | None
    last_error_code: str | None
    last_error_safe: str | None
    resolution_code: str | None
    resolved_at: datetime | None
    blocked_successors: int


def delivery_summary(delivery_id: UUID) -> DeliverySummary:
    row = IotMqttDelivery.objects.filter(pk=delivery_id).first()
    if row is None:
        raise DeliveryNotFoundError("entrega MQTT no encontrada")
    return _summary(row)


def list_deliveries(*, state: str, limit: int = 50) -> list[DeliverySummary]:
    if state not in LISTABLE_STATES:
        raise ValueError("estado no listable")
    if type(limit) is not int or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit debe estar entre 1 y {MAX_LIST_LIMIT}")
    rows = IotMqttDelivery.objects.filter(state=state).order_by("received_at", "id")[:limit]
    return [_summary(row) for row in rows]


def delivery_events(delivery_id: UUID) -> list[IotMqttDeliveryEvent]:
    return list(
        IotMqttDeliveryEvent.objects.filter(delivery_id=delivery_id).order_by("occurred_at", "id")
    )


def list_pauses(*, include_inactive: bool = False, limit: int = 50) -> list[IotMqttDeliveryPause]:
    if type(limit) is not int or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit debe estar entre 1 y {MAX_LIST_LIMIT}")
    queryset = IotMqttDeliveryPause.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return list(queryset.order_by("-started_at", "-id")[:limit])


def resolve_dead_letter(
    *,
    delivery_id: UUID,
    action: ResolutionAction,
    reason_code: str,
    operator: str,
    actor: User | None = None,
) -> IotMqttDelivery:
    """Explicit, audited resolution of exactly one ``DEAD_LETTER`` delivery."""
    if not isinstance(delivery_id, UUID):
        raise ValueError("identificador de entrega no válido")
    action = ResolutionAction(action)
    _validate_code(reason_code, "código de motivo")
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 80:
        raise ValueError("operador no válido")
    if not operator.isascii() or not operator.isprintable():
        raise ValueError("operador no válido")
    if actor is not None and not isinstance(actor, User):
        raise ValueError("actor de auditoría no válido")
    require_mariadb(DEFAULT_DB_ALIAS)
    process_id = f"ops:{operator.strip()}"
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        row = IotMqttDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if row is None:
            raise DeliveryNotFoundError("entrega MQTT no encontrada")
        if row.state != State.DEAD_LETTER:
            raise DeadLetterResolutionError("solo se resuelven entregas en DEAD_LETTER")
        previous_attempts = row.attempt_count
        if action is ResolutionAction.REQUEUE:
            row.state = State.RETRY_PENDING
            row.next_attempt_at = None
            row.attempt_count = 0
            row.save(update_fields=["state", "next_attempt_at", "attempt_count", "updated_at"])
            event_type, to_state = "DEAD_LETTER_REQUEUED", State.RETRY_PENDING
        else:
            row.state = State.RESOLVED
            row.resolved_at = timezone.now()
            row.resolved_by = actor
            row.resolution_code = reason_code
            row.save(
                update_fields=[
                    "state",
                    "resolved_at",
                    "resolved_by",
                    "resolution_code",
                    "updated_at",
                ]
            )
            event_type, to_state = "DEAD_LETTER_RESOLVED", State.RESOLVED
        IotMqttDeliveryEvent.objects.create(
            delivery=row,
            event_type=event_type,
            from_state=State.DEAD_LETTER,
            to_state=to_state,
            attempt_number=previous_attempts,
            actor=actor,
            worker_id=process_id,
            result_code=reason_code,
            correlation_id=row.id,
            safe_detail=f"acción={action.value}; intentos previos={previous_attempts}",
        )
        return row


def _summary(row: IotMqttDelivery) -> DeliverySummary:
    lane = row.authenticated_coordinator_id or row.coordinator_id
    return DeliverySummary(
        id=row.id,
        state=row.state,
        topic=row.topic,
        received_at=row.received_at,
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        lane_coordinator_id=lane,
        authenticated=row.authenticated_coordinator_id is not None,
        result_code=row.result_code,
        reason_code=row.reason_code,
        last_error_code=row.last_error_code,
        last_error_safe=row.last_error_safe,
        resolution_code=row.resolution_code,
        resolved_at=row.resolved_at,
        blocked_successors=_blocked_successors(row),
    )


def _blocked_successors(row: IotMqttDelivery) -> int:
    """Pending deliveries queued behind ``row`` in the same FIFO lane."""
    if row.state not in _PENDING_STATES:
        return 0
    lane = row.authenticated_coordinator_id or row.coordinator_id
    if lane is None:
        same_lane = Q(authenticated_coordinator__isnull=True, coordinator__isnull=True)
    else:
        same_lane = Q(authenticated_coordinator_id=lane) | Q(
            authenticated_coordinator__isnull=True, coordinator_id=lane
        )
    later = Q(received_at__gt=row.received_at) | Q(received_at=row.received_at, id__gt=row.id)
    return (
        IotMqttDelivery.objects.filter(state__in=_PENDING_STATES)
        .filter(later)
        .filter(same_lane)
        .count()
    )


def _validate_code(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or not value.isascii()
        or not all(char.isalnum() or char in "_-." for char in value)
    ):
        raise ValueError(f"{label} no válido")


__all__ = [
    "DeadLetterResolutionError",
    "DeliverySummary",
    "LISTABLE_STATES",
    "ResolutionAction",
    "delivery_events",
    "delivery_summary",
    "list_deliveries",
    "list_pauses",
    "resolve_dead_letter",
]
