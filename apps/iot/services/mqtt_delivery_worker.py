"""Polling worker for durable MQTT deliveries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from apps.iot.broker_identity_config import (
    require_operational_config,
    require_operational_mode_from_env,
)
from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryPause,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_failures import (
    FailureKind,
    RetryPolicy,
    classify_delivery_failure,
    record_mqtt_delivery_failure,
)
from apps.iot.services.mqtt_delivery_leases import (
    claim_mqtt_delivery,
    renew_mqtt_delivery_lease,
)
from apps.iot.services.mqtt_delivery_pauses import (
    mqtt_delivery_pause_active,
    pause_mqtt_deliveries,
)
from apps.iot.services.mqtt_delivery_processing import (
    DeliveryProcessingOutcome,
    process_encrypted_mqtt_delivery,
)

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_CLAIM_ERRORS = 3
# Upper bound of claim attempts per batch, relative to batch_size, so that
# many blocked lanes cannot turn one batch into an unbounded scan.
MAX_ATTEMPTS_PER_BATCH_FACTOR = 5


def _retryable_query(now):
    return (
        Q(state=State.RECEIVED)
        | Q(state=State.RETRY_PENDING, next_attempt_at__isnull=True)
        | Q(state=State.RETRY_PENDING, next_attempt_at__lte=now)
        | Q(state=State.PROCESSING, lease_expires_at__isnull=True)
        | Q(state=State.PROCESSING, lease_expires_at__lte=now)
    )


@dataclass(frozen=True, slots=True)
class WorkerBatchResult:
    discovered: int
    claimed: int
    processed: int
    deferred: int


def _paused_coordinator_ids() -> set[int]:
    """Planning hint only: claim_mqtt_delivery re-checks pauses under lock."""
    return set(
        IotMqttDeliveryPause.objects.filter(is_active=True, coordinator__isnull=False)
        .values_list("coordinator_id", flat=True)
    )


def _plan_page(*, now, excluded_lanes: set, limit: int) -> list[tuple[UUID, int | None]]:
    """Oldest retryable rows outside excluded lanes, as ``(id, lane_id)``.

    A lane is ``authenticated_coordinator`` or, failing that, the candidate
    ``coordinator``; ``None`` is the isolated lane without coordinator. This
    mirrors the lane key used by the lease service. It is only a scheduling
    filter: eligibility, FIFO and pauses are enforced by the transactional
    claim.
    """
    queryset = IotMqttDelivery.objects.filter(_retryable_query(now))
    coordinator_lanes = [lane for lane in excluded_lanes if lane is not None]
    if coordinator_lanes:
        queryset = queryset.exclude(authenticated_coordinator_id__in=coordinator_lanes).exclude(
            authenticated_coordinator__isnull=True, coordinator_id__in=coordinator_lanes
        )
    if None in excluded_lanes:
        queryset = queryset.exclude(
            authenticated_coordinator__isnull=True, coordinator__isnull=True
        )
    rows = queryset.order_by("received_at", "id").values_list(
        "id", "authenticated_coordinator_id", "coordinator_id"
    )[:limit]
    return [(row_id, authenticated or candidate) for row_id, authenticated, candidate in rows]


class _Attempt:
    PROCESSED = "processed"
    DEFERRED = "deferred"
    CLAIM_ERROR = "claim_error"


def run_mqtt_delivery_worker_once(
    *,
    worker_id: str,
    batch_size: int = 10,
    policy: RetryPolicy | None = None,
    broker_identity=None,
) -> WorkerBatchResult:
    """Claim and process one bounded, lane-fair batch; rows survive restarts.

    Fairness: rows are planned oldest-first in pages of ``batch_size``. A lane
    that cannot advance (claim refused, lease held elsewhere, pause, failure)
    is excluded from the rest of the batch after one attempt, and paused
    coordinators are excluded from planning up front. A lane that advances
    keeps its FIFO turn. Claims are bounded by ``batch_size`` and claim
    attempts by ``batch_size * MAX_ATTEMPTS_PER_BATCH_FACTOR``.
    """
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 100:
        raise ValueError("identificador de worker no válido")
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise ValueError("tamaño de lote no válido")
    # BUILD-FLEPSI-02H: ENFORCE (env or hand-built config) fails before any
    # claim; it never silently degrades to off.
    require_operational_mode_from_env()
    if broker_identity is not None:
        require_operational_config(broker_identity)
    if policy is None:
        policy = RetryPolicy()
    elif not isinstance(policy, RetryPolicy):
        raise ValueError("política de reintentos no válida")

    close_old_connections()
    try:
        discovered: set[UUID] = set()
        blocked_lanes: set[int | None] = set()
        claimed_count = processed_count = deferred_count = attempts = 0
        consecutive_claim_errors = 0
        max_attempts = batch_size * MAX_ATTEMPTS_PER_BATCH_FACTOR
        stop = False
        while not stop and claimed_count < batch_size and attempts < max_attempts:
            if mqtt_delivery_pause_active(authenticated_coordinator_id=None):
                break  # Global pause: nothing can be claimed (claim re-checks).
            page = _plan_page(
                now=timezone.now(),
                excluded_lanes=blocked_lanes | _paused_coordinator_ids(),
                limit=batch_size,
            )
            page = [(row_id, lane) for row_id, lane in page if row_id not in discovered]
            if not page:
                break
            discovered.update(row_id for row_id, _lane in page)
            attempted_in_page = 0
            for delivery_id, lane in page:
                if lane in blocked_lanes:
                    continue
                if claimed_count >= batch_size or attempts >= max_attempts:
                    stop = True
                    break
                attempts += 1
                attempted_in_page += 1
                outcome, claimed = _attempt_delivery(delivery_id, worker_id, policy)
                claimed_count += claimed
                if outcome == _Attempt.PROCESSED:
                    processed_count += 1
                    consecutive_claim_errors = 0
                    continue
                deferred_count += 1
                blocked_lanes.add(lane)
                if outcome != _Attempt.CLAIM_ERROR:
                    consecutive_claim_errors = 0
                    continue
                consecutive_claim_errors += 1
                if consecutive_claim_errors >= MAX_CONSECUTIVE_CLAIM_ERRORS:
                    # A systemic failure would repeat for every row: stop the
                    # batch instead of hammering the database.
                    logger.error(
                        "lote MQTT detenido tras %s errores consecutivos de reclamación",
                        consecutive_claim_errors,
                    )
                    stop = True
                    break
            if attempted_in_page == 0:
                break  # Defensive: never spin without progress.
        return WorkerBatchResult(len(discovered), claimed_count, processed_count, deferred_count)
    finally:
        close_old_connections()


def _attempt_delivery(
    delivery_id: UUID, worker_id: str, policy: RetryPolicy
) -> tuple[str, int]:
    """Claim then process one delivery; returns ``(outcome, claimed 0/1)``."""
    # Claim phase: an error here leaves the row untouched (the claim
    # transaction rolled back) or with a live lease that later expires; it is
    # never counted as processed.
    try:
        claim = claim_mqtt_delivery(
            delivery_id=delivery_id,
            worker_id=worker_id,
            max_attempts=policy.max_attempts,
        )
        if claim is None or claim.lease_token is None:
            return _Attempt.DEFERRED, 0
        token: UUID = claim.lease_token
        renewed = renew_mqtt_delivery_lease(
            delivery_id=delivery_id, worker_id=worker_id, lease_token=token
        )
    except Exception as exc:
        logger.error(
            "falló la reclamación durable para entrega %s (%s)",
            delivery_id,
            type(exc).__name__,
        )
        close_old_connections()
        return _Attempt.CLAIM_ERROR, 0
    if not renewed:
        return _Attempt.DEFERRED, 1
    try:
        result = process_encrypted_mqtt_delivery(
            delivery_id=delivery_id,
            lease_owner=worker_id,
            lease_token=token,
        )
    except Exception as exc:
        failure = classify_delivery_failure(exc)
        close_old_connections()
        if failure.pauses_globally:
            # A missing/invalid key or unauthentic envelope is an operational
            # stop, never a business rejection or ACK.
            _pause_safely(worker_id)
            logger.error("entrega %s pausada por error criptográfico", delivery_id)
        else:
            logger.error(
                "falló procesamiento durable para entrega %s (%s)",
                delivery_id,
                failure.safe_detail,
            )
        if failure.kind is not FailureKind.LEASE_LOST:
            _record_failure_safely(delivery_id, worker_id, token, failure, policy)
        return _Attempt.DEFERRED, 1
    finally:
        close_old_connections()
    if result.outcome is DeliveryProcessingOutcome.PROCESSED:
        return _Attempt.PROCESSED, 1
    return _Attempt.DEFERRED, 1


def _pause_safely(worker_id: str) -> None:
    try:
        pause_mqtt_deliveries(reason_code="MQTT_PAYLOAD_CRYPTO_ERROR", process_id=worker_id)
    except Exception as exc:
        logger.error("no se pudo registrar la pausa criptográfica (%s)", type(exc).__name__)


def _record_failure_safely(delivery_id, worker_id, token, failure, policy) -> None:
    """Persist the failure; if that also fails, the lease expiry is the fallback.

    In that case a later claim increments ``attempt_count`` again and the row
    reaches ``DEAD_LETTER`` once the budget is exhausted, so it never loops
    forever.
    """
    try:
        record_mqtt_delivery_failure(
            delivery_id=delivery_id,
            worker_id=worker_id,
            lease_token=token,
            failure=failure,
            policy=policy,
        )
    except Exception as exc:
        logger.error(
            "no se pudo registrar el fallo de la entrega %s (%s); se recuperará al vencer el lease",
            delivery_id,
            type(exc).__name__,
        )
    finally:
        close_old_connections()


__all__ = ["WorkerBatchResult", "run_mqtt_delivery_worker_once"]
