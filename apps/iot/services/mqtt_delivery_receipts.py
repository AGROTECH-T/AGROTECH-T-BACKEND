"""Durable, idempotent receipt of MQTT transport envelopes (B2.1-03-01B)."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from enum import StrEnum

from django.db import DatabaseError, IntegrityError, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone

from apps.iot.models import (
    Coordinator,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryState,
)
from apps.iot.services.broker_identity import (
    PHASE_RECEIPT,
    observe_redelivery_safely,
    prepare_new_delivery_evidence,
    record_observation_failure,
)

logger = logging.getLogger(__name__)

# BUILD-FLEPSI-02L: MariaDB server error numbers relevant to the receipt race.
MARIADB_DUPLICATE_ENTRY = 1062
MARIADB_LOCK_WAIT_TIMEOUT = 1205
MARIADB_DEADLOCK = 1213
# Bounded retry, only for 1213 and only when this function owns the transaction.
DEADLOCK_MAX_ATTEMPTS = 3
DEADLOCK_RETRY_DELAYS = (0.02, 0.1)  # seconds, before retries 1 and 2


def mariadb_errno(error: BaseException) -> int | None:
    """Numeric MariaDB error code of a Django database error, never its text.

    Django re-raises driver errors as ``dj_exc(*driver_exc.args) from driver_exc``;
    PyMySQL puts the server errno in ``args[0]``. Returns ``None`` when absent.
    """
    for candidate in (error.__cause__, error):
        args = getattr(candidate, "args", ())
        if args and type(args[0]) is int:
            return args[0]
    return None


_TRANSPORT_KEY_DOMAIN = b"AGROTECH-T/IOT/MQTT-TRANSPORT\x00v1\x00"
_TOPIC_MAX_LENGTH = IotMqttDelivery._meta.get_field("topic").max_length
_KEY_VERSION_MAX_LENGTH = IotMqttDelivery._meta.get_field("payload_key_version").max_length


class MqttReceiptStatus(StrEnum):
    PERSISTED = "persisted"
    REDELIVERY = "redelivery"
    IDENTITY_INCONSISTENCY = "identity_inconsistency"
    PERSISTENCE_ERROR = "persistence_error"


@dataclass(frozen=True, slots=True)
class MqttReceiptResult:
    """Controlled outcome; persistence errors never masquerade as business results."""

    status: MqttReceiptStatus
    delivery: IotMqttDelivery | None = None
    error_code: str | None = None


def build_transport_key(*, topic: str, payload: bytes) -> bytes:
    """Hash the exact UTF-8 topic and payload using versioned, length-prefixed input."""
    if not isinstance(topic, str) or type(payload) is not bytes:
        raise ValueError("topic o payload no válido")
    try:
        topic_bytes = topic.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("topic no válido") from None

    if len(topic_bytes) >= 2**32 or len(payload) >= 2**64:
        raise ValueError("sobre MQTT demasiado grande")

    material = (
        _TRANSPORT_KEY_DOMAIN
        + len(topic_bytes).to_bytes(4, byteorder="big")
        + topic_bytes
        + len(payload).to_bytes(8, byteorder="big")
        + payload
    )
    return hashlib.sha256(material).digest()


def register_mqtt_delivery(
    *,
    topic: str,
    payload: bytes,
    qos: int,
    payload_ciphertext: bytes,
    payload_nonce: bytes,
    payload_tag: bytes,
    payload_key_version: str,
    coordinator_id: int | None = None,
    max_payload_bytes: int,
    broker_identity=None,
) -> MqttReceiptResult:
    """Persist a new transport envelope and its first event atomically.

    BUILD-FLEPSI-02M (M-02): must be called *outside* any transaction. The
    service owns its transactions (one per attempt) so it can roll back fully
    after a UNIQUE collision or a 1213 before retrying; inside a caller's
    transaction that is impossible, so the call is rejected with
    ``TransactionManagementError`` before any validation, query or write.

    ``broker_identity`` (BUILD-FLEPSI-02, modo record) is an in-memory
    ``BrokerIdentityObservation``: its evidence is stored in the same INSERT,
    and on redelivery the existing row is locked *before* any event insert so
    identity anomalies are recorded idempotently. It never changes the
    transport key, the AAD or the PERSISTED/REDELIVERY outcome.

    ``payload`` is used only in memory to derive transport identity, size and
    SHA-256. Only ``payload_ciphertext`` is stored. This service deliberately
    has no encryption/key-management code; the caller must provide a valid
    AES-GCM envelope from an authorized future integration layer. No MQTT
    packet identifier is accepted or used.
    """
    if transaction.get_connection().in_atomic_block:
        raise TransactionManagementError(
            "register_mqtt_delivery no admite una transacción externa: "
            "gestiona sus propias transacciones"
        )
    _validate_inputs(
        topic=topic,
        payload=payload,
        qos=qos,
        payload_ciphertext=payload_ciphertext,
        payload_nonce=payload_nonce,
        payload_tag=payload_tag,
        payload_key_version=payload_key_version,
        coordinator_id=coordinator_id,
        max_payload_bytes=max_payload_bytes,
    )

    transport_key = build_transport_key(topic=topic, payload=payload)
    payload_sha256 = hashlib.sha256(payload).digest()
    # Receipt storage accepts only an envelope whose GCM tag authenticates the
    # exact topic/payload transport identity. Plaintext remains memory-only.
    from apps.iot.services.mqtt_payload_crypto import decrypt_mqtt_payload

    verified = decrypt_mqtt_payload(
        ciphertext=payload_ciphertext,
        nonce=payload_nonce,
        tag=payload_tag,
        key_version=payload_key_version,
        transport_key=transport_key,
    )
    if verified != payload:
        raise ValueError("sobre cifrado no corresponde al payload recibido")
    # BUILD-FLEPSI-02H: pure evidence computation before the transaction;
    # a non-database failure yields NULL evidence plus a diagnostic event.
    evidence, observation_failure = prepare_new_delivery_evidence(broker_identity)

    new_delivery = {
        "transport_key": transport_key,
        "topic": topic,
        "qos": qos,
        "payload_ciphertext": payload_ciphertext,
        "payload_nonce": payload_nonce,
        "payload_tag": payload_tag,
        "payload_key_version": payload_key_version,
        "payload_sha256": payload_sha256,
        "payload_size": len(payload),
        "coordinator_id": coordinator_id,
        **evidence,
    }
    # BUILD-FLEPSI-02L: each attempt is its own transaction (no caller
    # transaction is possible, see the guard above).
    collision: IntegrityError | None = None
    deadlocks = 0
    while True:
        try:
            return _register_attempt(
                new_delivery=new_delivery,
                observation_failure=observation_failure,
                broker_identity=broker_identity,
                collision=collision,
            )
        except _UniqueCollision as lost:
            # The losing transaction is fully rolled back, releasing the shared
            # lock taken by the duplicate-key check; recover in a new one.
            collision = lost.error
        except DatabaseError as error:
            code = mariadb_errno(error)
            if code == MARIADB_DEADLOCK and deadlocks + 1 < DEADLOCK_MAX_ATTEMPTS:
                deadlocks += 1
                logger.warning(
                    "recepción MQTT: interbloqueo errno=%s; reintento %s/%s en transacción nueva",
                    code,
                    deadlocks,
                    DEADLOCK_MAX_ATTEMPTS - 1,
                )
                _deadlock_pause(deadlocks)
                continue
            # Do not include driver text, SQL, payload or credentials in results.
            logger.error(
                "recepción MQTT no durable: %s errno=%s",
                type(error).__name__,
                code,
            )
            return MqttReceiptResult(
                status=MqttReceiptStatus.PERSISTENCE_ERROR,
                error_code=type(error).__name__,
            )


class _UniqueCollision(Exception):  # noqa: N818 - control flow, not an error
    """Lost the ``transport_key`` race; raised *out of* the transaction."""

    def __init__(self, error: IntegrityError) -> None:
        super().__init__("transport_key collision")
        self.error = error


def _deadlock_pause(retry: int) -> None:
    time.sleep(DEADLOCK_RETRY_DELAYS[min(retry, len(DEADLOCK_RETRY_DELAYS)) - 1])


def _find_existing(transport_key: bytes, *, lock: bool) -> IotMqttDelivery | None:
    lookup = IotMqttDelivery.objects
    if lock:
        lookup = lookup.select_for_update()
    return lookup.filter(transport_key=transport_key).first()


def _register_attempt(
    *,
    new_delivery: dict,
    observation_failure,
    broker_identity,
    collision: IntegrityError | None,
) -> MqttReceiptResult:
    """One transactional attempt; commits before returning a durable result."""
    transport_key = new_delivery["transport_key"]
    coordinator_id = new_delivery["coordinator_id"]
    classify = {
        "topic": new_delivery["topic"],
        "qos": new_delivery["qos"],
        "payload_size": new_delivery["payload_size"],
        "payload_sha256": new_delivery["payload_sha256"],
        "coordinator_id": coordinator_id,
    }
    with transaction.atomic():
        if (
            coordinator_id is not None
            and not Coordinator.objects.filter(pk=coordinator_id).exists()
        ):
            raise ValueError("coordinador candidato no encontrado")

        existing = _find_existing(transport_key, lock=broker_identity is not None)
        if existing is not None:
            return _observe_identity(_classify_existing(existing, **classify), broker_identity)
        if collision is not None:
            # 1062 was reported but the winning row is not visible: another
            # integrity failure, never retried as a redelivery.
            raise collision

        try:
            # The savepoint lets the outer transaction remain usable after
            # a concurrent UNIQUE collision or another integrity failure.
            with transaction.atomic():
                delivery = IotMqttDelivery.objects.create(
                    received_at=timezone.now(),
                    state=IotMqttDeliveryState.RECEIVED,
                    **new_delivery,
                )
                IotMqttDeliveryEvent.objects.create(
                    delivery=delivery,
                    event_type="RECEIVED",
                    to_state=IotMqttDeliveryState.RECEIVED,
                    correlation_id=delivery.id,
                    safe_detail="Recepción durable registrada",
                )
                if observation_failure is not None:
                    record_observation_failure(delivery, observation_failure, phase=PHASE_RECEIPT)
        except IntegrityError as error:
            if mariadb_errno(error) != MARIADB_DUPLICATE_ENTRY:
                raise
            # Leave this transaction (rollback) before locking the winner:
            # locking it here would upgrade the duplicate-check S lock to X
            # and deadlock (1213) against other losers holding S.
            raise _UniqueCollision(error) from error

    return MqttReceiptResult(
        status=MqttReceiptStatus.PERSISTED,
        delivery=delivery,
    )


def _observe_identity(result: MqttReceiptResult, broker_identity) -> MqttReceiptResult:
    """Record identity anomalies of a redelivery; never alters ``result``."""
    if broker_identity is not None and result.status is MqttReceiptStatus.REDELIVERY:
        observe_redelivery_safely(result.delivery, broker_identity)
    return result


def _classify_existing(
    delivery: IotMqttDelivery,
    *,
    topic: str,
    qos: int,
    payload_size: int,
    payload_sha256: bytes,
    coordinator_id: int | None,
) -> MqttReceiptResult:
    """Compare stable transport metadata; candidate coordinator is only a hint."""
    consistent = (
        delivery.topic == topic
        and delivery.qos == qos
        and delivery.payload_size == payload_size
        and delivery.payload_sha256 == payload_sha256
    )
    if not consistent:
        IotMqttDeliveryEvent.objects.create(
            delivery=delivery,
            event_type="IDENTITY_INCONSISTENCY",
            from_state=delivery.state,
            to_state=delivery.state,
            correlation_id=delivery.id,
            safe_detail="Metadatos de transporte inconsistentes en una redelivery",
        )
        return MqttReceiptResult(
            status=MqttReceiptStatus.IDENTITY_INCONSISTENCY,
            delivery=delivery,
            error_code="TRANSPORT_KEY_METADATA_MISMATCH",
        )
    IotMqttDeliveryEvent.objects.create(
        delivery=delivery,
        event_type="REDELIVERY",
        from_state=delivery.state,
        to_state=delivery.state,
        correlation_id=delivery.id,
        safe_detail="Redelivery MQTT asociada a una recepción durable existente",
    )
    return MqttReceiptResult(
        status=MqttReceiptStatus.REDELIVERY,
        delivery=delivery,
    )


def _validate_inputs(
    *,
    topic: str,
    payload: bytes,
    qos: int,
    payload_ciphertext: bytes,
    payload_nonce: bytes,
    payload_tag: bytes,
    payload_key_version: str,
    coordinator_id: int | None,
    max_payload_bytes: int,
) -> None:
    """Reject invalid or oversized values before any database write."""
    if not isinstance(topic, str) or not topic or "\x00" in topic:
        raise ValueError("topic MQTT no válido")
    try:
        topic.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("topic MQTT no válido") from None
    if len(topic) > _TOPIC_MAX_LENGTH:
        raise ValueError("topic MQTT supera el límite permitido")

    if type(payload) is not bytes:
        raise ValueError("payload MQTT debe ser bytes")
    if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
        raise ValueError("límite de payload no válido")
    if len(payload) > max_payload_bytes:
        raise ValueError("payload MQTT supera el límite configurado")

    if type(qos) is not int or qos != 1:
        raise ValueError("solo se admite QoS 1")
    if type(payload_ciphertext) is not bytes:
        raise ValueError("payload cifrado debe ser bytes")
    if len(payload_ciphertext) != len(payload):
        raise ValueError("longitud del payload cifrado no coincide")
    if payload and payload_ciphertext == payload:
        raise ValueError("se requiere ciphertext, no payload en claro")
    if type(payload_nonce) is not bytes or len(payload_nonce) != 12:
        raise ValueError("nonce AES-GCM no válido")
    if type(payload_tag) is not bytes or len(payload_tag) != 16:
        raise ValueError("tag AES-GCM no válido")
    if (
        not isinstance(payload_key_version, str)
        or not payload_key_version
        or len(payload_key_version) > _KEY_VERSION_MAX_LENGTH
    ):
        raise ValueError("versión de clave no válida")
    if coordinator_id is not None and (type(coordinator_id) is not int or coordinator_id <= 0):
        raise ValueError("coordinador candidato no válido")


__all__ = [
    "MqttReceiptResult",
    "MqttReceiptStatus",
    "build_transport_key",
    "register_mqtt_delivery",
]
