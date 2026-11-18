# ruff: noqa: S101
"""Protección adicional del consumidor MQTT sin red ni base (BUILD-01D-A).

Complementa ``test_mqtt_consumer.py`` sin duplicarlo: orden persistencia→ACK,
desconexión ante fallos de recepción o de ACK, y registros sin payload. Fija la
regla de ACK vigente (PERSISTED/REDELIVERY); no la modifica.
"""

from __future__ import annotations

import logging

import pytest

from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.tests.test_mqtt_consumer import (  # noqa: F401 (fixture)
    TOPIC,
    FakeClient,
    FakeMessage,
    advisory_candidate_without_database,
    make_consumer,
    raw_payload,
    receipt,
)

SENSITIVE_MARKER = "SENSITIVE-MARKER-7f3a"


@pytest.fixture
def consumer_log_records():
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("apps.iot.mqtt.consumer")
    handler = _Collector(level=logging.DEBUG)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_ack_happens_only_after_receipt_returns():
    order: list[str] = []

    def receipt_func(**_kwargs):
        order.append("persisted")
        return receipt(MqttReceiptStatus.PERSISTED)

    consumer = make_consumer(receipt_func)
    consumer.handle_message(TOPIC, raw_payload(), ack=lambda: order.append("ack"))

    assert order == ["persisted", "ack"]


def test_receipt_receives_exact_payload_and_qos_one():
    calls: list[dict] = []
    payload = raw_payload()

    def receipt_func(**kwargs):
        calls.append(kwargs)
        return receipt()

    make_consumer(receipt_func).handle_message(TOPIC, bytearray(payload), ack=lambda: None)

    assert len(calls) == 1
    assert calls[0]["topic"] == TOPIC
    assert calls[0]["payload"] == payload
    assert type(calls[0]["payload"]) is bytes
    assert calls[0]["qos"] == 1


@pytest.mark.parametrize(
    "status",
    [MqttReceiptStatus.PERSISTED, MqttReceiptStatus.REDELIVERY],
)
def test_on_message_acks_and_stays_connected_for_durable_results(status):
    client = FakeClient()
    consumer = make_consumer(lambda **_kwargs: receipt(status), client=client)

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == [(42, 1)]
    assert client.disconnect_calls == 0


def test_on_message_disconnects_when_receipt_raises():
    client = FakeClient()

    def failing(**_kwargs):
        raise RuntimeError("persistencia caída")

    consumer = make_consumer(failing, client=client)
    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == []
    assert client.disconnect_calls == 1


def test_on_message_disconnects_when_ack_send_fails():
    client = FakeClient()

    def rejected_ack(mid, qos):
        client.ack_calls.append((mid, qos))
        return 4

    client.ack = rejected_ack
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == [(42, 1)]
    assert client.disconnect_calls == 1


def test_identity_inconsistency_is_not_acknowledged_and_disconnects():
    client = FakeClient()
    consumer = make_consumer(
        lambda **_kwargs: receipt(MqttReceiptStatus.IDENTITY_INCONSISTENCY),
        client=client,
    )

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == []
    assert client.disconnect_calls == 1


def test_receipt_failure_logs_only_exception_type(consumer_log_records):
    payload = raw_payload(lora_rx_at=SENSITIVE_MARKER)

    def failing(**_kwargs):
        raise RuntimeError(f"driver text {SENSITIVE_MARKER}")

    result = make_consumer(failing).handle_message(TOPIC, payload, ack=lambda: None)

    assert result is None
    text = "\n".join(record.getMessage() for record in consumer_log_records)
    assert "RuntimeError" in text
    assert SENSITIVE_MARKER not in text
    assert payload.decode("utf-8") not in text


def test_oversized_payload_is_not_acknowledged_and_disconnects():
    from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES

    client = FakeClient()
    calls: list[dict] = []
    consumer = make_consumer(lambda **kwargs: calls.append(kwargs) or receipt(), client=client)

    consumer._on_message(
        client, None, FakeMessage(payload=b"x" * (MAX_MESSAGE_BYTES + 1))
    )

    assert calls == []
    assert client.ack_calls == []
    assert client.disconnect_calls == 1
