# ruff: noqa: S101, S105, S106
"""Arranque de los procesos MQTT de despliegue, sin red ni base de datos.

Propósito: fijar cómo se validan los comandos ``mqtt_consumer`` y
``mqtt_delivery_worker`` que ejecutan los servicios de Render. Los valores son
ficticios; ``--check`` nunca abre conexión.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

MQTT_VARIABLES = (
    "MQTT_HOST",
    "MQTT_PORT",
    "MQTT_USERNAME",
    "MQTT_PASSWORD",
    "MQTT_CLIENT_ID",
    "MQTT_TLS",
    "MQTT_CA_CERTS",
    "MQTT_TLS_INSECURE",
    "MQTT_KEEPALIVE",
    "MQTT_RECONNECT_MIN_DELAY",
    "MQTT_RECONNECT_MAX_DELAY",
)


@pytest.fixture
def mqtt_env(monkeypatch):
    for name in MQTT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    def apply(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return apply


@pytest.fixture
def no_consumer(monkeypatch):
    """Fail the test if --check would ever build a client or connect."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("--check no debe crear el consumidor")

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_consumer.MqttConsumer", forbidden
    )


def test_consumer_check_accepts_tls_configuration(mqtt_env, no_consumer):
    mqtt_env(
        MQTT_HOST="broker.example.invalid",
        MQTT_PORT="8883",
        MQTT_CLIENT_ID="agrotech-consumer-test",
        MQTT_USERNAME="placeholder-user",
        MQTT_TLS="true",
    )
    out = StringIO()

    call_command("mqtt_consumer", "--check", stdout=out)

    assert "Configuración MQTT válida." in out.getvalue()
    assert "placeholder-user" not in out.getvalue()


@pytest.mark.parametrize(
    "values",
    [
        {"MQTT_CLIENT_ID": "c"},  # host ausente
        {"MQTT_HOST": "h"},  # client id ausente
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_PASSWORD": "x"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_TLS_INSECURE": "true"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_CA_CERTS": "/ca.pem"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_PORT": "0"},
        {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_TLS": "maybe"},
    ],
)
def test_consumer_check_rejects_invalid_configuration(mqtt_env, no_consumer, values):
    mqtt_env(**values)

    with pytest.raises(CommandError):
        call_command("mqtt_consumer", "--check", stdout=StringIO())


def test_consumer_check_error_does_not_echo_password(mqtt_env, no_consumer):
    mqtt_env(MQTT_HOST="h", MQTT_CLIENT_ID="c", MQTT_PASSWORD="SENSITIVE-7f3a")

    with pytest.raises(CommandError) as excinfo:
        call_command("mqtt_consumer", "--check", stdout=StringIO())

    assert "SENSITIVE-7f3a" not in str(excinfo.value)


@pytest.mark.parametrize(
    "args",
    [
        ["--poll-seconds", "0"],
        ["--poll-seconds", "61"],
        ["--batch-size", "0"],
        ["--batch-size", "101"],
        ["--worker-id", "x" * 101],
        ["--worker-id", "   "],
    ],
)
def test_worker_command_rejects_invalid_arguments(monkeypatch, args):
    def forbidden(**_kwargs):
        raise AssertionError("no debe procesar con argumentos inválidos")

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker.run_mqtt_delivery_worker_once",
        forbidden,
    )

    with pytest.raises(CommandError):
        call_command("mqtt_delivery_worker", *args, stdout=StringIO())


def test_worker_command_runs_batches_until_interrupted(monkeypatch):
    from apps.iot.services.mqtt_delivery_worker import WorkerBatchResult

    calls: list[dict] = []
    sleeps: list[float] = []

    def fake_batch(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return WorkerBatchResult(0, 0, 0, 0)
        if len(calls) == 2:
            raise RuntimeError("fallo transitorio")
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker.run_mqtt_delivery_worker_once",
        fake_batch,
    )
    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker._idle",
        lambda _stopping, seconds: sleeps.append(seconds),
    )
    out = StringIO()

    call_command(
        "mqtt_delivery_worker",
        "--poll-seconds",
        "1",
        "--batch-size",
        "10",
        "--worker-id",
        "render-worker-1",
        stdout=out,
    )

    assert [call["worker_id"] for call in calls] == ["render-worker-1"] * 3
    assert all(call["batch_size"] == 10 for call in calls)
    # Idle batch and operational error both back off; the loop survives errors.
    assert sleeps == [1.0, 1.0]
    assert "Worker MQTT durable detenido." in out.getvalue()
