"""Durable lease claiming and renewal for MQTT delivery workers."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import Q
from django.utils import timezone

from apps.iot.db_guard import require_mariadb
from apps.iot.models import (
    Coordinator,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_failures import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ALLOWED_ATTEMPTS,
    MAX_ATTEMPTS_EXCEEDED,
    dead_letter_row,
)
from apps.iot.services.mqtt_delivery_pauses import mqtt_delivery_pause_active

LEASE_SECONDS = 60


def claim_mqtt_delivery(
    *, delivery_id: UUID, worker_id: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> IotMqttDelivery | None:
    """Atomically claim one eligible row, generating a fresh fencing token.

    A row that already consumed ``max_attempts`` (for example after repeated
    worker crashes that never recorded a failure) is moved to ``DEAD_LETTER``
    instead of being claimed again, so ``attempt_count`` is bounded.
    """
    if not isinstance(delivery_id, UUID) or not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 100:
        raise ValueError("identidad de worker o entrega no válida")
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ALLOWED_ATTEMPTS:
        raise ValueError("máximo de intentos no válido")
    require_mariadb(DEFAULT_DB_ALIAS)
    now = timezone.now()
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        row = IotMqttDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if row is None or row.state in _FINAL_OR_BLOCKED:
            return None
        if row.state == State.RETRY_PENDING and row.next_attempt_at and row.next_attempt_at > now:
            return None
        if row.state == State.PROCESSING and row.lease_expires_at and row.lease_expires_at > now:
            return None
        lane_id = row.authenticated_coordinator_id or row.coordinator_id
        if mqtt_delivery_pause_active(
            authenticated_coordinator_id=row.authenticated_coordinator_id,
            coordinator_candidate_id=row.coordinator_id,
        ):
            return None
        if lane_id is not None:
            # Serialize claims for a known lane. Candidate identity is solely a
            # conservative scheduling hint until ingestion authenticates it.
            Coordinator.objects.select_for_update().filter(pk=lane_id).first()
        if IotMqttDelivery.objects.filter(
            state__in=(State.RECEIVED, State.PROCESSING, State.RETRY_PENDING, State.DEAD_LETTER),
        ).filter(
            (Q(received_at__lt=row.received_at) | Q(received_at=row.received_at, id__lt=row.id)),
            _same_lane_filter(row),
        ).exists():
            return None
        if row.attempt_count >= max_attempts:
            dead_letter_row(
                row,
                code=MAX_ATTEMPTS_EXCEEDED,
                last_error_code=None,
                safe_detail=f"presupuesto de {max_attempts} intentos agotado al reclamar",
                worker_id=worker_id,
                lease_token=None,
            )
            return None
        old_state = row.state
        token = uuid4()
        row.state = State.PROCESSING
        row.attempt_count += 1
        row.next_attempt_at = None
        row.lease_owner = worker_id
        row.lease_token = token
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.save(update_fields=["state", "attempt_count", "next_attempt_at", "lease_owner", "lease_token", "lease_expires_at", "updated_at"])
        _event(row, "LEASE_CLAIMED", old_state, State.PROCESSING, worker_id, token)
        return row


def renew_mqtt_delivery_lease(*, delivery_id: UUID, worker_id: str, lease_token: UUID) -> bool:
    """Renew only a still-live lease held by the exact worker/token pair."""
    require_mariadb(DEFAULT_DB_ALIAS)
    now = timezone.now()
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        row = IotMqttDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if not _owns_live_lease(row, worker_id, lease_token, now):
            return False
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.save(update_fields=["lease_expires_at", "updated_at"])
        _event(row, "LEASE_RENEWED", State.PROCESSING, State.PROCESSING, worker_id, lease_token)
        return True


def verify_mqtt_delivery_lease(row, *, worker_id: str, lease_token: UUID, now=None) -> bool:
    return _owns_live_lease(row, worker_id, lease_token, now or timezone.now())


def _owns_live_lease(row, worker_id, token, now) -> bool:
    return bool(row and row.state == State.PROCESSING and row.lease_owner == worker_id and row.lease_token == token and row.lease_expires_at and row.lease_expires_at > now)


def _same_lane_filter(row) -> Q:
    """Return the lane as a ``Q`` so it can be combined positionally.

    Deliveries with neither authenticated nor candidate coordinator form their
    own isolated FIFO lane; they never join an authenticated coordinator lane.
    """
    lane_id = row.authenticated_coordinator_id or row.coordinator_id
    if lane_id is None:
        return Q(authenticated_coordinator__isnull=True, coordinator__isnull=True)
    return Q(authenticated_coordinator_id=lane_id) | Q(
        authenticated_coordinator__isnull=True, coordinator_id=lane_id
    )


def _event(row, event_type, before, after, worker_id, token):
    IotMqttDeliveryEvent.objects.create(
        delivery=row, event_type=event_type, from_state=before, to_state=after,
        attempt_number=row.attempt_count, worker_id=worker_id, lease_token=token,
        correlation_id=row.id, safe_detail="Cambio durable de lease",
    )


_FINAL_OR_BLOCKED = (State.ACCEPTED, State.DUPLICATE, State.REJECTED, State.CONFLICT, State.RESOLVED, State.DEAD_LETTER)

__all__ = ["claim_mqtt_delivery", "renew_mqtt_delivery_lease", "verify_mqtt_delivery_lease"]
