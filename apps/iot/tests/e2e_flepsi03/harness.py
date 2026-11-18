"""Recursos propios de BUILD-FLEPSI-03: broker Mosquitto desechable, publicador
MQTT 5 y procesos hijo del consumidor real.

Aislamiento:

- Directorio exclusivo ``<TEMP>/agrotech-flepsi03/run-*``; se borra al final.
- Mosquitto propio (mismo binario instalado, configuración propia) solo en
  ``127.0.0.1``, en un puerto libre distinto de los ya usados; TLS con CA de
  prueba generada aquí; usuarios y contraseñas aleatorios de la prueba; ACL
  limitada a ``agrotech/v1/gw/+/up``; sin bridges.
- Solo se detienen los ``Popen`` creados aquí. Nunca se lee la configuración
  ni el descriptor de otros brokers.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

PHASE_ROOT = Path(tempfile.gettempdir()) / "agrotech-flepsi03"
OPT_IN = PHASE_ROOT / "OPT_IN"
MOSQUITTO_DIR = Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "mosquitto"
MOSQUITTO_EXE = MOSQUITTO_DIR / "mosquitto.exe"
MOSQUITTO_PASSWD = MOSQUITTO_DIR / "mosquitto_passwd.exe"
HOST = "127.0.0.1"
CANDIDATE_PORTS = (18893, 18894, 18895, 18896)
# Puertos que esta fase nunca usa (broker protegido y bases de datos).
FORBIDDEN_PORTS = frozenset({1883, 8883, 18883, 3306, 3307, 3317, 3318})
TOPIC_PATTERN = "agrotech/v1/gw/+/up"
TIMEOUT = 20.0
REPO_ROOT = Path(__file__).resolve().parents[4]


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _write_test_pki(directory: Path) -> tuple[Path, Path, Path]:
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agrotech-flepsi03-test-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address(HOST))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.crt"
    cert_path = directory / "server.crt"
    key_path = directory / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


@dataclass
class Broker:
    directory: Path
    port: int
    ca_certs: Path
    consumer_user: str
    consumer_password: str = field(repr=False)
    publisher_user: str = "flepsi03-publisher"
    publisher_password: str = field(default="", repr=False)
    process: subprocess.Popen | None = None
    version: str = ""

    @property
    def log_path(self) -> Path:
        return self.directory / "mosquitto.log"

    def log_lines(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)


def start_broker(run_dir: Path) -> Broker:
    if not MOSQUITTO_EXE.is_file() or not MOSQUITTO_PASSWD.is_file():
        raise RuntimeError("Mosquitto no está instalado en la ruta esperada")
    port = next((p for p in CANDIDATE_PORTS if p not in FORBIDDEN_PORTS and _port_is_free(p)), None)
    if port is None:
        raise RuntimeError("no hay puerto libre para el broker de la fase")
    broker_dir = run_dir / "broker"
    (broker_dir / "data").mkdir(parents=True)
    ca, cert, key = _write_test_pki(broker_dir)
    consumer_user = "flepsi03-consumer"
    consumer_password = secrets.token_urlsafe(24)
    publisher_password = secrets.token_urlsafe(24)
    passwd = broker_dir / "passwd"
    for index, (user, password) in enumerate(
        ((consumer_user, consumer_password), ("flepsi03-publisher", publisher_password))
    ):
        args = [str(MOSQUITTO_PASSWD), "-b", str(passwd), user, password]
        if index == 0:
            args.insert(1, "-c")
        subprocess.run(args, check=True, capture_output=True, timeout=30)  # noqa: S603
    acl = broker_dir / "acl"
    acl.write_text(
        f"user {consumer_user}\ntopic read {TOPIC_PATTERN}\n\n"
        f"user flepsi03-publisher\ntopic write {TOPIC_PATTERN}\n",
        encoding="ascii",
    )
    conf = broker_dir / "mosquitto.conf"
    conf.write_text(
        "\n".join(
            [
                "allow_anonymous false",
                f"password_file {_posix(passwd)}",
                f"acl_file {_posix(acl)}",
                "persistence true",
                f"persistence_location {_posix(broker_dir / 'data')}/",
                "autosave_interval 1",
                "max_queued_messages 1000",
                "log_dest file " + _posix(broker_dir / "mosquitto.log"),
                "log_type all",
                "log_timestamp true",
                "connection_messages true",
                f"listener {port} {HOST}",
                "protocol mqtt",
                f"cafile {_posix(ca)}",
                f"certfile {_posix(cert)}",
                f"keyfile {_posix(key)}",
                "tls_version tlsv1.2",
                "",
            ]
        ),
        encoding="ascii",
    )
    version = subprocess.run(  # noqa: S603
        [str(MOSQUITTO_EXE), "-h"], capture_output=True, text=True, timeout=30
    )
    version_line = next(
        (line for line in (version.stdout + version.stderr).splitlines() if "version" in line),
        "",
    )
    stderr = (broker_dir / "mosquitto.stderr").open("wb")
    process = subprocess.Popen(  # noqa: S603
        [str(MOSQUITTO_EXE), "-c", str(conf)],
        cwd=broker_dir,
        stdout=stderr,
        stderr=stderr,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    broker = Broker(
        directory=broker_dir,
        port=port,
        ca_certs=ca,
        consumer_user=consumer_user,
        consumer_password=consumer_password,
        publisher_password=publisher_password,
        process=process,
        version=version_line.strip(),
    )
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("el broker de la fase terminó al arrancar")
        try:
            with socket.create_connection((HOST, port), timeout=1):
                return broker
        except OSError:
            time.sleep(0.1)
    broker.stop()
    raise RuntimeError("el broker de la fase no abrió su puerto")


class Publisher:
    """Publicador de pruebas MQTT 5, QoS 1, TLS, credenciales de la fase."""

    def __init__(self, broker: Broker) -> None:
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"flepsi03-pub-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv5,
        )
        self.client.username_pw_set(broker.publisher_user, broker.publisher_password)
        self.client.tls_set(
            ca_certs=str(broker.ca_certs),
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.connect(HOST, broker.port, keepalive=15, clean_start=True)
        self.client.loop_start()
        self.published: list[int] = []

    def publish(self, topic: str, payload: bytes, user_properties=None) -> int:
        properties = None
        if user_properties is not None:
            properties = Properties(PacketTypes.PUBLISH)
            properties.UserProperty = list(user_properties)
        info = self.client.publish(topic, payload, qos=1, properties=properties)
        info.wait_for_publish(TIMEOUT)
        if not info.is_published():
            raise RuntimeError("el broker no confirmó la publicación QoS 1")
        self.published.append(info.mid)
        return info.mid

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


@dataclass
class Child:
    process: subprocess.Popen
    trace: Path
    log: Path
    stop_file: Path
    kill_done_file: Path

    def events(self) -> list[dict]:
        if not self.trace.exists():
            return []
        return [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def wait_for(self, predicate, timeout: float = TIMEOUT) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = self.events()
            if predicate(events):
                return events
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"condición no alcanzada; eventos={self.events()}")

    def kill(self) -> None:
        """Caída abrupta (TerminateProcess): sin PUBACK ni cierre ordenado."""
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=TIMEOUT)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.stop_file.write_text("stop", encoding="ascii")
            try:
                self.process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                self.kill()


def start_child(
    run_dir: Path,
    broker: Broker,
    *,
    name: str,
    client_id: str,
    mode: str,
    django_settings: dict[str, str],
    identity_mode: str = "off",
    identity_key: str = "",
    session_expiry_seconds: int = 600,
) -> Child:
    child_dir = run_dir / "children" / name
    child_dir.mkdir(parents=True)
    trace = child_dir / "trace.jsonl"
    log = child_dir / "consumer.log"
    stop_file = child_dir / "STOP"
    kill_done = child_dir / "KILL_DONE"
    config = {
        "host": HOST,
        "port": broker.port,
        "username": broker.consumer_user,
        "password": broker.consumer_password,
        "ca_certs": str(broker.ca_certs),
        "client_id": client_id,
        "mode": mode,
        "trace": str(trace),
        "log": str(log),
        "stop_file": str(stop_file),
        "kill_done_file": str(kill_done),
        "identity_mode": identity_mode,
        "identity_key": identity_key,
        "session_expiry_seconds": session_expiry_seconds,
        "django_settings": django_settings,
    }
    config_path = child_dir / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test_disposable_iot")
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-B",
            "-m",
            "apps.iot.tests.e2e_flepsi03.consumer_child",
            str(config_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=(child_dir / "stdout.txt").open("wb"),
        stderr=(child_dir / "stderr.txt").open("wb"),
    )
    return Child(
        process=process, trace=trace, log=log, stop_file=stop_file, kill_done_file=kill_done
    )


def new_run_dir() -> Path:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=PHASE_ROOT))


def export_evidence(run_dir: Path, destination: Path) -> None:
    """Copia evidencias sin secretos (trazas, logs) antes de borrar el directorio."""
    destination.mkdir(parents=True, exist_ok=True)
    for path in run_dir.rglob("*"):
        if path.name in {"trace.jsonl", "consumer.log", "stderr.txt", "mosquitto.log"}:
            relative = path.relative_to(run_dir)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def remove_run_dir(run_dir: Path) -> None:
    if run_dir.parent == PHASE_ROOT and run_dir.name.startswith("run-"):
        shutil.rmtree(run_dir, ignore_errors=True)
