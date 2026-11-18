# ruff: noqa: S101, S105, S106, F811
"""HARDEN-01 C2/S1: ciclo de vida de entregas fallidas contra MariaDB real.

Solo mediante el orquestador desechable IoT
(``config.settings_test_disposable_iot``); ``require_mariadb`` rechaza otros
motores. ``transaction=True`` porque claim, procesamiento y registro de fallos
confirman transacciones propias.
"""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connections
from django.utils import timezone

import apps.iot.services.mqtt_delivery_processing as processing_module
from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
    Measurement,
    ReceivedFrame,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_failures import (
    MAX_ATTEMPTS_EXCEEDED,
    FailureClassification,
    FailureKind,
    RetryPolicy,
    record_mqtt_delivery_failure,
)
from apps.iot.services.mqtt_delivery_leases import claim_mqtt_delivery
from apps.iot.services.mqtt_delivery_ops import (
    DeadLetterResolutionError,
    ResolutionAction,
    delivery_summary,
    resolve_dead_letter,
)
from apps.iot.services.mqtt_delivery_pauses import GLOBAL_SCOPE_KEY, resume_mqtt_deliveries
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    lanes,
    mqtt_key_settings,
    raw_payload,
    register,
    register_lane,
    world,
)

pytestmark = pytest.mark.django_db(transaction=True)

FAST = RetryPolicy(max_attempts=3, base_delay_seconds=5, max_delay_seconds=60)
TRANSIENT = FailureClassification(FailureKind.TRANSIENT, "DATABASE_ERROR", "OperationalError")


def _deliver(world, *, seq=120, payload=None):
    payload = payload or raw_payload(frame_for(world, seq=seq))
    result = register(payload, topic=world.topic, coordinator_id=world.coordinator.pk)
    assert result.status is MqttReceiptStatus.PERSISTED
    return result.delivery


def _events(delivery, event_type):
    return IotMqttDeliveryEvent.objects.filter(delivery=delivery, event_type=event_type)


def _make_due(delivery):
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )


def _failing_ingest(exc_factory=lambda: OperationalError("db caída")):
    def ingest(**_kwargs):
        raise exc_factory()

    return ingest


def _run(policy=FAST, worker_id="pytest-worker"):
    return run_mqtt_delivery_worker_once(worker_id=worker_id, policy=policy)


def _parallel(operations):
    barrier = Barrier(len(operations))

    def runner(operation):
        try:
            barrier.wait(timeout=5)
            return operation()
        except Exception as exc:  # noqa: BLE001 - reported to the assertion
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return [f.result(timeout=30) for f in [pool.submit(runner, op) for op in operations]]


# --------------------------------------------------------------------------
# A/B. Recepción y procesamiento correctos
# --------------------------------------------------------------------------


def test_a_received_delivery_is_durable_and_pending(world):
    delivery = _deliver(world)

    delivery.refresh_from_db()
    assert delivery.state == State.RECEIVED
    assert delivery.attempt_count == 0
    assert delivery.lease_token is None


def test_b_processed_delivery_is_final_with_cleared_lease(world):
    delivery = _deliver(world)

    batch = _run()

    delivery.refresh_from_db()
    assert batch.processed == 1
    assert delivery.state == State.ACCEPTED
    assert delivery.attempt_count == 1
    assert (delivery.lease_owner, delivery.lease_token, delivery.lease_expires_at) == (
        None,
        None,
        None,
    )
    assert Measurement.objects.count() == 2


# --------------------------------------------------------------------------
# C/D. Error transitorio, RETRY_PENDING y backoff
# --------------------------------------------------------------------------


def test_c_transient_error_schedules_retry_and_recovers(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())

    before = timezone.now()
    batch = _run()

    delivery.refresh_from_db()
    assert batch.processed == 0
    assert delivery.state == State.RETRY_PENDING
    assert delivery.lease_token is None
    assert delivery.last_error_code == "DATABASE_ERROR"
    assert delivery.next_attempt_at >= before + timedelta(seconds=5)
    assert _events(delivery, "RETRY_SCHEDULED").count() == 1
    assert Measurement.objects.count() == 0

    # Backoff is honored: not claimed again before next_attempt_at.
    assert _run().claimed == 0

    monkeypatch.undo()
    _make_due(delivery)
    assert _run().processed == 1
    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert delivery.attempt_count == 2
    assert Measurement.objects.count() == 2


def test_d_backoff_grows_exponentially_between_attempts(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=5, max_delay_seconds=60)

    observed = []
    for _ in range(3):
        _make_due(delivery)
        before = timezone.now()
        _run(policy)
        after = timezone.now()
        delivery.refresh_from_db()
        observed.append((delivery.next_attempt_at - before, after - before))

    for (delta, spread), expected in zip(observed, (5, 10, 20), strict=True):
        assert timedelta(seconds=expected) <= delta <= timedelta(seconds=expected) + spread


# --------------------------------------------------------------------------
# E/F. Máximo de intentos y error permanente
# --------------------------------------------------------------------------


def test_e_attempt_limit_moves_delivery_to_dead_letter(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())

    for _ in range(FAST.max_attempts):
        _make_due(delivery)
        _run()

    delivery.refresh_from_db()
    assert delivery.state == State.DEAD_LETTER
    assert delivery.attempt_count == FAST.max_attempts
    assert delivery.reason_code == MAX_ATTEMPTS_EXCEEDED
    assert delivery.last_error_code == "DATABASE_ERROR"
    assert _events(delivery, "RETRY_SCHEDULED").count() == FAST.max_attempts - 1
    assert _events(delivery, "DEAD_LETTERED").count() == 1

    _make_due(delivery)
    assert _run().claimed == 0  # never retried again automatically


def test_e_crashed_attempts_are_bounded_at_claim_time(world):
    """A worker that dies before recording the failure still burns the budget."""
    delivery = _deliver(world)

    for attempt in range(FAST.max_attempts):
        claim = claim_mqtt_delivery(
            delivery_id=delivery.pk, worker_id=f"crash-{attempt}", max_attempts=FAST.max_attempts
        )
        assert claim is not None
        IotMqttDelivery.objects.filter(pk=delivery.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

    assert (
        claim_mqtt_delivery(
            delivery_id=delivery.pk, worker_id="crash-x", max_attempts=FAST.max_attempts
        )
        is None
    )
    delivery.refresh_from_db()
    assert delivery.state == State.DEAD_LETTER
    assert delivery.attempt_count == FAST.max_attempts
    assert delivery.reason_code == MAX_ATTEMPTS_EXCEEDED


def test_f_permanent_error_dead_letters_without_retry(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", lambda **_kwargs: object())

    _run()

    delivery.refresh_from_db()
    assert delivery.state == State.DEAD_LETTER
    assert delivery.attempt_count == 1
    assert delivery.reason_code == "INGESTION_CONTRACT_ERROR"
    assert not _events(delivery, "RETRY_SCHEDULED").exists()


# --------------------------------------------------------------------------
# G/H. FIFO con entrega fallida y resolución explícita
# --------------------------------------------------------------------------


def test_g_failed_head_blocks_its_lane_visibly_but_not_other_lanes(world, lanes, monkeypatch):
    head = _deliver(world, seq=120)
    follower = _deliver(world, seq=121)
    other = register_lane("otro-carril", coordinator_id=lanes[0].pk, received_offset_s=0)
    monkeypatch.setattr(processing_module, "ingest", lambda **_kwargs: object())

    _run()

    head.refresh_from_db()
    follower.refresh_from_db()
    other.refresh_from_db()
    assert head.state == State.DEAD_LETTER
    assert follower.state == State.RECEIVED  # never skipped silently
    assert other.state == State.REJECTED  # independent lane keeps flowing
    summary = delivery_summary(head.pk)
    assert summary.blocked_successors == 1
    assert summary.reason_code == "INGESTION_CONTRACT_ERROR"


def test_h_discard_resolves_dead_letter_and_unblocks_lane(world, monkeypatch, admin):
    head = _deliver(world, seq=120)
    follower = _deliver(world, seq=121)
    monkeypatch.setattr(processing_module, "ingest", lambda **_kwargs: object())
    _run()
    monkeypatch.undo()

    resolved = resolve_dead_letter(
        delivery_id=head.pk,
        action=ResolutionAction.DISCARD,
        reason_code="OPS_VERIFIED_CORRUPT",
        operator="tester",
        actor=admin,
    )

    assert resolved.state == State.RESOLVED
    assert resolved.resolution_code == "OPS_VERIFIED_CORRUPT"
    assert resolved.resolved_by_id == admin.pk
    event = _events(head, "DEAD_LETTER_RESOLVED").get()
    assert event.worker_id == "ops:tester" and event.actor_id == admin.pk
    assert _run().processed == 1
    follower.refresh_from_db()
    assert follower.state == State.ACCEPTED
    assert IotMqttDelivery.objects.filter(pk=head.pk).exists()  # history kept


def test_h_requeue_gives_a_fresh_budget_and_keeps_history(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())
    for _ in range(FAST.max_attempts):
        _make_due(delivery)
        _run()
    monkeypatch.undo()

    requeued = resolve_dead_letter(
        delivery_id=delivery.pk,
        action=ResolutionAction.REQUEUE,
        reason_code="DB_RESTORED",
        operator="tester",
    )

    assert requeued.state == State.RETRY_PENDING
    assert requeued.attempt_count == 0
    event = _events(delivery, "DEAD_LETTER_REQUEUED").get()
    assert event.attempt_number == FAST.max_attempts
    assert _run().processed == 1
    delivery.refresh_from_db()
    assert delivery.state == State.ACCEPTED
    assert _events(delivery, "DEAD_LETTERED").count() == 1


def test_h_only_dead_letters_can_be_resolved(world):
    delivery = _deliver(world)

    with pytest.raises(DeadLetterResolutionError):
        resolve_dead_letter(
            delivery_id=delivery.pk,
            action=ResolutionAction.DISCARD,
            reason_code="X",
            operator="tester",
        )
    with pytest.raises(ValueError):
        resolve_dead_letter(
            delivery_id=delivery.pk,
            action=ResolutionAction.DISCARD,
            reason_code="motivo con espacios",
            operator="tester",
        )
    delivery.refresh_from_db()
    assert delivery.state == State.RECEIVED


# --------------------------------------------------------------------------
# I/J. Concurrencia, lease vencido y fencing
# --------------------------------------------------------------------------


def test_i_two_workers_record_a_single_failure(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())

    batches = _parallel([lambda: _run(worker_id="worker-1"), lambda: _run(worker_id="worker-2")])

    assert not [b for b in batches if isinstance(b, Exception)], batches
    assert sum(b.claimed for b in batches) == 1
    delivery.refresh_from_db()
    assert delivery.state == State.RETRY_PENDING
    assert delivery.attempt_count == 1
    assert _events(delivery, "RETRY_SCHEDULED").count() == 1
    assert Measurement.objects.count() == 0


def test_i_concurrent_resolutions_apply_once(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", lambda **_kwargs: object())
    _run()

    def resolve(operator):
        return lambda: resolve_dead_letter(
            delivery_id=delivery.pk,
            action=ResolutionAction.DISCARD,
            reason_code="OPS",
            operator=operator,
        )

    results = _parallel([resolve("a"), resolve("b")])

    assert sum(isinstance(r, IotMqttDelivery) for r in results) == 1
    assert sum(isinstance(r, DeadLetterResolutionError) for r in results) == 1
    assert _events(delivery, "DEAD_LETTER_RESOLVED").count() == 1


def test_j_stale_worker_cannot_record_failure_after_reclaim(world):
    delivery = _deliver(world)
    first = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="worker-a")
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    second = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="worker-b")
    assert second.lease_token != first.lease_token

    stale = record_mqtt_delivery_failure(
        delivery_id=delivery.pk,
        worker_id="worker-a",
        lease_token=first.lease_token,
        failure=TRANSIENT,
        policy=FAST,
    )

    assert stale is None
    delivery.refresh_from_db()
    assert delivery.state == State.PROCESSING
    assert delivery.lease_owner == "worker-b"

    current = record_mqtt_delivery_failure(
        delivery_id=delivery.pk,
        worker_id="worker-b",
        lease_token=second.lease_token,
        failure=TRANSIENT,
        policy=FAST,
    )
    assert current.state == State.RETRY_PENDING


def test_j_expired_but_unclaimed_lease_can_still_record_its_failure(world):
    delivery = _deliver(world)
    claim = claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="worker-a")
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    row = record_mqtt_delivery_failure(
        delivery_id=delivery.pk,
        worker_id="worker-a",
        lease_token=claim.lease_token,
        failure=TRANSIENT,
        policy=FAST,
    )

    assert row.state == State.RETRY_PENDING
    assert row.attempt_count == 1


# --------------------------------------------------------------------------
# K. Errores criptográficos y pausas
# --------------------------------------------------------------------------


def test_k_missing_key_version_pauses_and_retries_without_dead_letter(world):
    delivery = _deliver(world)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(payload_key_version="2")

    _run()

    delivery.refresh_from_db()
    assert delivery.state == State.RETRY_PENDING
    assert delivery.last_error_code == "MQTT_PAYLOAD_KEY_UNAVAILABLE"
    pause = IotMqttDeliveryPause.objects.get(is_active=True)
    assert pause.active_scope_key == GLOBAL_SCOPE_KEY
    _make_due(delivery)
    assert _run().claimed == 0  # globally paused


def test_k_unauthentic_envelope_is_dead_lettered_and_pauses(world):
    delivery = _deliver(world)
    IotMqttDelivery.objects.filter(pk=delivery.pk).update(payload_tag=bytes(16))

    _run()

    delivery.refresh_from_db()
    assert delivery.state == State.DEAD_LETTER
    assert delivery.reason_code == "MQTT_PAYLOAD_AUTH_FAILED"
    assert IotMqttDeliveryPause.objects.filter(is_active=True).count() == 1
    assert Measurement.objects.count() == 0
    resume_mqtt_deliveries(reason_code="OPS_KEY_OK", process_id="pytest")
    assert _run().claimed == 0  # dead letter stays put until resolved


# --------------------------------------------------------------------------
# O. Fechas futuras y antiguas en la ingesta real (S1)
# --------------------------------------------------------------------------


def _payload_with_rx(world, *, seq, rx: datetime) -> bytes:
    document = json.loads(raw_payload(frame_for(world, seq=seq)))
    document["lora_rx_at"] = rx.isoformat().replace("+00:00", "Z")
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def test_o_future_gateway_time_does_not_become_measured_at(world):
    future = datetime(2099, 1, 1, tzinfo=UTC)
    delivery = _deliver(world, payload=_payload_with_rx(world, seq=130, rx=future))

    _run()

    delivery.refresh_from_db()
    frame = ReceivedFrame.objects.get()
    assert delivery.state == State.ACCEPTED
    assert frame.gateway_received_at == future  # original kept for traceability
    assert frame.measured_at == delivery.received_at
    assert frame.server_received_at == delivery.received_at
    assert frame.measured_at_approximate is True
    assert set(Measurement.objects.values_list("measured_at", flat=True)) == {
        delivery.received_at
    }


def test_o_recent_buffered_gateway_time_is_kept(world):
    recent = timezone.now().replace(microsecond=0) - timedelta(hours=6)
    delivery = _deliver(world, payload=_payload_with_rx(world, seq=131, rx=recent))

    _run()

    frame = ReceivedFrame.objects.get()
    delivery.refresh_from_db()
    assert frame.measured_at == recent
    assert frame.measured_at_approximate is False


def test_o_too_old_gateway_time_falls_back_to_server_time(world):
    ancient = datetime(2001, 1, 1, tzinfo=UTC)
    delivery = _deliver(world, payload=_payload_with_rx(world, seq=132, rx=ancient))

    _run()

    delivery.refresh_from_db()
    frame = ReceivedFrame.objects.get()
    assert frame.gateway_received_at == ancient
    assert frame.measured_at == delivery.received_at
    assert frame.measured_at_approximate is True


# --------------------------------------------------------------------------
# Comandos de operación
# --------------------------------------------------------------------------


def _call(*args) -> dict:
    out = io.StringIO()
    call_command("mqtt_deliveries", *args, stdout=out)
    return json.loads(out.getvalue())


def _snapshot():
    return (
        list(IotMqttDelivery.objects.order_by("id").values_list("id", "state", "updated_at")),
        IotMqttDeliveryEvent.objects.count(),
        IotMqttDeliveryPause.objects.count(),
    )


def test_inspection_commands_are_read_only(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", _failing_ingest())
    _run()
    before = _snapshot()

    status = _call("status", str(delivery.pk), "--events")
    retry = _call("list", "--state", "retry_pending")
    dead = _call("list", "--state", "dead_letter")
    pauses = _call("pauses", "--all")

    assert _snapshot() == before
    assert status["state"] == State.RETRY_PENDING
    assert {e["event_type"] for e in status["events"]} >= {"LEASE_CLAIMED", "RETRY_SCHEDULED"}
    assert retry["count"] == 1 and dead["count"] == 0 and pauses["count"] == 0
    rendered = json.dumps(status)
    assert "payload_ciphertext" not in rendered and world.key.hex() not in rendered


def test_resolve_command_requires_matching_confirmation(world, monkeypatch):
    delivery = _deliver(world)
    monkeypatch.setattr(processing_module, "ingest", lambda **_kwargs: object())
    _run()

    with pytest.raises(CommandError, match="--confirm"):
        _call(
            "resolve", str(delivery.pk), "--action", "discard", "--reason", "OPS",
            "--operator", "tester", "--confirm", "otra-cosa",
        )
    delivery.refresh_from_db()
    assert delivery.state == State.DEAD_LETTER

    result = _call(
        "resolve", str(delivery.pk), "--action", "discard", "--reason", "OPS",
        "--operator", "tester", "--confirm", str(delivery.pk),
    )
    assert result["state"] == State.RESOLVED


def test_pause_and_resume_commands_are_audited(world):
    paused = _call("pause", "--coordinator-id", str(world.coordinator.pk), "--reason", "OPS_MANT",
                   "--operator", "tester")
    listed = _call("pauses")
    resumed = _call("resume", "--coordinator-id", str(world.coordinator.pk), "--reason", "OPS_FIN",
                    "--operator", "tester")

    assert paused["is_active"] is True
    assert listed["pauses"][0]["started_process_id"] == "ops:tester"
    assert resumed["resumed"] is True
    with pytest.raises(CommandError):
        _call("pause", "--reason", "X", "--operator", "tester")  # scope is mandatory
