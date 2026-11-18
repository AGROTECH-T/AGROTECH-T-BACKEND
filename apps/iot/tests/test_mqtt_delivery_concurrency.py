# ruff: noqa: S101, F811
"""Concurrencia real de la bandeja MQTT durable (BUILD-01D-A).

Cada hilo usa su propia conexión MariaDB y confirma transacciones reales
(``transaction=True``), siguiendo el patrón de ``test_concurrency.py``.
Solo mediante el orquestador desechable IoT.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections

from apps.iot.models import (
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
    IotMqttDeliveryPauseEvent,
    Measurement,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.services.mqtt_delivery_leases import claim_mqtt_delivery
from apps.iot.services.mqtt_delivery_pauses import pause_mqtt_deliveries
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


def _run_in_parallel(operations):
    barrier = Barrier(len(operations))

    def runner(operation):
        try:
            barrier.wait(timeout=5)
            return operation()
        except Exception as exc:  # noqa: BLE001 - reported to the assertion
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations), thread_name_prefix="mqtt") as pool:
        futures = [pool.submit(runner, operation) for operation in operations]
        return [future.result(timeout=30) for future in futures]


def test_parallel_receipt_of_same_message_creates_one_delivery(mqtt_key_settings):
    payload = b'{"probe":"race"}'

    results = _run_in_parallel([lambda: register(payload), lambda: register(payload)])

    assert not [r for r in results if isinstance(r, Exception)], results
    assert sorted(r.status.value for r in results) == sorted(
        [MqttReceiptStatus.PERSISTED.value, MqttReceiptStatus.REDELIVERY.value]
    )
    delivery = IotMqttDelivery.objects.get()
    assert {r.delivery.pk for r in results} == {delivery.pk}
    assert IotMqttDeliveryEvent.objects.filter(event_type="RECEIVED").count() == 1
    assert IotMqttDeliveryEvent.objects.filter(event_type="REDELIVERY").count() == 1


def test_parallel_claims_grant_a_single_lease(lanes):
    delivery = register_lane("claim-race", coordinator_id=lanes[0].pk, received_offset_s=0)

    results = _run_in_parallel(
        [
            lambda: claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1"),
            lambda: claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2"),
        ]
    )

    assert not [r for r in results if isinstance(r, Exception)], results
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    delivery.refresh_from_db()
    assert delivery.attempt_count == 1
    assert delivery.lease_token == winners[0].lease_token
    assert IotMqttDeliveryEvent.objects.filter(event_type="LEASE_CLAIMED").count() == 1


def test_parallel_claims_in_same_lane_never_skip_the_head(lanes):
    lane_a = lanes[0].pk
    head = register_lane("head", coordinator_id=lane_a, received_offset_s=0)
    follower = register_lane("follower", coordinator_id=lane_a, received_offset_s=10)

    results = _run_in_parallel(
        [
            lambda: claim_mqtt_delivery(delivery_id=follower.pk, worker_id="w1"),
            lambda: claim_mqtt_delivery(delivery_id=head.pk, worker_id="w2"),
        ]
    )

    assert not [r for r in results if isinstance(r, Exception)], results
    assert results[0] is None
    assert results[1] is not None
    follower.refresh_from_db()
    assert follower.state == State.RECEIVED


def test_parallel_workers_process_a_delivery_once(world):
    payload = raw_payload(frame_for(world, seq=140))
    result = register(payload, topic=world.topic, coordinator_id=world.coordinator.pk)

    batches = _run_in_parallel(
        [
            lambda: run_mqtt_delivery_worker_once(worker_id="worker-1"),
            lambda: run_mqtt_delivery_worker_once(worker_id="worker-2"),
        ]
    )

    assert not [b for b in batches if isinstance(b, Exception)], batches
    assert sum(b.processed for b in batches) == 1
    delivery = IotMqttDelivery.objects.get(pk=result.delivery.pk)
    assert delivery.state == State.ACCEPTED
    assert Measurement.objects.count() == 2
    assert (
        IotMqttDeliveryEvent.objects.filter(
            delivery=delivery, event_type="PROCESSING_COMPLETED"
        ).count()
        == 1
    )


def test_parallel_pause_of_same_scope_keeps_one_active_pause(mqtt_key_settings):
    results = _run_in_parallel(
        [
            lambda: pause_mqtt_deliveries(reason_code="OPS_A", process_id="p1"),
            lambda: pause_mqtt_deliveries(reason_code="OPS_B", process_id="p2"),
        ]
    )

    assert not [r for r in results if isinstance(r, Exception)], results
    assert len({r.pk for r in results}) == 1
    assert IotMqttDeliveryPause.objects.filter(is_active=True).count() == 1
    assert IotMqttDeliveryPauseEvent.objects.filter(event_type="PAUSED").count() == 1


def test_parallel_claims_without_candidate_grant_a_single_lease(lanes):
    delivery = register_lane("orphan-race", coordinator_id=None, received_offset_s=0)

    results = _run_in_parallel(
        [
            lambda: claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w1"),
            lambda: claim_mqtt_delivery(delivery_id=delivery.pk, worker_id="w2"),
        ]
    )

    assert not [r for r in results if isinstance(r, Exception)], results
    assert len([r for r in results if r is not None]) == 1
    delivery.refresh_from_db()
    assert delivery.attempt_count == 1
    assert IotMqttDeliveryEvent.objects.filter(event_type="LEASE_CLAIMED").count() == 1


def test_parallel_workers_process_unknown_lane_once_and_in_order(lanes):
    head = register_lane("orphan-head", coordinator_id=None, received_offset_s=0)
    follower = register_lane("orphan-next", coordinator_id=None, received_offset_s=10)

    batches = _run_in_parallel(
        [
            lambda: run_mqtt_delivery_worker_once(worker_id="worker-1"),
            lambda: run_mqtt_delivery_worker_once(worker_id="worker-2"),
        ]
    )

    assert not [b for b in batches if isinstance(b, Exception)], batches
    assert sum(b.processed for b in batches) == 2
    for delivery in (head, follower):
        delivery.refresh_from_db()
        assert delivery.state == State.REJECTED
        assert (
            IotMqttDeliveryEvent.objects.filter(
                delivery=delivery, event_type="PROCESSING_COMPLETED"
            ).count()
            == 1
        )
    head_done = IotMqttDeliveryEvent.objects.get(
        delivery=head, event_type="PROCESSING_COMPLETED"
    )
    follower_claim = IotMqttDeliveryEvent.objects.get(
        delivery=follower, event_type="LEASE_CLAIMED"
    )
    assert follower_claim.occurred_at >= head_done.occurred_at


def test_parallel_workers_keep_exclusivity_and_fifo_across_lanes(lanes):
    lane_a, lane_b = (coordinator.pk for coordinator in lanes)
    groups = [
        [register_lane(f"a{i}", coordinator_id=lane_a, received_offset_s=i * 3) for i in range(3)],
        [
            register_lane(f"b{i}", coordinator_id=lane_b, received_offset_s=i * 3 + 1)
            for i in range(3)
        ],
        [
            register_lane(f"n{i}", coordinator_id=None, received_offset_s=i * 3 + 2)
            for i in range(3)
        ],
    ]

    total_processed = 0
    for _round in range(10):
        batches = _run_in_parallel(
            [
                lambda: run_mqtt_delivery_worker_once(worker_id="worker-1", batch_size=2),
                lambda: run_mqtt_delivery_worker_once(worker_id="worker-2", batch_size=2),
            ]
        )
        assert not [b for b in batches if isinstance(b, Exception)], batches
        total_processed += sum(b.processed for b in batches)
        if not IotMqttDelivery.objects.exclude(state=State.REJECTED).exists():
            break

    assert total_processed == 9
    assert IotMqttDelivery.objects.filter(state=State.REJECTED).count() == 9
    for rows in groups:
        for row in rows:
            completed = IotMqttDeliveryEvent.objects.filter(
                delivery=row, event_type="PROCESSING_COMPLETED"
            )
            assert completed.count() == 1
        for previous, following in zip(rows, rows[1:], strict=False):
            done = IotMqttDeliveryEvent.objects.get(
                delivery=previous, event_type="PROCESSING_COMPLETED"
            )
            claims = IotMqttDeliveryEvent.objects.filter(
                delivery=following, event_type="LEASE_CLAIMED"
            )
            assert claims.exists()
            assert all(claim.occurred_at >= done.occurred_at for claim in claims)
