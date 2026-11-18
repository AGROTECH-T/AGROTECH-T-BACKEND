# ruff: noqa: S101, S105, S106, F811
"""Protección de los servicios MQTT durables existentes (BUILD-01D-A).

Cubre recepción durable, leases con fencing, orden FIFO por carril, pausas,
procesamiento y worker contra MariaDB real. No modifica lógica de producción:
documenta el comportamiento actual antes de BUILD-01D-B.

``transaction=True`` porque el worker llama ``close_old_connections`` y los
servicios confirman transacciones propias (claim y procesamiento son commits
separados). Solo mediante el orquestador desechable IoT
(``config.settings_test_disposable_iot``); ``require_mariadb`` rechaza otros
motores.
"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.exceptions import FieldError, ValidationError
from django.db import OperationalError
from django.utils import timezone

import apps.iot.services as iot
import apps.iot.services.mqtt_delivery_leases as leases_module
import apps.iot.services.mqtt_delivery_processing as processing_module
from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
    IotMqttDeliveryPauseEvent,
    Measurement,
    ReceivedFrame,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.mqtt_delivery_leases import (
    LEASE_SECONDS,
    claim_mqtt_delivery,
    renew_mqtt_delivery_lease,
    verify_mqtt_delivery_lease,
)
from apps.iot.services.mqtt_delivery_pauses import (
    GLOBAL_SCOPE_KEY,
    mqtt_delivery_pause_active,
    pause_mqtt_deliveries,
    resume_mqtt_deliveries,
)
from apps.iot.services.mqtt_delivery_processing import (
    DeliveryPayloadMismatchError,
    DeliveryProcessingOutcome,
    LeaseLostError,
    process_encrypted_mqtt_delivery,
    process_mqtt_delivery,
)
from apps.iot.services.mqtt_delivery_receipts import (
    MqttReceiptStatus,
    build_transport_key,
    register_mqtt_delivery,
)
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.services.mqtt_payload_crypto import (
    MqttPayloadCryptoError,
    encrypt_mqtt_payload,
)
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    LANE_TOPIC,
    frame_for,
    lanes,
    mqtt_key_settings,
    raw_payload,
    register,
    register_lane,
    world,
)

pytestmark = pytest.mark.django_db(transaction=True)

SENSITIVE_MARKER = "SENSITIVE-MARKER-7f3a"
FINAL_OR_BLOCKED = (
    State.ACCEPTED,
    State.DUPLICATE,
    State.REJECTED,
    State.CONFLICT,
    State.RESOLVED,
    State.DEAD_LETTER,
)


def _events(delivery, event_type=None):
    queryset = IotMqttDeliveryEvent.objects.filter(delivery=delivery)
    if event_type is not None:
        queryset = queryset.filter(event_type=event_type)
    return queryset


def _expire_lease(delivery) -> None:
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        lease_expires_at=timezone.now() - timedelta(minutes=5)
    )


def _registered_world_delivery(world, *, seq=120, coordinator_id=None):
    """Register as the consumer does for an authorized frame (candidate set).

    A delivery without candidate currently cannot be claimed (defect D1, see
    ``test_delivery_without_candidate_can_be_claimed``), so the processing and
    worker protections use the realistic candidate path.
    """
    payload = raw_payload(frame_for(world, seq=seq))
    candidate = world.coordinator.pk if coordinator_id is None else coordinator_id
    result = register(payload, topic=world.topic, coordinator_id=candidate)
    assert result.status is MqttReceiptStatus.PERSISTED
    return result.delivery, payload


@pytest.fixture
def iot_log_records():
    """Captura directa de registros bajo ``apps.iot`` (independiente de LOGGING)."""
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("apps.iot")
    handler = _Collector(level=logging.DEBUG)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _rendered(records) -> str:
    return "\n".join(record.getMessage() for record in records)


# --------------------------------------------------------------------------
# A. Recepción durable
# --------------------------------------------------------------------------


def test_valid_message_creates_received_delivery_with_event(mqtt_key_settings):
    payload = b'{"probe":"valid"}'

    result = register(payload)

    assert result.status is MqttReceiptStatus.PERSISTED
    delivery = IotMqttDelivery.objects.get()
    assert delivery.pk == result.delivery.pk
    assert delivery.state == State.RECEIVED
    assert delivery.attempt_count == 0
    assert delivery.lease_token is None
    assert delivery.payload_size == len(payload)
    assert bytes(delivery.transport_key) == build_transport_key(
        topic=LANE_TOPIC, payload=payload
    )
    assert payload not in bytes(delivery.payload_ciphertext)
    received = _events(delivery).get()
    assert received.event_type == "RECEIVED"
    assert received.to_state == State.RECEIVED
    assert received.correlation_id == delivery.pk


def test_delivery_and_event_are_written_atomically(mqtt_key_settings, monkeypatch):
    def failing_create(**_kwargs):
        raise OperationalError("fallo simulado al escribir el evento")

    monkeypatch.setattr(IotMqttDeliveryEvent.objects, "create", failing_create)

    result = register(b'{"probe":"atomic"}')

    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert result.delivery is None
    assert IotMqttDelivery.objects.count() == 0


def test_redelivery_with_same_transport_key_does_not_create_another_row(
    mqtt_key_settings,
):
    payload = b'{"probe":"redelivery"}'
    first = register(payload)

    second = register(payload)

    assert second.status is MqttReceiptStatus.REDELIVERY
    assert second.delivery.pk == first.delivery.pk
    assert IotMqttDelivery.objects.count() == 1
    assert _events(first.delivery, "RECEIVED").count() == 1
    assert _events(first.delivery, "REDELIVERY").count() == 1


def test_same_payload_on_different_topic_is_a_distinct_delivery(mqtt_key_settings):
    payload = b'{"probe":"topic"}'
    first = register(payload)
    second = register(payload, topic="agrotech/v1/gw/GW-2B3C4D5E6F/up")

    assert second.status is MqttReceiptStatus.PERSISTED
    assert first.delivery.pk != second.delivery.pk


def test_inconsistent_stored_metadata_is_not_accepted_as_redelivery(mqtt_key_settings):
    payload = b'{"probe":"identity"}'
    first = register(payload)
    IotMqttDelivery.objects.filter(pk=first.delivery.pk).update(
        payload_size=len(payload) + 1
    )

    second = register(payload)

    assert second.status is MqttReceiptStatus.IDENTITY_INCONSISTENCY
    assert second.error_code == "TRANSPORT_KEY_METADATA_MISMATCH"
    assert IotMqttDelivery.objects.count() == 1
    assert _events(first.delivery, "IDENTITY_INCONSISTENCY").count() == 1


def _envelope_kwargs(payload: bytes, *, topic: str = LANE_TOPIC, **overrides):
    envelope = encrypt_mqtt_payload(
        payload=payload,
        transport_key=build_transport_key(topic=topic, payload=payload),
    )
    kwargs = {
        "topic": topic,
        "payload": payload,
        "qos": 1,
        "payload_ciphertext": envelope.ciphertext,
        "payload_nonce": envelope.nonce,
        "payload_tag": envelope.tag,
        "payload_key_version": envelope.key_version,
        "max_payload_bytes": MAX_MESSAGE_BYTES,
    }
    kwargs.update(overrides)
    return kwargs


def test_envelope_for_another_payload_is_rejected_before_any_write(mqtt_key_settings):
    payload = b'{"probe":"AAAA"}'
    other = b'{"probe":"BBBB"}'
    foreign = _envelope_kwargs(other)

    with pytest.raises(MqttPayloadCryptoError):
        register_mqtt_delivery(**{**foreign, "payload": payload})

    assert IotMqttDelivery.objects.count() == 0


def test_tampered_tag_is_rejected_before_any_write(mqtt_key_settings):
    kwargs = _envelope_kwargs(b'{"probe":"tag"}')
    kwargs["payload_tag"] = bytes(16)

    with pytest.raises(MqttPayloadCryptoError):
        register_mqtt_delivery(**kwargs)

    assert IotMqttDelivery.objects.count() == 0


@pytest.mark.parametrize(
    "override",
    [
        {"qos": 0},
        {"payload_nonce": b"\x00" * 11},
        {"payload_tag": b"\x00" * 15},
        {"payload_key_version": ""},
        {"max_payload_bytes": 4},
    ],
)
def test_invalid_envelope_inputs_raise_without_writes(mqtt_key_settings, override):
    kwargs = _envelope_kwargs(b'{"probe":"inputs"}', **override)

    with pytest.raises(ValueError):
        register_mqtt_delivery(**kwargs)

    assert IotMqttDelivery.objects.count() == 0


def test_plaintext_as_ciphertext_is_rejected(mqtt_key_settings):
    payload = b'{"probe":"plain"}'
    kwargs = _envelope_kwargs(payload, payload_ciphertext=payload)

    with pytest.raises(ValueError):
        register_mqtt_delivery(**kwargs)

    assert IotMqttDelivery.objects.count() == 0


def test_persistence_error_is_not_presented_as_durable_and_hides_details(
    mqtt_key_settings, monkeypatch
):
    payload = f'{{"probe":"{SENSITIVE_MARKER}"}}'.encode()

    def failing_create(**_kwargs):
        raise OperationalError(f"driver text with {SENSITIVE_MARKER}")

    monkeypatch.setattr(IotMqttDelivery.objects, "create", failing_create)

    result = register(payload)

    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert result.status not in {MqttReceiptStatus.PERSISTED, MqttReceiptStatus.REDELIVERY}
    assert result.delivery is None
    assert result.error_code == "OperationalError"
    assert SENSITIVE_MARKER not in repr(result)
    assert IotMqttDelivery.objects.count() == 0


def test_delivery_events_are_append_only_through_the_orm(mqtt_key_settings):
    result = register(b'{"probe":"append-only"}')
    event = _events(result.delivery).get()

    with pytest.raises(ValidationError):
        IotMqttDeliveryEvent.objects.filter(pk=event.pk).update(safe_detail="x")
    with pytest.raises(ValidationError):
        event.delete()
    with pytest.raises(ValidationError):
        event.save()
    assert _events(result.delivery).get().safe_detail == "Recepción durable registrada"


def test_validation_errors_do_not_echo_payload(mqtt_key_settings):
    payload = f'{{"probe":"{SENSITIVE_MARKER}"}}'.encode()
    kwargs = _envelope_kwargs(payload, max_payload_bytes=4)

    with pytest.raises(ValueError) as excinfo:
        register_mqtt_delivery(**kwargs)

    assert SENSITIVE_MARKER not in str(excinfo.value)


# --------------------------------------------------------------------------
# C. Leases
# --------------------------------------------------------------------------


def test_eligible_delivery_can_be_claimed(lanes):
    delivery = register_lane("claim", coordinator_id=lanes[0].pk, received_offset_s=0)

    before = timezone.now()
    claimed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")

    assert claimed is not None
    claimed.refresh_from_db()
    assert claimed.state == State.PROCESSING
    assert claimed.lease_owner == "w1"
    assert claimed.lease_token is not None
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at >= before + timedelta(seconds=LEASE_SECONDS - 1)
    event = _events(delivery, "LEASE_CLAIMED").get()
    assert event.worker_id == "w1"
    assert event.lease_token == claimed.lease_token
    assert event.from_state == State.RECEIVED
    assert event.to_state == State.PROCESSING


def test_live_lease_cannot_be_claimed_by_another_worker(lanes):
    delivery = register_lane("live", coordinator_id=lanes[0].pk, received_offset_s=0)
    first = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")

    second = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2")

    assert second is None
    delivery.refresh_from_db()
    assert delivery.lease_owner == "w1"
    assert delivery.lease_token == first.lease_token
    assert delivery.attempt_count == 1


def test_expired_lease_can_be_reclaimed_with_a_new_fencing_token(lanes):
    delivery = register_lane("expired", coordinator_id=lanes[0].pk, received_offset_s=0)
    first = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")
    _expire_lease(delivery)

    second = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2")

    assert second is not None
    assert second.lease_owner == "w2"
    assert second.lease_token != first.lease_token
    assert second.attempt_count == 2
    assert _events(delivery, "LEASE_CLAIMED").count() == 2


def test_each_claim_generates_a_distinct_token(lanes):
    delivery = register_lane("tokens", coordinator_id=lanes[0].pk, received_offset_s=0)
    tokens = set()
    for index in range(3):
        claimed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id=f"w{index}")
        tokens.add(claimed.lease_token)
        _expire_lease(delivery)

    assert len(tokens) == 3


def test_stale_token_cannot_renew_or_verify(lanes):
    delivery = register_lane("stale", coordinator_id=lanes[0].pk, received_offset_s=0)
    old = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")
    _expire_lease(delivery)
    new = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2")

    assert not renew_mqtt_delivery_lease(
        delivery_id=delivery.pk, worker_id="w1", lease_token=old.lease_token
    )
    assert not renew_mqtt_delivery_lease(
        delivery_id=delivery.pk, worker_id="w2", lease_token=old.lease_token
    )
    assert not renew_mqtt_delivery_lease(
        delivery_id=delivery.pk, worker_id="w1", lease_token=new.lease_token
    )
    delivery.refresh_from_db()
    assert not verify_mqtt_delivery_lease(
        delivery, worker_id="w1", lease_token=old.lease_token
    )
    assert verify_mqtt_delivery_lease(
        delivery, worker_id="w2", lease_token=new.lease_token
    )


def test_owner_can_renew_live_lease_but_not_expired_lease(lanes):
    delivery = register_lane("renew", coordinator_id=lanes[0].pk, received_offset_s=0)
    claimed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")

    assert renew_mqtt_delivery_lease(
        delivery_id=delivery.pk, worker_id="w1", lease_token=claimed.lease_token
    )
    assert _events(delivery, "LEASE_RENEWED").count() == 1

    _expire_lease(delivery)
    assert not renew_mqtt_delivery_lease(
        delivery_id=delivery.pk, worker_id="w1", lease_token=claimed.lease_token
    )


def test_stale_worker_cannot_complete_delivery(world):
    delivery, payload = _registered_world_delivery(world)
    old = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")
    _expire_lease(delivery)
    new = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2")

    result = process_mqtt_delivery(
        delivery_id=delivery.pk,
        payload=payload,
        lease_owner="w1",
        lease_token=old.lease_token,
    )

    assert result.outcome is DeliveryProcessingOutcome.NOT_READY
    assert Measurement.objects.count() == 0
    delivery.refresh_from_db()
    assert delivery.state == State.PROCESSING
    assert delivery.lease_token == new.lease_token
    assert not _events(delivery, "PROCESSING_COMPLETED").exists()


@pytest.mark.parametrize("state", FINAL_OR_BLOCKED)
def test_final_or_blocked_delivery_cannot_be_claimed(lanes, state):
    delivery = register_lane("final", coordinator_id=lanes[0].pk, received_offset_s=0)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(state=state)

    assert claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1") is None
    delivery.refresh_from_db()
    assert delivery.state == state
    assert delivery.attempt_count == 0
    assert not _events(delivery, "LEASE_CLAIMED").exists()


def test_retry_pending_respects_next_attempt_at(lanes):
    delivery = register_lane("retry", coordinator_id=lanes[0].pk, received_offset_s=0)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        state=State.RETRY_PENDING,
        next_attempt_at=timezone.now() + timedelta(minutes=5),
    )
    assert claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1") is None

    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    claimed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")
    assert claimed is not None
    assert claimed.next_attempt_at is None


def test_fifo_order_is_respected_within_a_coordinator_lane(lanes):
    lane_a = lanes[0].pk
    first = register_lane("a1", coordinator_id=lane_a, received_offset_s=0)
    second = register_lane("a2", coordinator_id=lane_a, received_offset_s=10)

    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w1") is None
    claimed = claim_mqtt_delivery(delivery_id=first.pk, worker_id="w1")
    assert claimed is not None
    # The head is PROCESSING (not final): the follower is still blocked.
    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w2") is None

    IotMqttDelivery.objects.filter(pk=first.pk).update(
        state=State.REJECTED, lease_owner=None, lease_token=None, lease_expires_at=None
    )
    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w2") is not None


def test_dead_letter_head_blocks_its_lane(lanes):
    lane_a = lanes[0].pk
    head = register_lane("dlq", coordinator_id=lane_a, received_offset_s=0)
    follower = register_lane("after", coordinator_id=lane_a, received_offset_s=10)
    IotMqttDelivery.objects.filter(pk=head.pk).update(state=State.DEAD_LETTER)

    assert claim_mqtt_delivery(delivery_id=follower.pk, worker_id="w1") is None


def test_independent_lanes_do_not_block_each_other(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    register_lane("a1", coordinator_id=lane_a, received_offset_s=0)
    b_delivery = register_lane("b1", coordinator_id=lane_b, received_offset_s=10)
    unknown = register_lane("n1", coordinator_id=None, received_offset_s=20)

    assert claim_mqtt_delivery(delivery_id=b_delivery.pk, worker_id="w1") is not None
    assert claim_mqtt_delivery(delivery_id=unknown.pk, worker_id="w2") is not None


def test_unknown_coordinator_lane_does_not_block_known_lane(lanes):
    register_lane("n1", coordinator_id=None, received_offset_s=0)
    known = register_lane("a1", coordinator_id=lanes[0].pk, received_offset_s=10)

    assert claim_mqtt_delivery(delivery_id=known.pk, worker_id="w1") is not None


def test_unknown_coordinator_lane_does_not_block_authenticated_lane(lanes):
    lane_a = lanes[0].pk
    register_lane("n1", coordinator_id=None, received_offset_s=0)
    authenticated = register_lane("auth", coordinator_id=None, received_offset_s=10)
    IotMqttDelivery.objects.filter(pk=authenticated.pk).update(
        authenticated_coordinator_id=lane_a
    )

    assert claim_mqtt_delivery(delivery_id=authenticated.pk, worker_id="w1") is not None


def test_authenticated_lane_does_not_block_unknown_coordinator_lane(lanes):
    lane_a = lanes[0].pk
    head = register_lane("auth", coordinator_id=None, received_offset_s=0)
    IotMqttDelivery.objects.filter(pk=head.pk).update(authenticated_coordinator_id=lane_a)
    candidate = register_lane("cand", coordinator_id=lane_a, received_offset_s=5)
    unknown = register_lane("n1", coordinator_id=None, received_offset_s=10)

    assert claim_mqtt_delivery(delivery_id=unknown.pk, worker_id="w1") is not None
    # The authenticated head still blocks its own lane (candidate follower).
    assert claim_mqtt_delivery(delivery_id=candidate.pk, worker_id="w2") is None


def test_delivery_without_candidate_can_be_claimed(lanes):
    unknown = register_lane("n1", coordinator_id=None, received_offset_s=0)

    claimed = claim_mqtt_delivery(delivery_id=unknown.pk, worker_id="w1")

    assert claimed is not None
    claimed.refresh_from_db()
    assert claimed.state == State.PROCESSING
    assert claimed.coordinator_id is None
    assert claimed.authenticated_coordinator_id is None
    assert claimed.lease_owner == "w1"
    assert claimed.lease_token is not None
    assert claimed.attempt_count == 1
    assert _events(unknown, "LEASE_CLAIMED").count() == 1


def test_unknown_coordinator_lane_is_fifo_among_itself(lanes):
    first = register_lane("n1", coordinator_id=None, received_offset_s=0)
    second = register_lane("n2", coordinator_id=None, received_offset_s=10)

    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w1") is None
    assert claim_mqtt_delivery(delivery_id=first.pk, worker_id="w1") is not None
    # Head PROCESSING (not final) keeps blocking; a final head releases.
    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w2") is None
    IotMqttDelivery.objects.filter(pk=first.pk).update(
        state=State.REJECTED, lease_owner=None, lease_token=None, lease_expires_at=None
    )
    assert claim_mqtt_delivery(delivery_id=second.pk, worker_id="w2") is not None


def test_unknown_coordinator_lane_fencing_rejects_stale_worker(lanes):
    unknown = register_lane("n1", coordinator_id=None, received_offset_s=0)
    old = claim_mqtt_delivery(delivery_id=unknown.pk, worker_id="w1")
    _expire_lease(unknown)
    new = claim_mqtt_delivery(delivery_id=unknown.pk, worker_id="w2")

    assert new is not None and new.lease_token != old.lease_token
    assert not renew_mqtt_delivery_lease(
        delivery_id=unknown.pk, worker_id="w1", lease_token=old.lease_token
    )
    stale =process_encrypted_mqtt_delivery(
        delivery_id=unknown.pk, lease_owner="w1", lease_token=old.lease_token
    )
    assert stale.outcome is DeliveryProcessingOutcome.NOT_READY
    unknown.refresh_from_db()
    assert unknown.state == State.PROCESSING
    assert unknown.lease_token == new.lease_token
    assert not _events(unknown, "PROCESSING_COMPLETED").exists()


def test_authenticated_coordinator_defines_the_lane(lanes):
    lane_a = lanes[0].pk
    head = register_lane("auth", coordinator_id=None, received_offset_s=0)
    IotMqttDelivery.objects.filter(pk=head.pk).update(authenticated_coordinator_id=lane_a)
    follower = register_lane("cand", coordinator_id=lane_a, received_offset_s=10)

    assert claim_mqtt_delivery(delivery_id=follower.pk, worker_id="w1") is None


def test_claim_rejects_invalid_identities(lanes):
    delivery = register_lane("ids", coordinator_id=lanes[0].pk, received_offset_s=0)
    for worker_id in ("", "   ", "x" * 101, None):
        with pytest.raises(ValueError):
            claim_mqtt_delivery(delivery_id=delivery.pk, worker_id=worker_id)
    with pytest.raises(ValueError):
        claim_mqtt_delivery(delivery_id=str(delivery.pk), worker_id="w1")
    assert claim_mqtt_delivery(delivery_id=uuid4(), worker_id="w1") is None


# --------------------------------------------------------------------------
# D. Pausas
# --------------------------------------------------------------------------


def test_global_pause_blocks_new_claims_until_resumed(lanes):
    delivery = register_lane("global", coordinator_id=lanes[0].pk, received_offset_s=0)
    pause = pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="pytest")

    assert pause.active_scope_key == GLOBAL_SCOPE_KEY
    assert mqtt_delivery_pause_active(authenticated_coordinator_id=None)
    assert claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1") is None

    resumed = resume_mqtt_deliveries(reason_code="OPS_DONE", process_id="pytest")

    assert resumed.pk == pause.pk
    assert claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1") is not None


def test_coordinator_pause_only_affects_its_lane(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    a_delivery = register_lane("a", coordinator_id=lane_a, received_offset_s=0)
    b_delivery = register_lane("b", coordinator_id=lane_b, received_offset_s=10)
    n_delivery = register_lane("n", coordinator_id=None, received_offset_s=20)
    pause_mqtt_deliveries(reason_code="OPS_TEST", coordinator_id=lane_a, process_id="pytest")

    assert mqtt_delivery_pause_active(
        authenticated_coordinator_id=None, coordinator_candidate_id=lane_a
    )
    assert not mqtt_delivery_pause_active(
        authenticated_coordinator_id=None, coordinator_candidate_id=lane_b
    )
    assert claim_mqtt_delivery(delivery_id=a_delivery.pk, worker_id="w1") is None
    assert claim_mqtt_delivery(delivery_id=b_delivery.pk, worker_id="w1") is not None
    assert claim_mqtt_delivery(delivery_id=n_delivery.pk, worker_id="w1") is not None


def test_repeated_pause_is_idempotent(lanes):
    first = pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="pytest")
    second = pause_mqtt_deliveries(reason_code="OPS_OTHER", process_id="pytest-2")

    assert second.pk == first.pk
    assert IotMqttDeliveryPause.objects.filter(is_active=True).count() == 1
    assert IotMqttDeliveryPauseEvent.objects.filter(event_type="PAUSED").count() == 1


def test_resume_records_event_and_keeps_history(lanes, admin):
    first = pause_mqtt_deliveries(reason_code="OPS_TEST", actor=admin)
    resume_mqtt_deliveries(reason_code="OPS_DONE", process_id="pytest")
    second = pause_mqtt_deliveries(reason_code="OPS_AGAIN", process_id="pytest")

    assert second.pk != first.pk
    first.refresh_from_db()
    assert first.is_active is False
    assert first.active_scope_key is None
    assert first.resumed_at is not None
    assert first.resumed_process_id == "pytest"
    assert first.started_by_id == admin.pk
    events = IotMqttDeliveryPauseEvent.objects.filter(pause=first)
    assert sorted(events.values_list("event_type", flat=True)) == ["PAUSED", "RESUMED"]
    resumed = events.get(event_type="RESUMED")
    assert resumed.reason_code == "OPS_DONE"
    assert resumed.process_id == "pytest"
    assert IotMqttDeliveryPause.objects.count() == 2
    assert IotMqttDeliveryPauseEvent.objects.count() == 3


def test_resume_without_active_pause_is_a_noop(lanes):
    assert resume_mqtt_deliveries(reason_code="OPS_DONE", process_id="pytest") is None
    assert IotMqttDeliveryPauseEvent.objects.count() == 0


def test_pause_requires_audit_identity_and_valid_scope(lanes):
    with pytest.raises(ValueError):
        pause_mqtt_deliveries(reason_code="OPS_TEST")
    with pytest.raises(ValueError):
        pause_mqtt_deliveries(reason_code="", process_id="pytest")
    with pytest.raises(ValueError):
        pause_mqtt_deliveries(reason_code="OPS_TEST", coordinator_id=0, process_id="p")
    max_pk = max(coordinator.pk for coordinator in lanes)
    with pytest.raises(ValueError):
        pause_mqtt_deliveries(
            reason_code="OPS_TEST", coordinator_id=max_pk + 1000, process_id="p"
        )
    assert IotMqttDeliveryPause.objects.count() == 0


def test_pause_events_are_append_only(lanes):
    pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="pytest")
    event = IotMqttDeliveryPauseEvent.objects.get()

    with pytest.raises(ValidationError):
        IotMqttDeliveryPauseEvent.objects.filter(pk=event.pk).update(reason_code="X")
    with pytest.raises(ValidationError):
        event.delete()
    assert IotMqttDeliveryPauseEvent.objects.get().reason_code == "OPS_TEST"


# --------------------------------------------------------------------------
# E. Procesamiento
# --------------------------------------------------------------------------


def test_valid_delivery_is_processed_and_persisted(world):
    delivery, _payload = _registered_world_delivery(world)

    result = process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    assert result.outcome is DeliveryProcessingOutcome.PROCESSED
    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert delivery.result_code == "accepted"
    assert delivery.lease_owner is None
    assert delivery.lease_token is None
    assert delivery.lease_expires_at is None
    assert delivery.authenticated_coordinator_id == world.coordinator.pk
    assert Measurement.objects.count() == 2
    assert ReceivedFrame.objects.count() == 1
    completed = _events(delivery, "PROCESSING_COMPLETED").get()
    assert completed.to_state == State.ACCEPTED


def test_final_delivery_is_not_processed_again(world):
    delivery, payload = _registered_world_delivery(world)
    process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    again = process_encrypted_mqtt_delivery(delivery_id=delivery.pk)
    plain_again = process_mqtt_delivery(delivery_id=delivery.pk, payload=payload)

    assert again.outcome is DeliveryProcessingOutcome.ALREADY_FINAL
    assert plain_again.outcome is DeliveryProcessingOutcome.ALREADY_FINAL
    assert Measurement.objects.count() == 2
    assert _events(delivery, "PROCESSING_COMPLETED").count() == 1
    assert _events(delivery, "LEASE_CLAIMED").count() == 1


def test_altered_plaintext_is_not_processed(world):
    delivery, _payload = _registered_world_delivery(world)
    altered = raw_payload(frame_for(world, seq=999))

    with pytest.raises(DeliveryPayloadMismatchError):
        process_mqtt_delivery(delivery_id=delivery.pk, payload=altered)

    assert Measurement.objects.count() == 0
    assert ReceivedFrame.objects.count() == 0
    delivery.refresh_from_db()
    assert delivery.state not in {State.ACCEPTED, State.REJECTED}


def test_tampered_stored_ciphertext_is_not_processed(world):
    delivery, _payload = _registered_world_delivery(world)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(payload_tag=bytes(16))

    with pytest.raises(MqttPayloadCryptoError):
        process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    assert Measurement.objects.count() == 0
    delivery.refresh_from_db()
    assert delivery.state not in {State.ACCEPTED, State.REJECTED}


def test_lease_lost_during_processing_rolls_back_ingestion(world, monkeypatch):
    delivery, payload = _registered_world_delivery(world)
    claimed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1")
    shift = [timedelta(0)]
    real_now = timezone.now
    monkeypatch.setattr(
        leases_module, "timezone", SimpleNamespace(now=lambda: real_now() + shift[0])
    )
    real_invoke = processing_module._invoke_ingestion

    def invoke_then_lose_lease(**kwargs):
        outcome = real_invoke(**kwargs)
        assert Measurement.objects.count() == 2  # written inside the transaction
        shift[0] = timedelta(seconds=LEASE_SECONDS * 2)
        return outcome

    monkeypatch.setattr(processing_module, "_invoke_ingestion", invoke_then_lose_lease)

    with pytest.raises(LeaseLostError):
        process_mqtt_delivery(
            delivery_id=delivery.pk,
            payload=payload,
            lease_owner="w1",
            lease_token=claimed.lease_token,
        )

    assert Measurement.objects.count() == 0
    assert ReceivedFrame.objects.count() == 0
    delivery.refresh_from_db()
    assert delivery.state == State.PROCESSING
    assert delivery.lease_token == claimed.lease_token
    assert not _events(delivery, "PROCESSING_COMPLETED").exists()


def test_failed_ingestion_leaves_no_partial_measurements(world, monkeypatch):
    delivery, _payload = _registered_world_delivery(world)
    real_ingest = processing_module.ingest

    def ingest_then_fail(**kwargs):
        real_ingest(**kwargs)
        assert Measurement.objects.count() == 2
        raise OperationalError("fallo simulado tras escribir mediciones")

    monkeypatch.setattr(processing_module, "ingest", ingest_then_fail)

    with pytest.raises(OperationalError):
        process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    assert Measurement.objects.count() == 0
    assert ReceivedFrame.objects.count() == 0
    delivery.refresh_from_db()
    assert delivery.state == State.PROCESSING
    assert not _events(delivery, "PROCESSING_COMPLETED").exists()


def test_coordinator_identity_comes_from_message_not_candidate(world, lanes):
    unrelated = lanes[0]
    delivery, _payload = _registered_world_delivery(world, coordinator_id=unrelated.pk)

    process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert delivery.coordinator_id == unrelated.pk
    assert delivery.authenticated_coordinator_id == world.coordinator.pk


def test_candidate_coordinator_does_not_authenticate_invalid_hmac(world):
    payload = raw_payload(frame_for(world, seq=130, key=bytes(32)))
    result = register(payload, topic=world.topic, coordinator_id=world.coordinator.pk)

    processed = process_encrypted_mqtt_delivery(delivery_id=result.delivery.pk)

    assert processed.outcome is DeliveryProcessingOutcome.PROCESSED
    delivery = IotMqttDelivery.objects.get(pk=result.delivery.pk)
    assert delivery.state == State.REJECTED
    assert delivery.result_code == "rejected_hmac"
    assert delivery.authenticated_coordinator_id is None
    assert Measurement.objects.count() == 0


def test_invalid_payload_is_durably_rejected(lanes):
    delivery = register_lane("not-a-frame", coordinator_id=lanes[0].pk, received_offset_s=0)

    result = process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    assert result.outcome is DeliveryProcessingOutcome.PROCESSED
    delivery.refresh_from_db()
    assert delivery.state == State.REJECTED
    assert delivery.result_code == "rejected_invalid_payload"


def test_invalid_payload_without_candidate_is_durably_rejected(lanes):
    # The consumer always stores invalid envelopes without candidate.
    delivery = register_lane("not-a-frame", coordinator_id=None, received_offset_s=0)

    result = process_encrypted_mqtt_delivery(delivery_id=delivery.pk)

    assert result.outcome is DeliveryProcessingOutcome.PROCESSED
    delivery.refresh_from_db()
    assert delivery.state == State.REJECTED
    assert delivery.result_code == "rejected_invalid_payload"
    assert delivery.coordinator_id is None
    assert delivery.authenticated_coordinator_id is None
    assert delivery.lease_token is None
    assert Measurement.objects.count() == 0
    assert ReceivedFrame.objects.count() == 0
    assert _events(delivery, "PROCESSING_COMPLETED").get().to_state == State.REJECTED


def test_invalid_frame_without_candidate_is_rejected_without_measurements(world):
    payload = raw_payload(frame_for(world, seq=150, key=bytes(32)))
    result = register(payload, topic=world.topic, coordinator_id=None)

    processed = process_encrypted_mqtt_delivery(delivery_id=result.delivery.pk)

    assert processed.outcome is DeliveryProcessingOutcome.PROCESSED
    delivery = IotMqttDelivery.objects.get(pk=result.delivery.pk)
    assert delivery.state == State.REJECTED
    assert delivery.result_code == "rejected_hmac"
    assert delivery.authenticated_coordinator_id is None
    assert Measurement.objects.count() == 0


def test_valid_frame_without_candidate_authenticates_from_message(world):
    payload = raw_payload(frame_for(world, seq=151))
    result = register(payload, topic=world.topic, coordinator_id=None)

    process_encrypted_mqtt_delivery(delivery_id=result.delivery.pk)

    delivery = IotMqttDelivery.objects.get(pk=result.delivery.pk)
    assert delivery.state == State.ACCEPTED
    assert delivery.coordinator_id is None
    assert delivery.authenticated_coordinator_id == world.coordinator.pk
    assert Measurement.objects.count() == 2


# --------------------------------------------------------------------------
# F. Worker
# --------------------------------------------------------------------------


def test_worker_recovers_received_delivery(world):
    delivery, _payload = _registered_world_delivery(world)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert (batch.discovered, batch.claimed, batch.processed, batch.deferred) == (1, 1, 1, 0)
    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert Measurement.objects.count() == 2


def test_worker_does_not_claim_live_lease(world):
    delivery, _payload = _registered_world_delivery(world)
    held = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="other-worker")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.claimed == 0
    assert batch.processed == 0
    delivery.refresh_from_db()
    assert delivery.state == State.PROCESSING
    assert delivery.lease_owner == "other-worker"
    assert delivery.lease_token == held.lease_token
    assert Measurement.objects.count() == 0


def test_worker_recovers_expired_lease(world):
    delivery, _payload = _registered_world_delivery(world)
    crashed = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="crashed-worker")
    _expire_lease(delivery)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.processed == 1
    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert delivery.attempt_count == 2
    completed = _events(delivery, "PROCESSING_COMPLETED").get()
    assert completed.worker_id == "pytest-worker"
    assert completed.lease_token != crashed.lease_token
    assert Measurement.objects.count() == 2


def test_worker_respects_global_pause(world):
    delivery, _payload = _registered_world_delivery(world)
    pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="pytest")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.claimed == 0
    assert batch.processed == 0
    delivery.refresh_from_db()
    assert delivery.state == State.RECEIVED

    resume_mqtt_deliveries(reason_code="OPS_DONE", process_id="pytest")
    assert run_mqtt_delivery_worker_once(worker_id="pytest-worker").processed == 1


def test_worker_respects_coordinator_pause(world):
    delivery, _payload = _registered_world_delivery(
        world, coordinator_id=world.coordinator.pk
    )
    pause_mqtt_deliveries(
        reason_code="OPS_TEST", coordinator_id=world.coordinator.pk, process_id="pytest"
    )

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.processed == 0
    delivery.refresh_from_db()
    assert delivery.state == State.RECEIVED


def test_worker_does_not_report_failed_processing_as_success(
    world, monkeypatch, iot_log_records
):
    delivery, payload = _registered_world_delivery(world)

    def failing_ingest(**_kwargs):
        raise RuntimeError(f"fallo con {SENSITIVE_MARKER}")

    monkeypatch.setattr(processing_module, "ingest", failing_ingest)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.claimed == 1
    assert batch.processed == 0
    assert batch.deferred == 1
    delivery.refresh_from_db()
    # HARDEN-01 C2: the failure is recorded instead of waiting for lease expiry.
    assert delivery.state == State.RETRY_PENDING
    assert delivery.lease_token is None
    assert delivery.last_error_code == "UNEXPECTED_ERROR"
    assert SENSITIVE_MARKER not in (delivery.last_error_safe or "")
    assert not _events(delivery, "PROCESSING_COMPLETED").exists()
    assert Measurement.objects.count() == 0
    text = _rendered(iot_log_records)
    assert "falló procesamiento durable" in text
    assert "RuntimeError" in text
    assert SENSITIVE_MARKER not in text
    assert payload.decode("ascii") not in text
    frame_b64 = base64.b64encode(frame_for(world, seq=120)).decode("ascii")
    assert frame_b64 not in text
    assert world.key.hex() not in text


def test_worker_pauses_globally_on_crypto_error_without_leaking(world, iot_log_records):
    delivery, _payload = _registered_world_delivery(world)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(payload_tag=bytes(16))

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert batch.processed == 0
    assert batch.deferred == 1
    pause = IotMqttDeliveryPause.objects.get(is_active=True)
    assert pause.active_scope_key == GLOBAL_SCOPE_KEY
    assert pause.reason_code == "MQTT_PAYLOAD_CRYPTO_ERROR"
    assert pause.started_process_id == "pytest-worker"
    assert Measurement.objects.count() == 0
    text = _rendered(iot_log_records)
    assert "pausada por error criptográfico" in text
    assert world.key.hex() not in text


def test_worker_rejects_invalid_arguments():
    with pytest.raises(ValueError):
        run_mqtt_delivery_worker_once(worker_id="")
    with pytest.raises(ValueError):
        run_mqtt_delivery_worker_once(worker_id="w", batch_size=0)
    with pytest.raises(ValueError):
        run_mqtt_delivery_worker_once(worker_id="w", batch_size=101)


def test_worker_batch_survives_delivery_without_candidate(lanes):
    orphan = register_lane("orphan", coordinator_id=None, received_offset_s=0)
    known = register_lane("known", coordinator_id=lanes[0].pk, received_offset_s=10)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert (batch.discovered, batch.claimed, batch.processed, batch.deferred) == (2, 2, 2, 0)
    orphan.refresh_from_db()
    known.refresh_from_db()
    assert orphan.state == State.REJECTED
    assert known.state == State.REJECTED
    assert Measurement.objects.count() == 0


def test_claim_exception_is_isolated_and_not_counted_as_processed(
    lanes, monkeypatch, iot_log_records
):
    import apps.iot.services.mqtt_delivery_worker as worker_module

    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    failing = register_lane("fails", coordinator_id=lane_a, received_offset_s=0)
    healthy = register_lane("ok", coordinator_id=lane_b, received_offset_s=10)
    real_claim = worker_module.claim_mqtt_delivery

    def claim(*, delivery_id, worker_id, **_kwargs):
        if delivery_id == failing.pk:
            raise FieldError(f"fallo simulado {SENSITIVE_MARKER}")
        return real_claim(delivery_id=delivery_id, worker_id=worker_id)

    monkeypatch.setattr(worker_module, "claim_mqtt_delivery", claim)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert (batch.discovered, batch.claimed, batch.processed, batch.deferred) == (2, 1, 1, 1)
    failing.refresh_from_db()
    healthy.refresh_from_db()
    assert failing.state == State.RECEIVED
    assert failing.attempt_count == 0
    assert not _events(failing, "PROCESSING_COMPLETED").exists()
    assert healthy.state == State.REJECTED
    text = _rendered(iot_log_records)
    assert "falló la reclamación durable" in text
    assert "FieldError" in text
    assert SENSITIVE_MARKER not in text


def test_repeated_claim_errors_stop_the_batch_in_order(
    lanes, admin, farm_id, monkeypatch, iot_log_records
):
    import apps.iot.services.mqtt_delivery_worker as worker_module

    # One lane per delivery: since BUILD-01D-A2 a failing lane is skipped for
    # the rest of the batch, so distinct lanes keep exercising the threshold.
    coordinators = list(lanes) + [
        iot.create_coordinator(user=admin, farm_id=farm_id, name=f"Extra {index}")
        for index in range(3)
    ]
    for index, coordinator in enumerate(coordinators):
        register_lane(f"d{index}", coordinator_id=coordinator.pk, received_offset_s=index)
    attempts: list = []

    def broken_claim(*, delivery_id, worker_id, **_kwargs):
        attempts.append(delivery_id)
        raise OperationalError("base no disponible")

    monkeypatch.setattr(worker_module, "claim_mqtt_delivery", broken_claim)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert len(attempts) == worker_module.MAX_CONSECUTIVE_CLAIM_ERRORS
    assert len(set(attempts)) == len(attempts)
    assert batch.discovered == 5
    assert batch.claimed == 0
    assert batch.processed == 0
    assert batch.deferred == worker_module.MAX_CONSECUTIVE_CLAIM_ERRORS
    assert IotMqttDelivery.objects.filter(state=State.RECEIVED).count() == 5
    assert "lote MQTT detenido" in _rendered(iot_log_records)


def test_paused_lane_does_not_starve_other_lanes(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    for index in range(3):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=index)
    b_delivery = register_lane("b", coordinator_id=lane_b, received_offset_s=100)
    pause_mqtt_deliveries(reason_code="OPS_TEST", coordinator_id=lane_a, process_id="p")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=2)

    b_delivery.refresh_from_db()
    assert batch.processed == 1
    assert b_delivery.state == State.REJECTED
    # Paused rows are neither claimed nor counted as processed.
    paused_rows = IotMqttDelivery.objects.filter(coordinator_id=lane_a)
    assert paused_rows.count() == 3
    assert all(row.state == State.RECEIVED and row.attempt_count == 0 for row in paused_rows)


# --------------------------------------------------------------------------
# BUILD-01D-A2: equidad del worker entre carriles
# --------------------------------------------------------------------------


def _assert_lane_fifo(deliveries) -> None:
    """Each follower is claimed only after its predecessor completed."""
    for previous, following in zip(deliveries, deliveries[1:], strict=False):
        done = _events(previous, "PROCESSING_COMPLETED").get()
        claims = _events(following, "LEASE_CLAIMED")
        assert claims.exists()
        assert all(claim.occurred_at >= done.occurred_at for claim in claims)


def test_paused_lane_does_not_affect_unknown_coordinator_lane(lanes):
    lane_a = lanes[0].pk
    for index in range(4):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=index)
    orphan = register_lane("orphan", coordinator_id=None, received_offset_s=100)
    pause_mqtt_deliveries(reason_code="OPS_TEST", coordinator_id=lane_a, process_id="p")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=2)

    orphan.refresh_from_db()
    assert batch.processed == 1
    assert orphan.state == State.REJECTED
    paused_rows = IotMqttDelivery.objects.filter(coordinator_id=lane_a, state=State.RECEIVED)
    assert paused_rows.count() == 4


def test_pause_of_one_lane_does_not_affect_the_others(lanes, admin, farm_id):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    lane_c = iot.create_coordinator(user=admin, farm_id=farm_id, name="Carril C").pk
    for index in range(3):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=index)
    others = [
        register_lane(f"b{i}", coordinator_id=lane_b, received_offset_s=10 + i) for i in range(2)
    ] + [register_lane(f"c{i}", coordinator_id=lane_c, received_offset_s=20 + i) for i in range(2)]
    pause_mqtt_deliveries(reason_code="OPS_TEST", coordinator_id=lane_a, process_id="p")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=10)

    assert batch.processed == 4
    for row in others:
        row.refresh_from_db()
        assert row.state == State.REJECTED
    assert (
        IotMqttDelivery.objects.filter(coordinator_id=lane_a, state=State.RECEIVED).count() == 3
    )


def test_global_pause_blocks_every_lane_in_worker(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    register_lane("a", coordinator_id=lane_a, received_offset_s=0)
    register_lane("b", coordinator_id=lane_b, received_offset_s=1)
    register_lane("n", coordinator_id=None, received_offset_s=2)
    pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="p")

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")

    assert (batch.claimed, batch.processed) == (0, 0)
    assert IotMqttDelivery.objects.filter(state=State.RECEIVED).count() == 3
    assert not IotMqttDeliveryEvent.objects.filter(event_type="LEASE_CLAIMED").exists()


def test_worker_preserves_fifo_within_each_lane(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    a_rows = [
        register_lane(f"a{i}", coordinator_id=lane_a, received_offset_s=i * 2) for i in range(3)
    ]
    b_rows = [
        register_lane(f"b{i}", coordinator_id=lane_b, received_offset_s=i * 2 + 1)
        for i in range(3)
    ]
    n_rows = [
        register_lane(f"n{i}", coordinator_id=None, received_offset_s=10 + i) for i in range(3)
    ]

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=20)

    assert batch.processed == 9
    for rows in (a_rows, b_rows, n_rows):
        _assert_lane_fifo(rows)


def test_blocked_lane_heads_do_not_fill_the_batch(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    head = register_lane("a-head", coordinator_id=lane_a, received_offset_s=0)
    held = claim_mqtt_delivery(delivery_id=head.pk, worker_id="other-worker")
    for index in range(5):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=1 + index)
    b_delivery = register_lane("b", coordinator_id=lane_b, received_offset_s=100)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=2)

    b_delivery.refresh_from_db()
    assert b_delivery.state == State.REJECTED
    assert batch.processed == 1
    head.refresh_from_db()
    assert head.lease_owner == "other-worker"
    assert head.lease_token == held.lease_token
    followers = IotMqttDelivery.objects.filter(coordinator_id=lane_a).exclude(pk=head.pk)
    assert all(row.state == State.RECEIVED and row.attempt_count == 0 for row in followers)


def test_dead_letter_head_only_blocks_its_lane_in_worker(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    head = register_lane("dlq", coordinator_id=lane_a, received_offset_s=0)
    IotMqttDelivery.objects.filter(pk=head.pk).update(state=State.DEAD_LETTER)
    for index in range(3):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=1 + index)
    b_delivery = register_lane("b", coordinator_id=lane_b, received_offset_s=100)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=2)

    b_delivery.refresh_from_db()
    assert b_delivery.state == State.REJECTED
    assert batch.processed == 1
    assert (
        IotMqttDelivery.objects.filter(coordinator_id=lane_a, state=State.RECEIVED).count() == 3
    )
    head.refresh_from_db()
    assert head.state == State.DEAD_LETTER


def test_many_paused_lanes_do_not_starve_an_active_lane(lanes, admin, farm_id):
    paused = [
        iot.create_coordinator(user=admin, farm_id=farm_id, name=f"Pausado {index}").pk
        for index in range(4)
    ]
    for offset, coordinator_id in enumerate(paused):
        register_lane(f"p{offset}", coordinator_id=coordinator_id, received_offset_s=offset)
        pause_mqtt_deliveries(
            reason_code="OPS_TEST", coordinator_id=coordinator_id, process_id="p"
        )
    active = register_lane("active", coordinator_id=lanes[0].pk, received_offset_s=100)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=2)

    active.refresh_from_db()
    assert active.state == State.REJECTED
    assert batch.processed == 1
    assert batch.claimed == 1
    assert IotMqttDelivery.objects.filter(state=State.RECEIVED).count() == 4


def test_batch_claims_are_bounded_by_batch_size(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    for index in range(4):
        register_lane(f"a{index}", coordinator_id=lane_a, received_offset_s=index * 2)
        register_lane(f"b{index}", coordinator_id=lane_b, received_offset_s=index * 2 + 1)

    batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker", batch_size=3)

    assert batch.claimed == 3
    assert batch.processed == 3
    assert IotMqttDelivery.objects.filter(state=State.REJECTED).count() == 3
    assert IotMqttDelivery.objects.filter(state=State.RECEIVED).count() == 5
