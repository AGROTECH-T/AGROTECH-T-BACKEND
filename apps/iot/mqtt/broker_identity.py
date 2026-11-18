"""Clasificación y huella de la identidad del broker MQTT 5 (BUILD-FLEPSI-02).

Puro: sin ORM, red ni configuración global. Reglas (FLEPSI-02C/F):

- ``UserProperty`` se trata como **lista** de pares; nunca ``dict()``.
- Más de 32 pares -> ``oversized``.
- Nombre exacto ``token_id`` (sensible a mayúsculas):
  ninguno -> ``absent``; dos o más, o uno más una variante de mayúsculas ->
  ``multiple``; uno -> se valida su valor.
- Valor: UTF-8 válido, 1..256 bytes (>256 -> ``oversized``), sin caracteres
  de control ni espacios exteriores (si no -> ``invalid``).
- El formato concreto de Flespi no se asume (pendiente de prueba real).

El valor original solo vive en memoria dentro de ``BrokerIdentityObservation``
(``repr`` sin valor) y nunca se persiste ni se registra. Que la propiedad
exista no prueba que la haya añadido Flespi.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from apps.iot.broker_identity_config import BrokerIdentityKeyRing

PROVIDER_FLESPI = "flespi"
TOKEN_PROPERTY = "token_id"  # noqa: S105 - nombre de propiedad MQTT, no un secreto
MAX_USER_PROPERTIES = 32
MAX_VALUE_BYTES = 256

FINGERPRINT_DOMAIN = b"agrotech-t/iot/broker-identity/v1\x00"
CLASS_DIGEST_DOMAIN = b"agrotech-t/iot/broker-identity-class/v1\x00"
FINGERPRINT_BYTES = 32


class BrokerIdentityPresence(StrEnum):
    ABSENT = "absent"
    SINGLE = "single"
    MULTIPLE = "multiple"
    INVALID = "invalid"
    OVERSIZED = "oversized"


def classify_user_properties(user_properties) -> tuple[BrokerIdentityPresence, bytes | None]:
    """Devuelve ``(presence, valor_utf8 | None)``; el valor solo si es ``single``."""
    if user_properties is None:
        return BrokerIdentityPresence.ABSENT, None
    try:
        pairs = list(user_properties)
    except TypeError:
        return BrokerIdentityPresence.INVALID, None
    if len(pairs) > MAX_USER_PROPERTIES:
        return BrokerIdentityPresence.OVERSIZED, None
    exact: list[object] = []
    variants = 0
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            return BrokerIdentityPresence.INVALID, None
        name = pair[0]
        if not isinstance(name, str):
            return BrokerIdentityPresence.INVALID, None
        if name == TOKEN_PROPERTY:
            exact.append(pair[1])
        elif name.casefold() == TOKEN_PROPERTY:
            variants += 1
    if not exact:
        return BrokerIdentityPresence.ABSENT, None
    if len(exact) > 1 or variants:
        return BrokerIdentityPresence.MULTIPLE, None
    return _validate_value(exact[0])


def _validate_value(value: object) -> tuple[BrokerIdentityPresence, bytes | None]:
    if not isinstance(value, str):
        return BrokerIdentityPresence.INVALID, None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return BrokerIdentityPresence.INVALID, None
    if len(encoded) > MAX_VALUE_BYTES:
        return BrokerIdentityPresence.OVERSIZED, None
    if not encoded or value != value.strip():
        return BrokerIdentityPresence.INVALID, None
    if any(unicodedata.category(char) == "Cc" for char in value):
        return BrokerIdentityPresence.INVALID, None
    return BrokerIdentityPresence.SINGLE, encoded


def token_fingerprint(key: bytes, value: bytes, *, provider: str = PROVIDER_FLESPI) -> bytes:
    """HMAC-SHA256 con dominio y prefijos de longitud; 32 bytes."""
    provider_bytes = provider.encode("ascii")
    if not 0 < len(provider_bytes) < 2**16 or not 0 < len(value) < 2**32:
        raise ValueError("entrada de huella no válida")
    message = (
        FINGERPRINT_DOMAIN
        + len(provider_bytes).to_bytes(2, "big")
        + provider_bytes
        + len(value).to_bytes(4, "big")
        + value
    )
    return hmac.new(key, message, hashlib.sha256).digest()


def class_digest(
    key: bytes, presence: BrokerIdentityPresence, *, provider: str = PROVIDER_FLESPI
) -> bytes:
    """Resumen determinista de una clasificación sin valor (absent, multiple...)."""
    provider_bytes = provider.encode("ascii")
    label = presence.value.encode("ascii")
    message = (
        CLASS_DIGEST_DOMAIN
        + len(provider_bytes).to_bytes(2, "big")
        + provider_bytes
        + len(label).to_bytes(2, "big")
        + label
    )
    return hmac.new(key, message, hashlib.sha256).digest()


def stored_evidence_digest(
    key: bytes, presence: BrokerIdentityPresence, fingerprint: bytes | None
) -> bytes:
    """Resumen comparable de una evidencia guardada (huella o clase)."""
    if presence is BrokerIdentityPresence.SINGLE:
        if fingerprint is None or len(fingerprint) != FINGERPRINT_BYTES:
            raise ValueError("evidencia single sin huella")
        return bytes(fingerprint)
    return class_digest(key, presence)


@dataclass(frozen=True, slots=True)
class BrokerIdentityObservation:
    """Evidencia recibida en memoria. Nunca se persiste ``_value``."""

    presence: BrokerIdentityPresence
    keyring: BrokerIdentityKeyRing = field(repr=False)
    _value: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_user_properties(cls, user_properties, keyring: BrokerIdentityKeyRing):
        presence, value = classify_user_properties(user_properties)
        return cls(presence=presence, keyring=keyring, _value=value)

    def digest(self, version: int) -> bytes:
        """Resumen comparable con la clave de ``version`` (puede lanzar UnavailableKeyVersion)."""
        key = self.keyring.key_for(version)
        if self.presence is BrokerIdentityPresence.SINGLE:
            return token_fingerprint(key, self._value)
        return class_digest(key, self.presence)

    def evidence_for_new_delivery(self) -> tuple[str, int, bytes | None]:
        """``(presence, key_version, fingerprint | None)`` con la versión vigente."""
        version = self.keyring.current_version
        fingerprint = None
        if self.presence is BrokerIdentityPresence.SINGLE:
            fingerprint = token_fingerprint(self.keyring.key_for(version), self._value)
        return self.presence.value, version, fingerprint


@dataclass(frozen=True, slots=True)
class BrokerIdentityObservationFailure:
    """Marcador de observación fallida (BUILD-FLEPSI-02H): sin ningún valor."""

    stage: str
    error_type: str


def short_fingerprint(digest: bytes) -> str:
    """Solo para logs: 8 caracteres hex."""
    return bytes(digest).hex()[:8]


__all__ = [
    "BrokerIdentityObservation",
    "BrokerIdentityObservationFailure",
    "BrokerIdentityPresence",
    "FINGERPRINT_BYTES",
    "MAX_USER_PROPERTIES",
    "MAX_VALUE_BYTES",
    "PROVIDER_FLESPI",
    "TOKEN_PROPERTY",
    "class_digest",
    "classify_user_properties",
    "short_fingerprint",
    "stored_evidence_digest",
    "token_fingerprint",
]
