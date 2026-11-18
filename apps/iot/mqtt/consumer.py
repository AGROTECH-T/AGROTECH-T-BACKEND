"""MQTT QoS 1 receiver with durable encrypted receipt before manual PUBACK.

No se conecta al importar el módulo: la conexión la inicia el comando de
gestión ``mqtt_consumer``.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from django.conf import settings as django_settings
from django.db import DatabaseError, close_old_connections, connection
from django.db.models import F
from django.utils import timezone
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from apps.iot import lora_frame
from apps.iot.broker_identity_config import BrokerIdentityConfig, require_operational_config
from apps.iot.models import ACTIVE_PAIR, CoordinatorGateway
from apps.iot.mqtt.broker_identity import (
    BrokerIdentityObservation,
    BrokerIdentityObservationFailure,
)
from apps.iot.mqtt.payload import (
    MAX_MESSAGE_BYTES,
    PayloadError,
    TopicError,
    parse_payload,
    parse_topic,
)
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptResult, MqttReceiptStatus
from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery

logger = logging.getLogger(__name__)

TOPIC_FILTER = "agrotech/v1/gw/+/up"
QOS = 1
# BUILD-FLEPSI-02: MQTT 5 (clean_start=False + SessionExpiryInterval).
MQTT_PROTOCOL = mqtt.MQTTv5

ENV_HOST = "MQTT_HOST"
ENV_PORT = "MQTT_PORT"
ENV_USERNAME = "MQTT_USERNAME"
ENV_PASSWORD = "MQTT_PASSWORD"  # noqa: S105
ENV_CLIENT_ID = "MQTT_CLIENT_ID"
ENV_TLS = "MQTT_TLS"
ENV_CA_CERTS = "MQTT_CA_CERTS"
ENV_TLS_INSECURE = "MQTT_TLS_INSECURE"
ENV_KEEPALIVE = "MQTT_KEEPALIVE"
ENV_RECONNECT_MIN_DELAY = "MQTT_RECONNECT_MIN_DELAY"
ENV_RECONNECT_MAX_DELAY = "MQTT_RECONNECT_MAX_DELAY"
ENV_MAX_PERSISTENCE_FAILURES = "MQTT_MAX_PERSISTENCE_FAILURES"
ENV_MAX_CONNECT_FAILURES = "MQTT_MAX_CONNECT_FAILURES"
ENV_SESSION_EXPIRY = "MQTT_SESSION_EXPIRY_SECONDS"

DEFAULT_PORT = 1883
DEFAULT_KEEPALIVE = 60
DEFAULT_RECONNECT_MIN_DELAY = 1
DEFAULT_RECONNECT_MAX_DELAY = 60
DEFAULT_MAX_PERSISTENCE_FAILURES = 5
DEFAULT_MAX_CONNECT_FAILURES = 20
# Must be > 0: with 0 an MQTT 5 session ends on disconnect and queued QoS 1
# messages are lost. Upper bound is the MQTT 5 maximum (0xFFFFFFFF).
DEFAULT_SESSION_EXPIRY_SECONDS = 86_400
MIN_SESSION_EXPIRY_SECONDS = 60
MAX_SESSION_EXPIRY_SECONDS = 0xFFFFFFFF

HALT_PERSISTENCE = "persistence"
HALT_CONNECTION = "connection"
HALT_STOP = "stop"


class MqttConfigurationError(ValueError):
    """Configuración de entorno inválida; nunca incluye secretos."""


@dataclass(frozen=True, slots=True)
class MqttSettings:
    host: str
    port: int
    client_id: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    tls: bool = False
    ca_certs: str | None = None
    tls_insecure: bool = False
    keepalive: int = DEFAULT_KEEPALIVE
    reconnect_min_delay: int = DEFAULT_RECONNECT_MIN_DELAY
    reconnect_max_delay: int = DEFAULT_RECONNECT_MAX_DELAY
    max_persistence_failures: int = DEFAULT_MAX_PERSISTENCE_FAILURES
    max_connect_failures: int = DEFAULT_MAX_CONNECT_FAILURES
    session_expiry_seconds: int = DEFAULT_SESSION_EXPIRY_SECONDS

    @classmethod
    def from_env(
        cls, environ: dict[str, str] | None = None, *, production: bool | None = None
    ) -> MqttSettings:
        """Read and validate the environment.

        ``production`` defaults to ``not settings.DEBUG``. In production TLS is
        mandatory, certificate verification cannot be disabled and a custom CA
        bundle must exist.
        """
        values = os.environ if environ is None else environ
        if production is None:
            production = not django_settings.DEBUG
        host = _required(values, ENV_HOST)
        client_id = _required(values, ENV_CLIENT_ID)
        username = _optional(values, ENV_USERNAME)
        password = _optional(values, ENV_PASSWORD, strip=False)
        if password is not None and username is None:
            raise MqttConfigurationError(f"{ENV_PASSWORD} requiere {ENV_USERNAME}.")
        tls = _env_bool(values, ENV_TLS, False)
        ca_certs = _optional(values, ENV_CA_CERTS)
        if ca_certs is not None and not tls:
            raise MqttConfigurationError(f"{ENV_CA_CERTS} requiere {ENV_TLS}=true.")
        tls_insecure = _env_bool(values, ENV_TLS_INSECURE, False)
        if tls_insecure and not tls:
            raise MqttConfigurationError(f"{ENV_TLS_INSECURE} requiere {ENV_TLS}=true.")
        if production:
            if not tls:
                raise MqttConfigurationError(f"{ENV_TLS}=true es obligatoria en producción.")
            if tls_insecure:
                raise MqttConfigurationError(
                    f"{ENV_TLS_INSECURE} no está permitida en producción."
                )
            if ca_certs is not None and not Path(ca_certs).is_file():
                raise MqttConfigurationError(f"{ENV_CA_CERTS} no apunta a un archivo existente.")
        reconnect_min_delay = _env_int(
            values, ENV_RECONNECT_MIN_DELAY, DEFAULT_RECONNECT_MIN_DELAY, 1, 3600
        )
        reconnect_max_delay = _env_int(
            values, ENV_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY, 1, 3600
        )
        if reconnect_max_delay < reconnect_min_delay:
            raise MqttConfigurationError(
                f"{ENV_RECONNECT_MAX_DELAY} no puede ser menor que {ENV_RECONNECT_MIN_DELAY}."
            )
        return cls(
            host=host,
            port=_env_int(values, ENV_PORT, DEFAULT_PORT, 1, 65535),
            client_id=client_id,
            username=username,
            password=password,
            tls=tls,
            ca_certs=ca_certs,
            tls_insecure=tls_insecure,
            keepalive=_env_int(values, ENV_KEEPALIVE, DEFAULT_KEEPALIVE, 5, 3600),
            reconnect_min_delay=reconnect_min_delay,
            reconnect_max_delay=reconnect_max_delay,
            max_persistence_failures=_env_int(
                values, ENV_MAX_PERSISTENCE_FAILURES, DEFAULT_MAX_PERSISTENCE_FAILURES, 1, 100
            ),
            max_connect_failures=_env_int(
                values, ENV_MAX_CONNECT_FAILURES, DEFAULT_MAX_CONNECT_FAILURES, 1, 1000
            ),
            session_expiry_seconds=_env_int(
                values,
                ENV_SESSION_EXPIRY,
                DEFAULT_SESSION_EXPIRY_SECONDS,
                MIN_SESSION_EXPIRY_SECONDS,
                MAX_SESSION_EXPIRY_SECONDS,
            ),
        )


class MqttConsumer:
    """Persist each encrypted receipt before acknowledging QoS 1."""

    def __init__(
        self,
        settings: MqttSettings,
        *,
        client=None,
        receipt_func: Callable[..., MqttReceiptResult] = register_encrypted_mqtt_delivery,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        persistence_probe: Callable[[], None] | None = None,
        broker_identity: BrokerIdentityConfig | None = None,
    ) -> None:
        self.settings = settings
        # None or mode off: MQTT 5 user properties are not captured at all.
        # BUILD-FLEPSI-02H: a received config object is re-validated, so a
        # hand-built ENFORCE config fails explicitly (never degrades to off).
        if broker_identity is not None:
            require_operational_config(broker_identity)
        self._identity = broker_identity if broker_identity and broker_identity.records else None
        self._register = receipt_func
        self._clock = clock or timezone.now
        self._sleep = sleep
        self._probe = persistence_probe or _probe_persistence
        self._lock = threading.Lock()
        self._halt: str | None = None
        self._stop_requested = False
        self._persistence_failures = 0
        self._connect_failures = 0
        self._client = client if client is not None else _build_client(settings)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def run(self) -> None:
        """Run until stopped; raise ``MqttConsumerFatalError`` on any abnormal end.

        Paho's ``loop_forever`` returns normally after ``disconnect()``. The
        consumer disconnects on purpose when durable persistence fails (no
        PUBACK, the persistent session keeps the message), so a normal return
        is only a clean exit when ``stop()`` was requested. Otherwise:

        - persistence failure: wait with backoff, probe MariaDB and reconnect;
          after ``max_persistence_failures`` consecutive failures, fatal;
        - connection failures above ``max_connect_failures``: fatal;
        - any other return: fatal.

        The management command turns the fatal error into a non-zero exit so
        the platform sees the failure and restarts the process.
        """
        try:
            while True:
                if self._stop_requested:
                    logger.info("consumidor MQTT detenido")
                    return
                self._halt = None
                logger.info(
                    "consumidor MQTT conectando a %s:%s", self.settings.host, self.settings.port
                )
                self._client.connect_async(
                    self.settings.host,
                    self.settings.port,
                    self.settings.keepalive,
                    clean_start=False,
                    properties=_connect_properties(self.settings),
                )
                try:
                    self._client.loop_forever(retry_first_connection=True)
                except KeyboardInterrupt:
                    self._stop_requested = True
                if self._stop_requested:
                    logger.info("consumidor MQTT detenido")
                    return
                if self._halt == HALT_PERSISTENCE:
                    self._wait_for_persistence()
                    continue
                if self._halt == HALT_CONNECTION:
                    raise MqttConsumerFatalError(
                        f"conexión MQTT fallida {self._connect_failures} veces consecutivas"
                    )
                raise MqttConsumerFatalError("el bucle MQTT terminó sin una detención solicitada")
        finally:
            self._safe_disconnect()

    def stop(self) -> None:
        """Request a clean stop (also used by the SIGTERM/SIGINT handler).

        No new message is persisted or acknowledged after this point; a
        message already being persisted finishes its transaction. Anything not
        acknowledged is redelivered by the persistent session.
        """
        self._stop_requested = True
        if self._halt is None:
            self._halt = HALT_STOP
        self._client.disconnect()

    def _wait_for_persistence(self) -> None:
        """Bounded, logged backoff until MariaDB answers again."""
        maximum = self.settings.max_persistence_failures
        while True:
            if self._persistence_failures >= maximum:
                raise MqttConsumerFatalError(
                    f"persistencia durable fallida {self._persistence_failures} veces consecutivas"
                )
            exponent = min(max(self._persistence_failures - 1, 0), 16)
            delay = min(
                self.settings.reconnect_max_delay,
                self.settings.reconnect_min_delay * (2**exponent),
            )
            logger.warning(
                "consumidor MQTT en espera %ss por fallo de persistencia (%s/%s)",
                delay,
                self._persistence_failures,
                maximum,
            )
            self._sleep(delay)
            if self._stop_requested:
                return
            try:
                self._probe()
            except Exception as exc:
                self._persistence_failures += 1
                logger.error(
                    "la persistencia durable sigue sin responder (%s, %s/%s)",
                    type(exc).__name__,
                    self._persistence_failures,
                    maximum,
                )
                continue
            logger.info("persistencia durable disponible; reconectando MQTT")
            return

    def _safe_disconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception as exc:
            logger.warning("desconexión MQTT final fallida (%s)", type(exc).__name__)

    def handle_message(
        self, topic: object, raw_payload: object, *, ack, broker_identity=None
    ) -> MqttReceiptResult | None:
        """ACK only after MariaDB reports a durable insert or redelivery."""
        try:
            result = self.receive(topic, raw_payload, broker_identity=broker_identity)
            if type(result) is not MqttReceiptResult or type(result.status) is not MqttReceiptStatus:
                raise ValueError("servicio de recepción durable inválido")
        except Exception as exc:
            logger.error("falló la recepción durable MQTT (%s)", type(exc).__name__)
            return None
        if result.status not in {MqttReceiptStatus.PERSISTED, MqttReceiptStatus.REDELIVERY}:
            logger.error("recepción MQTT no durable (%s); mensaje sin ACK", result.status.value)
            return result
        try:
            ack()
        except Exception as exc:
            logger.error("falló PUBACK manual MQTT (%s)", type(exc).__name__)
            return None
        return result

    def receive(
        self, topic: object, raw_payload: object, *, broker_identity=None
    ) -> MqttReceiptResult:
        """Validate the envelope, encrypt it, and persist before returning."""
        close_old_connections()
        try:
            if (
                not isinstance(raw_payload, (bytes, bytearray))
                or len(raw_payload) > MAX_MESSAGE_BYTES
            ):
                raise ValueError("payload MQTT supera el tamaño admitido")
            payload = bytes(raw_payload)
            # Parsing here only supplies a scheduling candidate. Invalid
            # envelopes are still stored durably and rejected by ingestion.
            candidate_id = _coordinator_candidate(topic, payload)
            extra = {} if broker_identity is None else {"broker_identity": broker_identity}
            return self._register(
                topic=topic,
                payload=payload,
                qos=QOS,
                coordinator_id=candidate_id,
                max_payload_bytes=MAX_MESSAGE_BYTES,
                **extra,
            )
        finally:
            close_old_connections()

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        if reason_code.is_failure:
            logger.error("conexión MQTT rechazada: %s", reason_code)
            self._register_connect_failure(client)
            return
        self._connect_failures = 0
        session_present = bool(getattr(connect_flags, "session_present", False))
        logger.info("consumidor MQTT conectado (session_present=%s)", session_present)
        result, _mid = client.subscribe(TOPIC_FILTER, qos=QOS)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.error("suscripción MQTT fallida: %s", result)

    def _on_connect_fail(self, client, userdata) -> None:
        logger.warning("intento de conexión MQTT fallido")
        self._register_connect_failure(client)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        if self._halt is not None or self._stop_requested:
            return
        if getattr(reason_code, "is_failure", False):
            logger.warning("desconexión MQTT inesperada: %s", reason_code)
            self._register_connect_failure(client)

    def _register_connect_failure(self, client) -> None:
        self._connect_failures += 1
        maximum = self.settings.max_connect_failures
        logger.warning(
            "fallos de conexión MQTT consecutivos: %s/%s", self._connect_failures, maximum
        )
        if self._connect_failures >= maximum and self._halt is None:
            self._halt = HALT_CONNECTION
            logger.error("límite de reconexión MQTT alcanzado; el proceso terminará con error")
            client.disconnect()

    def _on_message(self, client, userdata, message) -> None:
        with self._lock:
            if self._halt is not None:
                # Already stopping: neither persist nor ACK. The persistent
                # session redelivers it after reconnection.
                return
            result = self.handle_message(
                message.topic,
                message.payload,
                ack=lambda: self._ack(message),
                broker_identity=self._observe(message),
            )
            if result is not None and result.status in {
                MqttReceiptStatus.PERSISTED,
                MqttReceiptStatus.REDELIVERY,
            }:
                self._persistence_failures = 0
                return
            # MQTT 5 has Receive Maximum, but this consumer does not set it
            # explicitly (CONNECT only carries SessionExpiryInterval), so it
            # cannot throttle inbound delivery that way. Stop reading when
            # durable responsibility could not be recorded; the persistent
            # session will redeliver later.
            self._persistence_failures += 1
            self._halt = HALT_PERSISTENCE
            logger.error(
                "recepción durable fallida (%s/%s consecutivas); desconexión sin PUBACK",
                self._persistence_failures,
                self.settings.max_persistence_failures,
            )
            client.disconnect()

    def _observe(self, message) -> BrokerIdentityObservation | None:
        """Classify MQTT 5 UserProperty as an ordered list (never ``dict``)."""
        if self._identity is None:
            return None
        try:
            properties = getattr(message, "properties", None)
            user_properties = getattr(properties, "UserProperty", None) if properties else None
            return BrokerIdentityObservation.from_user_properties(
                user_properties, self._identity.keyring
            )
        except Exception as exc:  # noqa: BLE001 - frontera de observación (código puro)
            # BUILD-FLEPSI-02H: never blocks the durable receipt or its ACK.
            logger.error("clasificación de identidad fallida (%s)", type(exc).__name__)
            return BrokerIdentityObservationFailure("classify", type(exc).__name__)

    def _ack(self, message) -> None:
        result = self._client.ack(message.mid, message.qos)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise MqttAckError(f"PUBACK fallido para mid={message.mid}: {result}")


class MqttAckError(RuntimeError):
    """El cliente no aceptó el envío del PUBACK; no se marca como confirmado."""


class MqttConsumerFatalError(RuntimeError):
    """El consumidor no puede continuar; el proceso debe terminar con error."""


def _probe_persistence() -> None:
    """Cheap MariaDB liveness check before resubscribing after a failure."""
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    finally:
        close_old_connections()


def _coordinator_candidate(topic: object, raw_payload: bytes) -> int | None:
    """Resolve an authorized same-farm scheduling hint; never an identity claim."""
    try:
        gateway_uid = parse_topic(topic)
        validated = parse_payload(raw_payload)
        frame = lora_frame.parse_lora_frame(validated.frame)
    except (TopicError, PayloadError, lora_frame.LoraFrameError):
        return None
    try:
        return (
            CoordinatorGateway.objects.filter(
                gateway__uid=gateway_uid,
                coordinator__uid=frame.coordinator_uid,
                gateway__farm_id=F("farm_id"),
                coordinator__farm_id=F("farm_id"),
                active_pair=ACTIVE_PAIR,
            )
            .values_list("coordinator_id", flat=True)
            .first()
        )
    except DatabaseError:
        # The receipt itself remains the durability boundary; candidate lookup
        # is advisory and must not prevent isolated receipt storage.
        return None


def _connect_properties(settings: MqttSettings) -> Properties:
    properties = Properties(PacketTypes.CONNECT)
    properties.SessionExpiryInterval = settings.session_expiry_seconds
    return properties


def _build_client(settings: MqttSettings):
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        protocol=MQTT_PROTOCOL,
        reconnect_on_failure=True,
        manual_ack=True,
    )
    client.reconnect_delay_set(
        min_delay=settings.reconnect_min_delay,
        max_delay=settings.reconnect_max_delay,
    )
    if settings.username is not None:
        client.username_pw_set(settings.username, settings.password)
    if settings.tls:
        client.tls_set(
            ca_certs=settings.ca_certs,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        if settings.tls_insecure:
            # Only reachable outside production (from_env rejects it there).
            client.tls_insecure_set(True)
    return client


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MqttConfigurationError(f"{name} es obligatoria.")
    return value.strip()


def _optional(values: dict[str, str], name: str, *, strip: bool = True) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if strip:
        return value.strip() or None
    return value or None


def _env_bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MqttConfigurationError(f"{name} debe ser un booleano.")


def _env_int(values: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError:
        raise MqttConfigurationError(f"{name} debe ser un entero.") from None
    if not minimum <= parsed <= maximum:
        raise MqttConfigurationError(f"{name} debe estar entre {minimum} y {maximum}.")
    return parsed


__all__ = [
    "MqttConfigurationError",
    "MqttConsumerFatalError",
    "MqttAckError",
    "MqttConsumer",
    "MqttSettings",
    "QOS",
    "TOPIC_FILTER",
]
