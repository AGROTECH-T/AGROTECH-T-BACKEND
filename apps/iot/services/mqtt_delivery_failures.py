"""Retry policy, failure classification and dead-letter transitions (HARDEN-01 C2).

A delivery whose processing fails is never left in ``PROCESSING`` waiting for
its lease to expire. The worker that still holds the fencing token records the
failure durably:

- transient failure with attempts left -> ``RETRY_PENDING`` with
  ``next_attempt_at`` computed by exponential backoff;
- permanent failure, or attempt budget exhausted -> ``DEAD_LETTER``.

``DEAD_LETTER`` keeps blocking its FIFO lane on purpose (a failed delivery is
never skipped silently). It is logged at ERROR, listed by the
``mqtt_deliveries`` management command and resolved only by an explicit,
audited operator action (``apps.iot.services.mqtt_delivery_ops``).

``attempt_count`` is a ``PositiveSmallIntegerField``. Overflow is prevented by
construction: ``MAX_ALLOWED_ATTEMPTS`` is far below the column limit, a claim
never increments a row that already reached its budget (it dead-letters it
instead) and a requeue resets the counter while the event log keeps history.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.db import (
    DEFAULT_DB_ALIAS,
    DatabaseError,
    DataError,
    IntegrityError,
    transaction,
)
from django.utils import timezone

from apps.iot.db_guard import require_mariadb
from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryEvent,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_processing import (
    DeliveryNotFoundError,
    DeliveryPayloadMismatchError,
    IngestionContractError,
    LeaseLostError,
)
from apps.iot.services.mqtt_payload_crypto import (
    MqttPayloadCryptoError,
    MqttPayloadKeyUnavailable,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_BASE_DELAY_SECONDS = 5
DEFAULT_MAX_DELAY_SECONDS = 900
# Hard ceiling, well below PositiveSmallIntegerField (32767 on every backend).
MAX_ALLOWED_ATTEMPTS = 1000
MAX_ALLOWED_DELAY_SECONDS = 86_400
_MAX_EXPONENT = 30

ENV_MAX_ATTEMPTS = "IOT_MQTT_DELIVERY_MAX_ATTEMPTS"
ENV_BASE_DELAY = "IOT_MQTT_DELIVERY_RETRY_BASE_SECONDS"
ENV_MAX_DELAY = "IOT_MQTT_DELIVERY_RETRY_MAX_SECONDS"

MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED"


class RetryPolicyError(ValueError):
    """Invalid retry policy; never includes secrets."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: int = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: int = DEFAULT_MAX_DELAY_SECONDS

    def __post_init__(self) -> None:
        for name in ("max_attempts", "base_delay_seconds", "max_delay_seconds"):
            if type(getattr(self, name)) is not int:
                raise RetryPolicyError(f"{name} debe ser entero")
        if not 1 <= self.max_attempts <= MAX_ALLOWED_ATTEMPTS:
            raise RetryPolicyError(
                f"max_attempts debe estar entre 1 y {MAX_ALLOWED_ATTEMPTS}"
            )
        if not 1 <= self.base_delay_seconds <= 3600:
            raise RetryPolicyError("base_delay_seconds debe estar entre 1 y 3600")
        if not self.base_delay_seconds <= self.max_delay_seconds <= MAX_ALLOWED_DELAY_SECONDS:
            raise RetryPolicyError(
                "max_delay_seconds debe estar entre base_delay_seconds y "
                f"{MAX_ALLOWED_DELAY_SECONDS}"
            )

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RetryPolicy:
        values = os.environ if environ is None else environ
        return cls(
            max_attempts=_env_int(values, ENV_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS),
            base_delay_seconds=_env_int(values, ENV_BASE_DELAY, DEFAULT_BASE_DELAY_SECONDS),
            max_delay_seconds=_env_int(values, ENV_MAX_DELAY, DEFAULT_MAX_DELAY_SECONDS),
        )

    def delay_for(self, attempt: int) -> timedelta:
        """Backoff after ``attempt`` failed attempts: base * 2**(attempt-1), capped.

        The exponent is bounded so a corrupted counter cannot build a huge
        integer or overflow ``datetime``.
        """
        if type(attempt) is not int or attempt < 1:
            attempt = 1
        exponent = min(attempt - 1, _MAX_EXPONENT)
        seconds = min(self.max_delay_seconds, self.base_delay_seconds * (2**exponent))
        return timedelta(seconds=seconds)

    def exhausted(self, attempt_count: int) -> bool:
        return attempt_count >= self.max_attempts


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    KEY_UNAVAILABLE = "key_unavailable"
    CRYPTO_INVALID = "crypto_invalid"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    kind: FailureKind
    code: str
    safe_detail: str

    @property
    def retryable(self) -> bool:
        return self.kind in (FailureKind.TRANSIENT, FailureKind.KEY_UNAVAILABLE)

    @property
    def pauses_globally(self) -> bool:
        return self.kind in (FailureKind.KEY_UNAVAILABLE, FailureKind.CRYPTO_INVALID)


def classify_delivery_failure(exc: BaseException) -> FailureClassification:
    """Map an exception to a retry decision. Details never include payloads.

    Crypto errors are split on purpose: a missing key version is an
    operational condition (retryable once the key is configured), while an
    envelope that fails authentication is permanent for that row. Both pause
    processing globally because a wrong key would affect every delivery.
    """
    name = type(exc).__name__
    if isinstance(exc, LeaseLostError):
        return FailureClassification(FailureKind.LEASE_LOST, "LEASE_LOST", name)
    if isinstance(exc, MqttPayloadKeyUnavailable):
        return FailureClassification(
            FailureKind.KEY_UNAVAILABLE, "MQTT_PAYLOAD_KEY_UNAVAILABLE", name
        )
    if isinstance(exc, MqttPayloadCryptoError):
        return FailureClassification(
            FailureKind.CRYPTO_INVALID, "MQTT_PAYLOAD_AUTH_FAILED", name
        )
    if isinstance(exc, DeliveryPayloadMismatchError):
        return FailureClassification(
            FailureKind.PERMANENT, "PAYLOAD_FINGERPRINT_MISMATCH", name
        )
    if isinstance(exc, IngestionContractError):
        return FailureClassification(FailureKind.PERMANENT, "INGESTION_CONTRACT_ERROR", name)
    if isinstance(exc, DeliveryNotFoundError):
        return FailureClassification(FailureKind.PERMANENT, "DELIVERY_NOT_FOUND", name)
    if isinstance(exc, (IntegrityError, DataError)):
        return FailureClassification(FailureKind.PERMANENT, "DATABASE_INTEGRITY_ERROR", name)
    if isinstance(exc, DatabaseError):
        return FailureClassification(FailureKind.TRANSIENT, "DATABASE_ERROR", name)
    if isinstance(exc, ImproperlyConfigured):
        return FailureClassification(FailureKind.TRANSIENT, "CONFIGURATION_ERROR", name)
    return FailureClassification(FailureKind.TRANSIENT, "UNEXPECTED_ERROR", name)


def record_mqtt_delivery_failure(
    *,
    delivery_id: UUID,
    worker_id: str,
    lease_token: UUID,
    failure: FailureClassification,
    policy: RetryPolicy | None = None,
    now: datetime | None = None,
) -> IotMqttDelivery | None:
    """Persist the failure of a fenced attempt; returns the row or ``None``.

    Only the worker whose token is still the current one may record it. The
    lease may have expired as long as nobody re-claimed the row (the token is
    the fence). Returns ``None`` when the fence no longer matches.
    """
    if failure.kind is FailureKind.LEASE_LOST:
        return None
    policy = policy or RetryPolicy()
    require_mariadb(DEFAULT_DB_ALIAS)
    now = now or timezone.now()
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        row = IotMqttDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if (
            row is None
            or row.state != State.PROCESSING
            or row.lease_owner != worker_id
            or row.lease_token != lease_token
        ):
            return None
        if not failure.retryable or policy.exhausted(row.attempt_count):
            code = failure.code if not failure.retryable else MAX_ATTEMPTS_EXCEEDED
            detail = (
                failure.safe_detail
                if not failure.retryable
                else f"{failure.code}: {failure.safe_detail}"
            )
            dead_letter_row(
                row,
                code=code,
                last_error_code=failure.code,
                safe_detail=detail,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            return row
        row.state = State.RETRY_PENDING
        row.next_attempt_at = now + policy.delay_for(row.attempt_count)
        _clear_lease(row)
        row.last_error_code = failure.code
        row.last_error_safe = failure.safe_detail[:1000]
        row.save(
            update_fields=[
                "state",
                "next_attempt_at",
                "lease_owner",
                "lease_token",
                "lease_expires_at",
                "last_error_code",
                "last_error_safe",
                "updated_at",
            ]
        )
        IotMqttDeliveryEvent.objects.create(
            delivery=row,
            event_type="RETRY_SCHEDULED",
            from_state=State.PROCESSING,
            to_state=State.RETRY_PENDING,
            attempt_number=row.attempt_count,
            worker_id=worker_id,
            lease_token=lease_token,
            result_code=failure.code,
            correlation_id=row.id,
            safe_detail=(
                f"{failure.safe_detail}; próximo intento {row.next_attempt_at.isoformat()}"
            )[:1000],
        )
    logger.warning(
        "entrega %s en RETRY_PENDING (intento %s/%s, %s)",
        delivery_id,
        row.attempt_count,
        policy.max_attempts,
        failure.code,
    )
    return row


def dead_letter_row(
    row: IotMqttDelivery,
    *,
    code: str,
    last_error_code: str | None,
    safe_detail: str,
    worker_id: str | None,
    lease_token: UUID | None,
) -> None:
    """Move a row already locked by the caller to ``DEAD_LETTER`` with an event."""
    previous = row.state
    row.state = State.DEAD_LETTER
    row.next_attempt_at = None
    _clear_lease(row)
    row.reason_code = code
    if last_error_code is not None:
        row.last_error_code = last_error_code
    row.last_error_safe = safe_detail[:1000]
    row.save(
        update_fields=[
            "state",
            "next_attempt_at",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "reason_code",
            "last_error_code",
            "last_error_safe",
            "updated_at",
        ]
    )
    IotMqttDeliveryEvent.objects.create(
        delivery=row,
        event_type="DEAD_LETTERED",
        from_state=previous,
        to_state=State.DEAD_LETTER,
        attempt_number=row.attempt_count,
        worker_id=worker_id,
        lease_token=lease_token,
        result_code=code,
        correlation_id=row.id,
        safe_detail=safe_detail[:1000],
    )
    logger.error(
        "entrega %s en DEAD_LETTER tras %s intentos (%s); su carril queda bloqueado "
        "hasta resolución explícita",
        row.pk,
        row.attempt_count,
        code,
    )


def _clear_lease(row: IotMqttDelivery) -> None:
    row.lease_owner = None
    row.lease_token = None
    row.lease_expires_at = None


def _env_int(values, name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        raise RetryPolicyError(f"{name} debe ser un entero") from None


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_ALLOWED_ATTEMPTS",
    "MAX_ATTEMPTS_EXCEEDED",
    "FailureClassification",
    "FailureKind",
    "RetryPolicy",
    "RetryPolicyError",
    "classify_delivery_failure",
    "dead_letter_row",
    "record_mqtt_delivery_failure",
]
