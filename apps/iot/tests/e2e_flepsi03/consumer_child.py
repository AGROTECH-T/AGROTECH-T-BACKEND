"""Proceso hijo: ejecuta el ``MqttConsumer`` REAL con instrumentación externa.

No modifica código de producción. Solo envuelve, desde fuera:

- ``receipt_func`` (parámetro público del constructor) alrededor del
  ``register_encrypted_mqtt_delivery`` real;
- ``client.ack`` / ``client.on_message`` / ``client.on_connect`` del cliente
  Paho del consumidor, para registrar la secuencia causal.

Cada evento se escribe en una traza JSONL con ``flush + fsync`` antes de
continuar, de modo que un ``kill`` abrupto deja la traza completa hasta ese
punto. La traza no contiene payloads, claves ni contraseñas.

Uso: ``python -B -m apps.iot.tests.e2e_flepsi03.consumer_child <config.json>``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, event: str, **fields) -> None:
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "pid": os.getpid(), "event": event, **fields}
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def _hold_forever(trace: Trace, reason: str) -> None:
    """Bloquea el hilo de Paho (sin PUBACK) hasta que el padre mate el proceso."""
    trace.emit("hold", reason=reason)
    while True:
        time.sleep(3600)


def _committed_rows(transport_key: bytes) -> int:
    """Cuenta filas desde una conexión MariaDB independiente (otro hilo)."""
    from django.db import connections

    from apps.iot.models import IotMqttDelivery

    box: dict[str, int] = {}

    def count() -> None:
        try:
            box["n"] = IotMqttDelivery.objects.filter(transport_key=transport_key).count()
        finally:
            connections.close_all()

    worker = threading.Thread(target=count)
    worker.start()
    worker.join(timeout=30)
    return box.get("n", -1)


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def main(config_path: str) -> int:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    trace = Trace(Path(config["trace"]))
    logging.basicConfig(
        filename=config["log"],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if config["identity_mode"] == "record":
        os.environ["IOT_BROKER_IDENTITY_MODE"] = "record"
        os.environ["IOT_BROKER_IDENTITY_HMAC_KEY"] = config["identity_key"]
        os.environ["IOT_BROKER_IDENTITY_HMAC_KEY_VERSION"] = "1"
    else:
        os.environ.pop("IOT_BROKER_IDENTITY_MODE", None)

    import django

    django.setup()
    from django.conf import settings
    from django.db import connection

    for name, value in config["django_settings"].items():
        setattr(settings, name, value)

    from apps.iot.broker_identity_config import load_broker_identity_config
    from apps.iot.db_guard import require_read_committed
    from apps.iot.mqtt.consumer import MqttConsumer, MqttSettings
    from apps.iot.services.mqtt_delivery_receipts import (
        MqttReceiptStatus,
        build_transport_key,
    )
    from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery

    identity = load_broker_identity_config()
    require_read_committed()
    mode = config["mode"]
    state = {"armed": True}

    def receipt(**kwargs):
        transport_key = build_transport_key(topic=kwargs["topic"], payload=kwargs["payload"])
        tk = transport_key.hex()[:16]
        trace.emit("receipt_enter", tk=tk, qos=kwargs["qos"])
        if mode == "hold_before_commit" and state["armed"]:
            state["armed"] = False
            _hold_forever(trace, "before_commit")
        if mode == "kill_db_connection_once" and state["armed"]:
            state["armed"] = False
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                conn_id = int(cursor.fetchone()[0])
            trace.emit("db_connection_ready", tk=tk, connection_id=conn_id)
            signal = Path(config["kill_done_file"])
            deadline = time.monotonic() + 60
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            trace.emit("db_connection_killed_ack", tk=tk, signalled=signal.exists())
        result = register_encrypted_mqtt_delivery(**kwargs)
        status = result.status.value
        trace.emit("receipt_return", tk=tk, status=status, error_code=result.error_code)
        if result.status in (MqttReceiptStatus.PERSISTED, MqttReceiptStatus.REDELIVERY):
            rows = _committed_rows(transport_key)
            trace.emit("commit_verified", tk=tk, rows_independent_connection=rows)
            if mode == "hold_after_commit" and state["armed"]:
                state["armed"] = False
                _hold_forever(trace, "after_commit_before_puback")
        return result

    mqtt_settings = MqttSettings(
        host=config["host"],
        port=int(config["port"]),
        client_id=config["client_id"],
        username=config["username"],
        password=config["password"],
        tls=True,
        ca_certs=config["ca_certs"],
        keepalive=15,
        reconnect_min_delay=1,
        reconnect_max_delay=2,
        session_expiry_seconds=int(config["session_expiry_seconds"]),
    )
    consumer = MqttConsumer(mqtt_settings, receipt_func=receipt, broker_identity=identity)
    client = consumer._client

    real_ack = client.ack

    def ack(mid, qos):
        trace.emit("puback_send", mid=mid, qos=qos)
        rc = real_ack(mid, qos)
        trace.emit("puback_result", mid=mid, rc=int(rc))
        return rc

    client.ack = ack

    real_on_message = client.on_message

    def on_message(cli, userdata, message):
        props = getattr(message, "properties", None)
        user_props = getattr(props, "UserProperty", None) if props else None
        trace.emit(
            "on_message",
            mid=message.mid,
            qos=message.qos,
            dup=int(bool(message.dup)),
            topic_suffix=message.topic.rsplit("/", 2)[-2],
            user_properties=None
            if user_props is None
            else [[key, _digest(value)] for key, value in user_props],
            user_properties_type=type(user_props).__name__,
        )
        return real_on_message(cli, userdata, message)

    client.on_message = on_message

    real_on_connect = client.on_connect

    def on_connect(cli, userdata, flags, reason_code, properties=None):
        trace.emit(
            "on_connect",
            session_present=bool(getattr(flags, "session_present", False)),
            reason=str(reason_code),
            protocol=int(getattr(cli, "_protocol", 0)),
        )
        return real_on_connect(cli, userdata, flags, reason_code, properties)

    client.on_connect = on_connect

    trace.emit(
        "child_start",
        mode=mode,
        identity_mode=identity.mode.value,
        client_id=config["client_id"],
        session_expiry_seconds=mqtt_settings.session_expiry_seconds,
    )
    runner = threading.Thread(target=consumer.run, daemon=True)
    runner.start()
    stop_file = Path(config["stop_file"])
    while runner.is_alive():
        if stop_file.exists():
            trace.emit("stop_requested")
            consumer.stop()
            runner.join(timeout=30)
            break
        time.sleep(0.1)
    trace.emit("child_exit", runner_alive=runner.is_alive())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
