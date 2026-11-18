"""Versioned AES-256-GCM protection for MQTT payloads (IoT B2.1-03)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.exceptions import ImproperlyConfigured

from config.coordinator_key_encryption_key import (
    MASTER_KEY_VERSION_MAX,
    UnsupportedMasterKeyVersion,
    load_master_key_ring,
)

_KEY_INFO = b"agrotech-t/iot/mqtt-payload/aes-256-gcm/v1"
_AAD_DOMAIN = b"agrotech-t/iot/mqtt-delivery/aes-256-gcm/v1\x00"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class EncryptedMqttPayload:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    tag: bytes = field(repr=False)
    key_version: str


class MqttPayloadCryptoError(ValueError):
    """Safe failure for invalid or unauthentic MQTT payload envelopes."""


class MqttPayloadKeyUnavailable(MqttPayloadCryptoError):
    """The key version required by a retained delivery is not configured."""


def encrypt_mqtt_payload(*, payload: bytes, transport_key: bytes) -> EncryptedMqttPayload:
    """Encrypt payload using a purpose-derived key from the configured key ring."""
    _validate_payload_and_transport_key(payload=payload, transport_key=transport_key)
    try:
        key_ring = load_master_key_ring()
    except ImproperlyConfigured:
        raise MqttPayloadKeyUnavailable("la clave activa de payload MQTT no está configurada") from None
    version = key_ring.current_version
    key = _derive_payload_key(key_ring.key_for(version))
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, payload, _aad(transport_key))
    return EncryptedMqttPayload(
        ciphertext=sealed[:-_TAG_BYTES],
        nonce=nonce,
        tag=sealed[-_TAG_BYTES:],
        key_version=str(version),
    )


def decrypt_mqtt_payload(
    *,
    ciphertext: bytes,
    nonce: bytes,
    tag: bytes,
    key_version: str,
    transport_key: bytes,
) -> bytes:
    """Authenticate the GCM tag before returning any plaintext bytes."""
    if type(ciphertext) is not bytes:
        raise MqttPayloadCryptoError("ciphertext MQTT no válido")
    if type(nonce) is not bytes or len(nonce) != _NONCE_BYTES:
        raise MqttPayloadCryptoError("nonce MQTT no válido")
    if type(tag) is not bytes or len(tag) != _TAG_BYTES:
        raise MqttPayloadCryptoError("tag MQTT no válido")
    if not isinstance(key_version, str) or not key_version.isascii() or not key_version.isdigit():
        raise MqttPayloadCryptoError("versión de clave MQTT no válida")
    version = int(key_version)
    if not 1 <= version <= MASTER_KEY_VERSION_MAX or str(version) != key_version:
        raise MqttPayloadCryptoError("versión de clave MQTT no válida")
    if type(transport_key) is not bytes or len(transport_key) != 32:
        raise MqttPayloadCryptoError("identidad de transporte MQTT no válida")

    try:
        master_key = load_master_key_ring().key_for(version)
    except (UnsupportedMasterKeyVersion, ImproperlyConfigured):
        raise MqttPayloadKeyUnavailable(
            "la versión de clave MQTT requerida no está disponible"
        ) from None
    key = _derive_payload_key(master_key)
    try:
        return AESGCM(key).decrypt(
            nonce,
            ciphertext + tag,
            _aad(transport_key),
        )
    except InvalidTag:
        raise MqttPayloadCryptoError("no se pudo autenticar el payload MQTT") from None


def register_encrypted_mqtt_delivery(
    *,
    topic: str,
    payload: bytes,
    qos: int,
    max_payload_bytes: int,
    coordinator_id: int | None = None,
    broker_identity=None,
):
    """Encrypt before delegating to the durable receipt service.

    ``broker_identity`` (BUILD-FLEPSI-02) is passed through unchanged; it does
    not take part in the transport key or the AAD.
    """
    from apps.iot.services.mqtt_delivery_receipts import (
        build_transport_key,
        register_mqtt_delivery,
    )

    transport_key = build_transport_key(topic=topic, payload=payload)
    envelope = encrypt_mqtt_payload(payload=payload, transport_key=transport_key)
    return register_mqtt_delivery(
        topic=topic,
        payload=payload,
        qos=qos,
        payload_ciphertext=envelope.ciphertext,
        payload_nonce=envelope.nonce,
        payload_tag=envelope.tag,
        payload_key_version=envelope.key_version,
        coordinator_id=coordinator_id,
        max_payload_bytes=max_payload_bytes,
        broker_identity=broker_identity,
    )


def decrypt_mqtt_delivery(delivery) -> bytes:
    """Decrypt a durable row only after its GCM tag and transport AAD verify."""
    return decrypt_mqtt_payload(
        ciphertext=bytes(delivery.payload_ciphertext),
        nonce=bytes(delivery.payload_nonce),
        tag=bytes(delivery.payload_tag),
        key_version=delivery.payload_key_version,
        transport_key=bytes(delivery.transport_key),
    )


def _derive_payload_key(master_key: bytes) -> bytes:
    return HKDF(
        algorithm=SHA256(),
        length=_KEY_BYTES,
        salt=None,
        info=_KEY_INFO,
    ).derive(master_key)


def _aad(transport_key: bytes) -> bytes:
    return _AAD_DOMAIN + transport_key


def _validate_payload_and_transport_key(*, payload: bytes, transport_key: bytes) -> None:
    if type(payload) is not bytes:
        raise MqttPayloadCryptoError("payload MQTT no válido")
    if type(transport_key) is not bytes or len(transport_key) != 32:
        raise MqttPayloadCryptoError("identidad de transporte MQTT no válida")


__all__ = [
    "EncryptedMqttPayload",
    "MqttPayloadCryptoError",
    "MqttPayloadKeyUnavailable",
    "decrypt_mqtt_payload",
    "decrypt_mqtt_delivery",
    "encrypt_mqtt_payload",
    "register_encrypted_mqtt_delivery",
]
