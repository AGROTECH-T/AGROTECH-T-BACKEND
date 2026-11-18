# ruff: noqa: S101
"""HARDEN-01: política de reintentos, clasificación de fallos y fecha de medición.

Pruebas puras, sin base de datos ni red.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, OperationalError

from apps.iot.services.ingestion import (
    MEASURED_AT_MAX_AGE,
    MEASURED_AT_MAX_FUTURE_SKEW,
    MeasuredAtSource,
    resolve_measured_at,
)
from apps.iot.services.mqtt_delivery_failures import (
    MAX_ALLOWED_ATTEMPTS,
    FailureKind,
    RetryPolicy,
    RetryPolicyError,
    classify_delivery_failure,
)
from apps.iot.services.mqtt_delivery_processing import (
    DeliveryPayloadMismatchError,
    IngestionContractError,
    LeaseLostError,
)
from apps.iot.services.mqtt_payload_crypto import (
    MqttPayloadCryptoError,
    MqttPayloadKeyUnavailable,
)

# --------------------------------------------------------------------------
# D. Backoff exponencial
# --------------------------------------------------------------------------


def test_backoff_is_exponential_and_capped():
    policy = RetryPolicy(max_attempts=10, base_delay_seconds=5, max_delay_seconds=60)

    delays = [policy.delay_for(attempt).total_seconds() for attempt in range(1, 7)]

    assert delays == [5, 10, 20, 40, 60, 60]


def test_backoff_is_safe_for_huge_or_invalid_attempts():
    policy = RetryPolicy(max_attempts=10, base_delay_seconds=5, max_delay_seconds=900)

    assert policy.delay_for(10**9) == timedelta(seconds=900)
    assert policy.delay_for(0) == timedelta(seconds=5)
    assert policy.delay_for(-3) == timedelta(seconds=5)
    assert policy.delay_for("x") == timedelta(seconds=5)  # type: ignore[arg-type]


def test_exhaustion_is_reached_at_the_configured_limit():
    policy = RetryPolicy(max_attempts=3)

    assert not policy.exhausted(2)
    assert policy.exhausted(3)
    assert policy.exhausted(30000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": MAX_ALLOWED_ATTEMPTS + 1},
        {"base_delay_seconds": 0},
        {"base_delay_seconds": 10, "max_delay_seconds": 5},
        {"max_delay_seconds": 86_401},
        {"max_attempts": "8"},
        {"max_attempts": True},
    ],
)
def test_invalid_policies_are_rejected(kwargs):
    with pytest.raises(RetryPolicyError):
        RetryPolicy(**kwargs)


def test_attempt_ceiling_cannot_overflow_small_integer_column():
    assert MAX_ALLOWED_ATTEMPTS < 32767


def test_policy_from_environment():
    policy = RetryPolicy.from_env(
        {
            "IOT_MQTT_DELIVERY_MAX_ATTEMPTS": "4",
            "IOT_MQTT_DELIVERY_RETRY_BASE_SECONDS": "2",
            "IOT_MQTT_DELIVERY_RETRY_MAX_SECONDS": "30",
        }
    )

    assert (policy.max_attempts, policy.base_delay_seconds, policy.max_delay_seconds) == (4, 2, 30)
    assert RetryPolicy.from_env({}) == RetryPolicy()
    with pytest.raises(RetryPolicyError):
        RetryPolicy.from_env({"IOT_MQTT_DELIVERY_MAX_ATTEMPTS": "muchos"})


# --------------------------------------------------------------------------
# C/F/K. Clasificación de errores
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind", "code"),
    [
        (OperationalError("db"), FailureKind.TRANSIENT, "DATABASE_ERROR"),
        (RuntimeError("x"), FailureKind.TRANSIENT, "UNEXPECTED_ERROR"),
        (ImproperlyConfigured("x"), FailureKind.TRANSIENT, "CONFIGURATION_ERROR"),
        (IntegrityError("x"), FailureKind.PERMANENT, "DATABASE_INTEGRITY_ERROR"),
        (DeliveryPayloadMismatchError("x"), FailureKind.PERMANENT, "PAYLOAD_FINGERPRINT_MISMATCH"),
        (IngestionContractError("x"), FailureKind.PERMANENT, "INGESTION_CONTRACT_ERROR"),
        (
            MqttPayloadKeyUnavailable("x"),
            FailureKind.KEY_UNAVAILABLE,
            "MQTT_PAYLOAD_KEY_UNAVAILABLE",
        ),
        (MqttPayloadCryptoError("x"), FailureKind.CRYPTO_INVALID, "MQTT_PAYLOAD_AUTH_FAILED"),
        (LeaseLostError("x"), FailureKind.LEASE_LOST, "LEASE_LOST"),
    ],
)
def test_failure_classification(exc, kind, code):
    failure = classify_delivery_failure(exc)

    assert failure.kind is kind
    assert failure.code == code
    assert failure.safe_detail == type(exc).__name__


def test_crypto_errors_are_not_all_transient():
    missing_key = classify_delivery_failure(MqttPayloadKeyUnavailable("x"))
    forged = classify_delivery_failure(MqttPayloadCryptoError("x"))

    assert missing_key.retryable is True
    assert forged.retryable is False
    assert missing_key.pauses_globally and forged.pauses_globally


def test_classification_never_echoes_exception_messages():
    failure = classify_delivery_failure(RuntimeError("SECRETO-payload-7f3a"))

    assert "SECRETO" not in failure.safe_detail
    assert "SECRETO" not in failure.code


# --------------------------------------------------------------------------
# O. Fechas futuras y antiguas (S1)
# --------------------------------------------------------------------------

SERVER = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def test_synced_gateway_time_within_tolerance_is_used():
    rx = SERVER - timedelta(minutes=3)

    assert resolve_measured_at(lora_rx_at=rx, clock_synced=True, server_received_at=SERVER) == (
        rx,
        False,
        MeasuredAtSource.GATEWAY,
    )


def test_small_future_skew_is_tolerated():
    rx = SERVER + MEASURED_AT_MAX_FUTURE_SKEW

    measured, approximate, source = resolve_measured_at(
        lora_rx_at=rx, clock_synced=True, server_received_at=SERVER
    )

    assert (measured, approximate, source) == (rx, False, MeasuredAtSource.GATEWAY)


def test_future_time_beyond_tolerance_falls_back_to_server_time():
    rx = SERVER + MEASURED_AT_MAX_FUTURE_SKEW + timedelta(seconds=1)

    measured, approximate, source = resolve_measured_at(
        lora_rx_at=rx, clock_synced=True, server_received_at=SERVER
    )

    assert measured == SERVER
    assert approximate is True
    assert source is MeasuredAtSource.SERVER_FUTURE


def test_far_future_year_cannot_become_latest_reading():
    rx = datetime(2099, 1, 1, tzinfo=UTC)

    measured, approximate, _ = resolve_measured_at(
        lora_rx_at=rx, clock_synced=True, server_received_at=SERVER
    )

    assert measured == SERVER and approximate


def test_old_buffered_time_within_max_age_is_kept():
    rx = SERVER - MEASURED_AT_MAX_AGE

    assert resolve_measured_at(lora_rx_at=rx, clock_synced=True, server_received_at=SERVER)[0] == rx


def test_time_older_than_max_age_falls_back_to_server_time():
    rx = SERVER - MEASURED_AT_MAX_AGE - timedelta(seconds=1)

    measured, approximate, source = resolve_measured_at(
        lora_rx_at=rx, clock_synced=True, server_received_at=SERVER
    )

    assert (measured, approximate, source) == (SERVER, True, MeasuredAtSource.SERVER_TOO_OLD)


@pytest.mark.parametrize(("rx", "synced"), [(None, True), (SERVER, False)])
def test_unsynced_or_missing_time_uses_server_time(rx, synced):
    measured, approximate, source = resolve_measured_at(
        lora_rx_at=rx, clock_synced=synced, server_received_at=SERVER
    )

    assert (measured, approximate, source) == (SERVER, True, MeasuredAtSource.SERVER_UNSYNCED)
