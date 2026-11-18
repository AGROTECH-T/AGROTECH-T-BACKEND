# ruff: noqa: S101, S105, S106
"""HARDEN-01 S2/S3: ciclo de vida del consumidor MQTT y TLS obligatorio.

Sin red ni base de datos: el cliente Paho se sustituye por un doble que
ejecuta un guion por cada ``loop_forever``. El comportamiento de Paho 2.2.0
que se imita (``loop_forever`` retorna con normalidad tras ``disconnect()``)
se verificó leyendo el código instalado.
"""

from __future__ import annotations

import ssl

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.iot.management.commands.mqtt_consumer import Command as ConsumerCommand
from apps.iot.mqtt.consumer import (
    MqttConfigurationError,
    MqttConsumer,
    MqttConsumerFatalError,
    MqttSettings,
    _build_client,
)
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptResult, MqttReceiptStatus
from apps.iot.tests.test_mqtt_consumer import FakeClient, FakeMessage, FakeReason

MQTT_VARS = (
    "MQTT_HOST",
    "MQTT_PORT",
    "MQTT_CLIENT_ID",
    "MQTT_USERNAME",
    "MQTT_PASSWORD",
    "MQTT_TLS",
    "MQTT_CA_CERTS",
    "MQTT_TLS_INSECURE",
    "MQTT_KEEPALIVE",
    "MQTT_RECONNECT_MIN_DELAY",
    "MQTT_RECONNECT_MAX_DELAY",
    "MQTT_MAX_PERSISTENCE_FAILURES",
    "MQTT_MAX_CONNECT_FAILURES",
)


@pytest.fixture(autouse=True)
def advisory_candidate_without_database(monkeypatch):
    monkeypatch.setattr(
        "apps.iot.mqtt.consumer._coordinator_candidate", lambda topic, payload: None
    )


class ScriptedClient(FakeClient):
    """Each ``loop_forever`` call runs the next scripted step."""

    def __init__(self, steps) -> None:
        super().__init__()
        self.steps = list(steps)
        self.on_connect_fail = None

    def loop_forever(self, **kwargs) -> None:
        self.loop_forever_calls.append(kwargs)
        if not self.steps:
            raise AssertionError("loop_forever llamado más veces de lo previsto")
        self.steps.pop(0)(self)


class FlakyReceipt:
    """Receipt service that fails a given number of times, then persists."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.persisted = 0

    def __call__(self, **_kwargs) -> MqttReceiptResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("MariaDB no disponible")
        self.persisted += 1
        if self.persisted == 1:
            return MqttReceiptResult(MqttReceiptStatus.PERSISTED)
        return MqttReceiptResult(MqttReceiptStatus.REDELIVERY)


def settings_for(**overrides) -> MqttSettings:
    values = {
        "host": "broker.test",
        "port": 8883,
        "client_id": "ingest-test",
        "tls": True,
        "reconnect_min_delay": 1,
        "reconnect_max_delay": 8,
        "max_persistence_failures": 3,
        "max_connect_failures": 3,
    }
    values.update(overrides)
    return MqttSettings(**values)


def make(client, receipt, *, probe=None, sleeps=None, **settings_overrides):
    sleeps = [] if sleeps is None else sleeps
    return MqttConsumer(
        settings_for(**settings_overrides),
        client=client,
        receipt_func=receipt,
        sleep=sleeps.append,
        persistence_probe=probe or (lambda: None),
    )


def deliver(consumer_ref, message=None):
    def step(client):
        client.on_message(client, None, message or FakeMessage())

    return step


def stop(consumer_ref):
    def step(_client):
        consumer_ref[0].stop()

    return step


# --------------------------------------------------------------------------
# L. Error de persistencia sin PUBACK / M. Recuperación
# --------------------------------------------------------------------------


def test_persistence_error_never_acks_and_disconnects():
    client = FakeClient()
    consumer = make(client, FlakyReceipt(failures=1))

    consumer._on_message(client, None, FakeMessage())

    assert client.ack_calls == []
    assert client.disconnect_calls == 1
    assert consumer._halt == "persistence"
    assert consumer._persistence_failures == 1


def test_messages_after_a_halt_are_neither_persisted_nor_acked():
    client = FakeClient()
    receipt = FlakyReceipt(failures=1)
    consumer = make(client, receipt)

    consumer._on_message(client, None, FakeMessage())
    consumer._on_message(client, None, FakeMessage())

    assert receipt.calls == 1
    assert client.ack_calls == []


def test_consumer_recovers_and_acks_the_redelivered_message():
    ref = []
    receipt = FlakyReceipt(failures=1)
    sleeps: list[float] = []
    # Second loop: the broker redelivers the unacked message and we stop
    # inside the same loop after persisting it.
    client = ScriptedClient([deliver(ref), lambda c: (deliver(ref)(c), stop(ref)(c))])
    consumer = make(client, receipt, sleeps=sleeps)
    ref.append(consumer)

    consumer.run()

    assert receipt.calls == 2
    assert client.ack_calls == [(42, 1)]  # only after durable persistence
    assert len(client.connect_async_calls) == 2  # controlled reconnection
    assert sleeps == [1]
    assert consumer._persistence_failures == 0


def test_recovery_waits_until_persistence_probe_succeeds():
    ref = []
    probe_results = [RuntimeError("caída"), RuntimeError("caída"), None]

    def probe():
        result = probe_results.pop(0)
        if result is not None:
            raise result

    sleeps: list[float] = []
    receipt = FlakyReceipt(failures=1)
    client = ScriptedClient([deliver(ref), lambda c: (deliver(ref)(c), stop(ref)(c))])
    consumer = make(
        client, receipt, probe=probe, sleeps=sleeps, max_persistence_failures=5
    )
    ref.append(consumer)

    consumer.run()

    assert sleeps == [1, 2, 4]  # exponential backoff, bounded by max delay
    assert client.ack_calls == [(42, 1)]


def test_persistent_database_failure_ends_with_fatal_error_and_no_ack():
    ref = []
    sleeps: list[float] = []

    def probe():
        raise RuntimeError("MariaDB caída")

    client = ScriptedClient([deliver(ref)])
    consumer = make(client, FlakyReceipt(failures=99), probe=probe, sleeps=sleeps)
    ref.append(consumer)

    with pytest.raises(MqttConsumerFatalError, match="persistencia"):
        consumer.run()

    assert client.ack_calls == []
    assert len(sleeps) == 2  # 1 failed receipt + 2 failed probes = limit 3
    assert client.disconnect_calls >= 2  # halt + final cleanup


def test_repeated_receipt_failures_reach_the_limit():
    ref = []
    client = ScriptedClient([deliver(ref), deliver(ref), deliver(ref)])
    consumer = make(client, FlakyReceipt(failures=99))
    ref.append(consumer)

    with pytest.raises(MqttConsumerFatalError):
        consumer.run()

    assert client.ack_calls == []
    assert len(client.connect_async_calls) == 3


def test_unexplained_loop_exit_is_fatal():
    client = ScriptedClient([lambda c: None])
    consumer = make(client, FlakyReceipt(failures=0))

    with pytest.raises(MqttConsumerFatalError, match="sin una detención"):
        consumer.run()


def test_keyboard_interrupt_is_a_clean_stop():
    def interrupt(_client):
        raise KeyboardInterrupt

    client = ScriptedClient([interrupt])
    consumer = make(client, FlakyReceipt(failures=0))

    consumer.run()

    assert client.disconnect_calls == 1


# --------------------------------------------------------------------------
# Desconexión MQTT y reconexión acotada
# --------------------------------------------------------------------------


def test_connection_failures_are_bounded_and_fatal():
    def failing_connections(client):
        for _ in range(3):
            client.on_connect_fail(client, None)

    client = ScriptedClient([failing_connections])
    consumer = make(client, FlakyReceipt(failures=0))

    with pytest.raises(MqttConsumerFatalError, match="conexión MQTT fallida 3"):
        consumer.run()

    assert client.disconnect_calls >= 1


def test_successful_connection_resets_the_failure_counter():
    client = FakeClient()
    consumer = make(client, FlakyReceipt(failures=0))

    consumer._on_connect_fail(client, None)
    consumer._on_connect_fail(client, None)
    consumer._on_connect(client, None, None, FakeReason(False))
    consumer._on_connect_fail(client, None)

    assert consumer._connect_failures == 1
    assert consumer._halt is None


def test_unexpected_disconnect_counts_and_rejected_connect_counts():
    client = FakeClient()
    consumer = make(client, FlakyReceipt(failures=0))

    consumer._on_disconnect(client, None, None, FakeReason(True))
    consumer._on_connect(client, None, None, FakeReason(True))

    assert consumer._connect_failures == 2


def test_disconnect_during_our_own_halt_is_not_a_connection_failure():
    client = FakeClient()
    consumer = make(client, FlakyReceipt(failures=1))

    consumer._on_message(client, None, FakeMessage())
    consumer._on_disconnect(client, None, None, FakeReason(True))

    assert consumer._connect_failures == 0


def test_command_exits_non_zero_on_fatal_error(monkeypatch):
    for name in MQTT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MQTT_HOST", "broker.test")
    monkeypatch.setenv("MQTT_CLIENT_ID", "ingest-test")
    monkeypatch.setenv("MQTT_TLS", "true")

    def fatal(self):
        raise MqttConsumerFatalError("persistencia durable fallida 5 veces consecutivas")

    monkeypatch.setattr(MqttConsumer, "run", fatal)
    monkeypatch.setattr(MqttConsumer, "__init__", lambda self, settings, **_kwargs: None)

    with pytest.raises(SystemExit) as exit_info:
        ConsumerCommand().run_from_argv(["manage.py", "mqtt_consumer"])

    assert exit_info.value.code == 1


# --------------------------------------------------------------------------
# N. TLS obligatorio en producción
# --------------------------------------------------------------------------

BASE = {"MQTT_HOST": "broker.test", "MQTT_CLIENT_ID": "ingest-test"}


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"MQTT_TLS": "false"},
        {"MQTT_TLS": "true", "MQTT_TLS_INSECURE": "true"},
        {"MQTT_TLS": "true", "MQTT_CA_CERTS": "Z:/no-existe/ca.pem"},
    ],
)
def test_production_rejects_insecure_mqtt(overrides):
    with pytest.raises(MqttConfigurationError):
        MqttSettings.from_env({**BASE, **overrides}, production=True)


def test_production_accepts_tls_with_system_or_custom_ca(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder", encoding="ascii")

    system = MqttSettings.from_env({**BASE, "MQTT_TLS": "true"}, production=True)
    custom = MqttSettings.from_env(
        {**BASE, "MQTT_TLS": "true", "MQTT_CA_CERTS": str(ca)}, production=True
    )

    assert system.tls is True and system.tls_insecure is False
    assert custom.ca_certs == str(ca)


def test_isolated_test_mode_keeps_plain_mqtt_available():
    assert MqttSettings.from_env(BASE, production=False).tls is False


def test_production_mode_follows_django_debug(settings):
    settings.DEBUG = False
    with pytest.raises(MqttConfigurationError, match="obligatoria en producción"):
        MqttSettings.from_env(BASE)
    settings.DEBUG = True
    assert MqttSettings.from_env(BASE).tls is False


def test_check_command_rejects_production_without_tls(monkeypatch, settings):
    settings.DEBUG = False
    for name in MQTT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MQTT_HOST", "broker.test")
    monkeypatch.setenv("MQTT_CLIENT_ID", "ingest-test")
    monkeypatch.setenv("MQTT_TLS", "false")

    with pytest.raises(CommandError, match="MQTT_TLS=true"):
        call_command("mqtt_consumer", "--check")

    monkeypatch.setenv("MQTT_TLS", "true")
    call_command("mqtt_consumer", "--check")


def test_real_tls_client_verifies_certificates_and_hostname():
    client = _build_client(settings_for(tls=True))

    context = client._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_reconnect_delays_must_be_ordered():
    with pytest.raises(MqttConfigurationError):
        MqttSettings.from_env(
            {**BASE, "MQTT_RECONNECT_MIN_DELAY": "10", "MQTT_RECONNECT_MAX_DELAY": "5"},
            production=False,
        )
