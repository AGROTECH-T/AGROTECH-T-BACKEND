# ruff: noqa: S101, S105, S106
"""Pruebas del consumidor MQTT sin red ni base de datos."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from apps.iot.mqtt.consumer import (
    TOPIC_FILTER,
    MqttConfigurationError,
    MqttConsumer,
    MqttConsumerFatalError,
    MqttSettings,
    _build_client,
)
from apps.iot.mqtt.delivery import DeliveryClassification, classify_result
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.ingestion import IngestionResult, IngestionStatus
from apps.iot.services.mqtt_delivery_receipts import (
    MqttReceiptResult,
    MqttReceiptStatus,
)
from apps.iot.tests import lora_vectors

GATEWAY_UID = "GW-7K3M9QXT2A"
TOPIC = f"agrotech/v1/gw/{GATEWAY_UID}/up"
RECEIVED_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def raw_payload(**overrides) -> bytes:
    document = {
        "v": 1,
        "frame": base64.b64encode(lora_vectors.REFERENCE_FRAME).decode("ascii"),
        "lora_rx_at": "2026-11-13T11:59:00+00:00",
        "clock_synced": True,
        "buffered": False,
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def receipt(status: MqttReceiptStatus = MqttReceiptStatus.PERSISTED) -> MqttReceiptResult:
    return MqttReceiptResult(status)


class FakeReason:
    def __init__(self, failure: bool = False) -> None:
        self.is_failure = failure

    def __str__(self) -> str:
        return "fallo" if self.is_failure else "ok"


class FakeMessage:
    def __init__(self, *, topic: str = TOPIC, payload: bytes | None = None):
        self.topic = topic
        self.payload = raw_payload() if payload is None else payload
        self.mid = 42
        self.qos = 1


class FakeClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.ack_calls: list[tuple[int, int]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.connect_async_calls: list[tuple[str, int, int]] = []
        self.connect_async_kwargs: list[dict] = []
        self.loop_forever_calls: list[dict] = []
        self.disconnect_calls = 0
        self.reconnect_delay = None
        self.username_password = None
        self.tls = None
        self.tls_insecure = None

    def reconnect_delay_set(self, *, min_delay, max_delay) -> None:
        self.reconnect_delay = (min_delay, max_delay)

    def username_pw_set(self, username, password=None) -> None:
        self.username_password = (username, password)

    def tls_set(self, **kwargs) -> None:
        self.tls = kwargs

    def tls_insecure_set(self, value) -> None:
        self.tls_insecure = value

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))
        return (0, 1)

    def ack(self, mid, qos):
        self.ack_calls.append((mid, qos))
        return 0

    def connect_async(self, host, port, keepalive, **kwargs) -> None:
        # BUILD-FLEPSI-02: MQTT 5 passes clean_start and CONNECT properties.
        self.connect_async_calls.append((host, port, keepalive))
        self.connect_async_kwargs.append(kwargs)

    def loop_forever(self, **kwargs) -> None:
        self.loop_forever_calls.append(kwargs)

    def disconnect(self) -> None:
        self.disconnect_calls += 1


@pytest.fixture(autouse=True)
def advisory_candidate_without_database(monkeypatch):
    monkeypatch.setattr(
        "apps.iot.mqtt.consumer._coordinator_candidate",
        lambda topic, payload: None,
    )


def make_consumer(receipt_func, *, client=None) -> MqttConsumer:
    settings = MqttSettings(host="localhost", port=1883, client_id="test-client")
    return MqttConsumer(
        settings,
        client=FakeClient() if client is None else client,
        receipt_func=receipt_func,
        clock=lambda: RECEIVED_AT,
    )


def test_successful_receipt_acknowledges_after_persistence() -> None:
    events: list[str] = []
    captured = {}

    def fake_receipt(**kwargs):
        events.append("receipt")
        captured.update(kwargs)
        return receipt()

    consumer = make_consumer(fake_receipt)
    payload = raw_payload()

    result = consumer.handle_message(TOPIC, payload, ack=lambda: events.append("ack"))

    assert result.status is MqttReceiptStatus.PERSISTED
    assert events == ["receipt", "ack"]
    assert captured["topic"] == TOPIC
    assert captured["payload"] == payload
    assert captured["qos"] == 1
    assert captured["max_payload_bytes"] == MAX_MESSAGE_BYTES


def test_receipt_exception_does_not_acknowledge() -> None:
    acked = []

    def fake_receipt(**_kwargs):
        raise RuntimeError("base de datos no disponible")

    consumer = make_consumer(fake_receipt)

    result = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(True))

    assert result is None
    assert acked == []


def test_invalid_topic_is_still_stored_durably() -> None:
    captured = {}
    acked = []

    def fake_receipt(**kwargs):
        captured.update(kwargs)
        return receipt()

    consumer = make_consumer(fake_receipt)

    result = consumer.handle_message(
        "agrotech/v1/gw/+/up", raw_payload(), ack=lambda: acked.append(True)
    )

    assert result.status is MqttReceiptStatus.PERSISTED
    assert acked == [True]
    assert captured["topic"] == "agrotech/v1/gw/+/up"


def test_invalid_payload_is_still_stored_durably() -> None:
    captured = {}
    acked = []

    def fake_receipt(**kwargs):
        captured.update(kwargs)
        return receipt()

    consumer = make_consumer(fake_receipt)

    result = consumer.handle_message(TOPIC, b"{", ack=lambda: acked.append(True))

    assert result.status is MqttReceiptStatus.PERSISTED
    assert acked == [True]
    assert captured["payload"] == b"{"


def test_oversized_payload_is_not_acknowledged_without_receipt() -> None:
    called = []
    acked = []
    consumer = make_consumer(lambda **kwargs: called.append(kwargs) or receipt())

    result = consumer.handle_message(
        TOPIC, b" " * (MAX_MESSAGE_BYTES + 1), ack=lambda: acked.append(True)
    )

    assert result is None
    assert acked == []
    assert called == []


@pytest.mark.parametrize(
    "status",
    [
        MqttReceiptStatus.IDENTITY_INCONSISTENCY,
        MqttReceiptStatus.PERSISTENCE_ERROR,
    ],
)
def test_non_durable_receipt_status_is_not_acknowledged(status) -> None:
    acked = []
    consumer = make_consumer(lambda **_kwargs: receipt(status))

    result = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(True))

    assert result.status is status
    assert acked == []


@pytest.mark.parametrize(
    "malformed_result",
    [
        None,
        object(),
        MqttReceiptResult(status="persisted"),
        MqttReceiptResult(status=[]),
    ],
)
def test_malformed_receipt_result_is_operational_and_not_acknowledged(
    malformed_result,
) -> None:
    acked = []
    consumer = make_consumer(lambda **_kwargs: malformed_result)

    result = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(True))

    assert result is None
    assert acked == []


def test_ack_exception_does_not_return_a_confirmed_result() -> None:
    consumer = make_consumer(lambda **_kwargs: receipt())

    def fail_ack() -> None:
        raise RuntimeError("ack failed")

    result = consumer.handle_message(TOPIC, raw_payload(), ack=fail_ack)

    assert result is None


def test_mqtt_ack_error_code_does_not_return_a_confirmed_result() -> None:
    client = FakeClient()

    def rejected_ack(mid, qos):
        client.ack_calls.append((mid, qos))
        return 1

    client.ack = rejected_ack
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)
    message = FakeMessage()

    result = consumer.handle_message(
        message.topic,
        message.payload,
        ack=lambda: consumer._ack(message),
    )

    assert result is None
    assert client.ack_calls == [(message.mid, message.qos)]


@pytest.mark.parametrize(
    ("status", "classification"),
    [
        (IngestionStatus.REJECTED_GATEWAY, DeliveryClassification.REJECTED_GATEWAY),
        (
            IngestionStatus.REJECTED_AUTHORIZATION,
            DeliveryClassification.REJECTED_AUTHORIZATION,
        ),
        (IngestionStatus.REJECTED_CREDENTIAL, DeliveryClassification.REJECTED_CREDENTIAL),
        (IngestionStatus.REJECTED_HMAC, DeliveryClassification.REJECTED_HMAC),
        (IngestionStatus.REJECTED_GENERATION, DeliveryClassification.REJECTED_GENERATION),
    ],
)
def test_authentication_and_authorization_results_have_distinct_classifications(
    status, classification
) -> None:
    assert classify_result(IngestionResult(status)) is classification


def test_redelivery_acknowledges_existing_row() -> None:
    acked = []
    consumer = make_consumer(
        lambda **_kwargs: receipt(MqttReceiptStatus.REDELIVERY)
    )

    result = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(True))

    assert result.status is MqttReceiptStatus.REDELIVERY
    assert acked == [True]


def test_failed_attempt_is_retried_on_broker_redelivery() -> None:
    attempts = []
    acked = []

    def fake_receipt(**_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("fallo transitorio")
        return receipt(MqttReceiptStatus.REDELIVERY)

    consumer = make_consumer(fake_receipt)

    first = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(1))
    second = consumer.handle_message(TOPIC, raw_payload(), ack=lambda: acked.append(2))

    assert first is None
    assert second.status is MqttReceiptStatus.REDELIVERY
    assert acked == [2]


def test_on_message_uses_manual_ack_with_mid_and_qos() -> None:
    client = FakeClient()
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == [(42, 1)]


def test_on_message_disconnects_when_receipt_is_not_durable() -> None:
    client = FakeClient()
    consumer = make_consumer(
        lambda **_kwargs: receipt(MqttReceiptStatus.PERSISTENCE_ERROR), client=client
    )

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == []
    assert client.disconnect_calls == 1


def test_on_connect_subscribes_to_the_contract_filter() -> None:
    client = FakeClient()
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)

    consumer._on_connect(client, None, None, FakeReason(False))

    assert client.subscriptions == [(TOPIC_FILTER, 1)]


def test_on_connect_failure_does_not_subscribe() -> None:
    client = FakeClient()
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)

    consumer._on_connect(client, None, None, FakeReason(True))

    assert client.subscriptions == []


def test_construction_does_not_open_a_connection() -> None:
    client = FakeClient()
    make_consumer(lambda **_kwargs: receipt(), client=client)

    assert client.connect_async_calls == []
    assert client.loop_forever_calls == []


def test_run_uses_async_connect_and_retries_the_first_connection() -> None:
    client = FakeClient()
    consumer = make_consumer(lambda **_kwargs: receipt(), client=client)

    # HARDEN-01 S2: a loop that ends without stop() is a fatal condition.
    with pytest.raises(MqttConsumerFatalError):
        consumer.run()

    assert client.connect_async_calls == [("localhost", 1883, 60)]
    assert client.loop_forever_calls == [{"retry_first_connection": True}]
    assert client.disconnect_calls == 1


def test_settings_from_environment() -> None:
    settings = MqttSettings.from_env(
        {
            "MQTT_HOST": "broker.local",
            "MQTT_PORT": "8883",
            "MQTT_CLIENT_ID": "ingest-1",
            "MQTT_USERNAME": "user",
            "MQTT_PASSWORD": "secret",
            "MQTT_TLS": "true",
            "MQTT_CA_CERTS": "C:/certs/ca.pem",
            "MQTT_TLS_INSECURE": "false",
            "MQTT_KEEPALIVE": "30",
            "MQTT_RECONNECT_MIN_DELAY": "2",
            "MQTT_RECONNECT_MAX_DELAY": "20",
        },
        production=False,
    )

    assert settings.host == "broker.local"
    assert settings.port == 8883
    assert settings.client_id == "ingest-1"
    assert settings.username == "user"
    assert settings.password == "secret"
    assert settings.tls is True
    assert settings.ca_certs == "C:/certs/ca.pem"
    assert settings.keepalive == 30
    assert settings.reconnect_min_delay == 2
    assert settings.reconnect_max_delay == 20
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"MQTT_CLIENT_ID": "x"},
        {"MQTT_HOST": "h"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_PASSWORD": "p"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_CA_CERTS": "ca.pem"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_TLS_INSECURE": "true"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_PORT": "0"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_PORT": "abc"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_TLS": "maybe"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_KEEPALIVE": "1"},
    ],
)
def test_invalid_settings_are_rejected(environ) -> None:
    with pytest.raises(MqttConfigurationError):
        MqttSettings.from_env(environ, production=False)


def test_real_client_uses_manual_ack_and_persistent_session() -> None:
    client = _build_client(
        MqttSettings(host="localhost", port=1883, client_id="persistent")
    )

    assert client._manual_ack is True
    # BUILD-FLEPSI-02: MQTT 5; session persistence comes from clean_start=False
    # plus SessionExpiryInterval at connect time (see test_mqtt5_connect_*).
    assert client._protocol == 5
    assert client._client_id == b"persistent"
