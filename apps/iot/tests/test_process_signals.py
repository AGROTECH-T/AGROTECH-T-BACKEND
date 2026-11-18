# ruff: noqa: S101, S603
"""VALIDATION-02 fase F: SIGTERM y ciclo de vida de consumer y worker.

Las señales son reales (``signal.raise_signal``) y se entregan al propio
proceso de pruebas; no hay red ni base de datos. La prueba entre procesos
solo se ejecuta en POSIX: en Windows ``os.kill(pid, SIGTERM)`` es
``TerminateProcess`` y no entrega ninguna señal.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from io import StringIO

import pytest
from django.core.management import call_command

from apps.iot.management.commands.mqtt_consumer import Command as ConsumerCommand
from apps.iot.mqtt.consumer import MqttConsumer
from apps.iot.process_signals import graceful_stop
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptResult, MqttReceiptStatus
from apps.iot.services.mqtt_delivery_worker import WorkerBatchResult
from apps.iot.tests.test_mqtt_consumer import FakeMessage
from apps.iot.tests.test_mqtt_consumer_hardening import ScriptedClient, settings_for

SIGTERM = signal.SIGTERM


@pytest.fixture(autouse=True)
def advisory_candidate_without_database(monkeypatch):
    monkeypatch.setattr(
        "apps.iot.mqtt.consumer._coordinator_candidate", lambda topic, payload: None
    )


class RecordingReceipt:
    def __init__(self, during=None):
        self.calls = 0
        self.during = during
        self.events: list[str] = []

    def __call__(self, **_kwargs):
        self.calls += 1
        self.events.append("persist-start")
        if self.during and self.calls == 1:
            self.during()
        self.events.append("persist-end")
        return MqttReceiptResult(MqttReceiptStatus.PERSISTED)


def _consumer(client, receipt, sleeps=None):
    return MqttConsumer(
        settings_for(),
        client=client,
        receipt_func=receipt,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        persistence_probe=lambda: None,
    )


def _deliver(client, mid):
    message = FakeMessage()
    message.mid = mid
    client.on_message(client, None, message)


# --------------------------------------------------------------------------
# Consumer
# --------------------------------------------------------------------------


def test_sigterm_stops_consumer_cleanly_and_blocks_new_acks():
    receipt = RecordingReceipt()

    def step(client):
        _deliver(client, 1)
        signal.raise_signal(SIGTERM)
        _deliver(client, 2)  # arrives after the stop request

    client = ScriptedClient([step])
    consumer = _consumer(client, receipt)

    with graceful_stop(consumer.stop, process="test") as received:
        consumer.run()  # returns normally: requested stop, not a fatal error

    assert received == [SIGTERM]
    assert receipt.calls == 1
    assert client.ack_calls == [(1, 1)]  # message 2 neither persisted nor acked
    assert len(client.connect_async_calls) == 1  # no reconnection after stop


def test_sigterm_during_persistence_finishes_transaction_before_ack():
    receipt = RecordingReceipt(during=lambda: signal.raise_signal(SIGTERM))
    client = ScriptedClient([lambda c: (_deliver(c, 7), _deliver(c, 8))])
    consumer = _consumer(client, receipt)
    order: list[str] = []
    original_ack = client.ack

    def ack(mid, qos):
        order.append(f"ack-{mid}")
        return original_ack(mid, qos)

    client.ack = ack

    with graceful_stop(consumer.stop, process="test"):
        consumer.run()

    assert receipt.events == ["persist-start", "persist-end"]
    assert order == ["ack-7"]  # ACK only after the durable insert completed
    assert receipt.calls == 1


def test_sigterm_while_waiting_for_persistence_does_not_reconnect():
    def failing_receipt(**_kwargs):
        raise RuntimeError("MariaDB caída")

    sleeps: list[float] = []
    client = ScriptedClient([lambda c: _deliver(c, 3)])
    consumer = _consumer(client, failing_receipt, sleeps=sleeps)
    consumer._sleep = lambda _seconds: signal.raise_signal(SIGTERM)

    with graceful_stop(consumer.stop, process="test"):
        consumer.run()

    assert client.ack_calls == []
    assert len(client.connect_async_calls) == 1


def test_consumer_command_exits_zero_on_sigterm(monkeypatch):
    stopped = []

    def run(self):
        signal.raise_signal(SIGTERM)
        assert stopped == [True]

    monkeypatch.setattr(MqttConsumer, "__init__", lambda self, settings, **_kwargs: None)
    monkeypatch.setattr(MqttConsumer, "run", run)
    monkeypatch.setattr(MqttConsumer, "stop", lambda self: stopped.append(True))
    for name, value in {"MQTT_HOST": "b", "MQTT_CLIENT_ID": "c", "MQTT_TLS": "true"}.items():
        monkeypatch.setenv(name, value)

    ConsumerCommand().run_from_argv(["manage.py", "mqtt_consumer"])  # no SystemExit

    assert stopped == [True]


def test_handlers_are_restored_after_the_block():
    previous = signal.getsignal(SIGTERM)
    with graceful_stop(lambda: None, process="test"):
        assert signal.getsignal(SIGTERM) is not previous
    assert signal.getsignal(SIGTERM) is previous


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def test_sigterm_lets_worker_finish_current_batch_then_exit(monkeypatch):
    calls: list[str] = []

    def batch(**_kwargs):
        calls.append("start")
        signal.raise_signal(SIGTERM)  # arrives mid-batch
        calls.append("end")
        return WorkerBatchResult(1, 1, 1, 0)

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker.run_mqtt_delivery_worker_once", batch
    )
    out = StringIO()

    call_command("mqtt_delivery_worker", "--worker-id", "w-sig", stdout=out)

    assert calls == ["start", "end"]  # batch completed, no further batch
    assert "Worker MQTT durable detenido." in out.getvalue()


def test_sigterm_interrupts_worker_idle_wait(monkeypatch):
    import threading
    import time

    def idle_batch(**_kwargs):
        threading.Timer(0.2, lambda: signal.raise_signal(SIGTERM)).start()
        return WorkerBatchResult(0, 0, 0, 0)

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker.run_mqtt_delivery_worker_once",
        idle_batch,
    )
    started = time.monotonic()

    call_command("mqtt_delivery_worker", "--poll-seconds", "30", stdout=StringIO())

    assert time.monotonic() - started < 10  # did not wait the full 30 s poll


# --------------------------------------------------------------------------
# Entre procesos (POSIX)
# --------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Windows no entrega SIGTERM entre procesos")
def test_real_process_handles_sigterm_and_exits_zero(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys, threading
            sys.path.insert(0, sys.argv[1])
            from apps.iot.process_signals import graceful_stop
            stop = threading.Event()
            with graceful_stop(stop.set, process="child"):
                print("ready", flush=True)
                stop.wait(30)
            sys.exit(0 if stop.is_set() else 3)
            """
        ),
        encoding="utf-8",
    )
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    child = subprocess.Popen(
        [sys.executable, str(script), root], stdout=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "ready"
    child.send_signal(SIGTERM)

    assert child.wait(timeout=10) == 0
