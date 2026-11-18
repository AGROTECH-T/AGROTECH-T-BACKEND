"""Criptografía de las credenciales de coordinador (IoT-03.2, DEC-13 §13).

Propósito: generar claves HMAC de coordinador, cifrarlas en reposo con
AES-256-GCM y calcular/verificar la etiqueta HMAC-SHA256 de una trama LoRa.
Sin acceso a base de datos ni a Django settings: todas las dependencias
(taguas, clave maestra, versión, contexto) se inyectan.

Formato aprobado:

- Clave del coordinador: exactamente 32 bytes de ``secrets.token_bytes``.
- Cifrado: AES-256-GCM con nonce aleatorio de 12 bytes; la etiqueta GCM (16
  bytes) viaja dentro del ciphertext, así que una clave de 32 bytes produce 48
  bytes de ciphertext.
- AAD: ``agrotech:co-cred:v1:{coordinator_uid}:{master_key_version}``. Liga el
  ciphertext al coordinador, al dominio y a la versión de la clave maestra:
  modificar cualquiera de los tres, el nonce o el ciphertext hace fallar la
  autenticación.
- HMAC de trama: HMAC-SHA256 sobre ``contexto || bytes_firmados`` y
  comparación con ``hmac.compare_digest``.
- Tag (P-IOT-12, resuelto en IoT-03.2-R3): el HMAC-SHA256 **completo**, de
  exactamente ``LORA_TAG_BYTES = 32`` bytes. La longitud la fija el backend en
  esta constante; nunca se deduce del tag recibido. Un tag de cualquier otra
  longitud (truncado o ampliado) se rechaza y el HMAC esperado nunca se recorta.

Contexto y estructura binaria (IoT-04.1/04.2): el contexto aprobado es
``apps.iot.lora_frame.LORA_FRAME_HMAC_CONTEXT`` y la estructura la interpreta
``apps.iot.lora_frame.parse_lora_frame``. ``compute_lora_hmac`` sigue exigiendo
el contexto como argumento obligatorio: el llamador lo elige explícitamente.

M-02 (IoT-04.2): ``validate_coordinator_frame`` vuelve a interpretar
``signed_bytes + tag`` con el parser v1 y exige que ``coordinator_uid``,
``key_version``, ``boot_counter``, ``seq``, ``signed_bytes`` y ``tag`` coincidan
exactamente; un ``ParsedCoordinatorFrame`` construido a mano con campos que
contradicen los bytes firmados se rechaza.

Reglas de seguridad: ninguna excepción, ``repr`` o log incluye la clave, el
ciphertext ni el nonce. Las funciones que reciben material secreto llevan
``sensitive_variables`` para los informes de error de Django.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.views.decorators.debug import sensitive_variables

from apps.iot import lora_frame
from apps.iot.identifiers import IdentifierKind, is_valid_identifier
from config.coordinator_key_encryption_key import (
    MASTER_KEY_VERSION_MAX,
    MasterKeyRing,
    UnsupportedMasterKeyVersion,
)

COORDINATOR_KEY_BYTES = 32
NONCE_BYTES = 12
GCM_TAG_BYTES = 16
CIPHERTEXT_BYTES = COORDINATOR_KEY_BYTES + GCM_TAG_BYTES  # 48
# Longitud fija del tag LoRa (P-IOT-12): HMAC-SHA256 completo, sin truncar.
LORA_TAG_BYTES = 32
if LORA_TAG_BYTES != hashlib.sha256().digest_size:  # pragma: no cover - invariante
    raise RuntimeError("LORA_TAG_BYTES debe coincidir con la salida de SHA-256")
if LORA_TAG_BYTES != lora_frame.TAG_BYTES:  # pragma: no cover - invariante
    raise RuntimeError("LORA_TAG_BYTES debe coincidir con el tag de la trama v1")
AAD_CONTEXT = "agrotech:co-cred:v1"
INVALID_KEY_MESSAGE = "clave de coordinador no válida"
INVALID_CREDENTIAL_MESSAGE = "credencial cifrada no válida"
UNREADABLE_CREDENTIAL_MESSAGE = "credencial cifrada ilegible"
MISSING_CONTEXT_MESSAGE = "el contexto HMAC es obligatorio"
INVALID_FRAME_MESSAGE = "trama no válida para autenticación"


class InvalidCoordinatorKey(ValueError):
    """La clave del coordinador está mal formada. El mensaje no la incluye."""

    def __init__(self) -> None:
        super().__init__(INVALID_KEY_MESSAGE)


class InvalidCoordinatorIdentifier(ValueError):
    """El ``uid`` no tiene el formato ``CO-`` esperado."""

    def __init__(self) -> None:
        super().__init__("identificador de coordinador no válido")


class InvalidEncryptedCredential(ValueError):
    """Ciphertext o nonce con formato incorrecto. El mensaje no los incluye."""

    def __init__(self) -> None:
        super().__init__(INVALID_CREDENTIAL_MESSAGE)


class CoordinatorCredentialUnreadable(ValueError):
    """AES-GCM no autenticó el ciphertext, el nonce o el AAD."""

    def __init__(self) -> None:
        super().__init__(UNREADABLE_CREDENTIAL_MESSAGE)


class MissingHmacContext(ValueError):
    """Falta el contexto fijo del HMAC (P-IOT-06 sin resolver)."""

    def __init__(self) -> None:
        super().__init__(MISSING_CONTEXT_MESSAGE)


class InvalidLoraTag(ValueError):
    """El tag recibido no tiene un tamaño utilizable."""

    def __init__(self) -> None:
        super().__init__(INVALID_FRAME_MESSAGE)


@dataclass(frozen=True)
class EncryptedCoordinatorKey:
    """Resultado de cifrar una clave; sin material sensible en ``repr``.

    ``master_key_version`` identifica la versión AES-GCM de la clave maestra;
    no es la ``key_version`` que declara la trama LoRa.
    """

    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    master_key_version: int


@dataclass(frozen=True)
class ParsedCoordinatorFrame:
    """Trama ya interpretada por ``apps.iot.lora_frame`` (IoT-04.2).

    Se obtiene con ``LoraFrameV1.to_coordinator_frame()``. Aunque es público,
    ``validate_coordinator_frame`` rechaza cualquier instancia cuyos campos no
    salgan exactamente de ``signed_bytes`` (M-02).

    ``signed_bytes`` son los bytes exactos sobre los que el coordinador calculó
    el tag (``CONTEXTO`` excluido: se añade al verificar). ``tag`` debe medir
    exactamente ``LORA_TAG_BYTES`` (32). ``key_version`` es la versión declarada
    por la trama;
    con rotación inmediata solo la credencial vigente es válida, así que no se
    usa para seleccionar clave, pero se conserva para la tipificación.
    """

    coordinator_uid: str
    key_version: int
    boot_counter: int
    seq: int
    signed_bytes: bytes = field(repr=False)
    tag: bytes = field(repr=False)


def generate_coordinator_key() -> bytes:
    """Genera 32 bytes con la fuente criptográfica de ``secrets``."""
    return secrets.token_bytes(COORDINATOR_KEY_BYTES)


def validate_coordinator_key(value: object) -> bytes:
    """Exige exactamente 32 bytes y los devuelve como ``bytes``."""
    if not isinstance(value, (bytes, bytearray)) or len(value) != COORDINATOR_KEY_BYTES:
        raise InvalidCoordinatorKey()
    return bytes(value)


def validate_encrypted_credential(ciphertext: object, nonce: object) -> tuple[bytes, bytes]:
    """Exige ciphertext de 48 bytes y nonce de 12; los devuelve como ``bytes``."""
    if not isinstance(ciphertext, (bytes, bytearray)) or len(ciphertext) != CIPHERTEXT_BYTES:
        raise InvalidEncryptedCredential()
    if not isinstance(nonce, (bytes, bytearray)) or len(nonce) != NONCE_BYTES:
        raise InvalidEncryptedCredential()
    return bytes(ciphertext), bytes(nonce)


@sensitive_variables("coordinator_uid")
def build_coordinator_aad(coordinator_uid, master_key_version: int) -> bytes:
    """Construye el AAD aprobado, ligado al coordinador y a la versión."""
    if not is_valid_identifier(coordinator_uid, IdentifierKind.COORDINATOR):
        raise InvalidCoordinatorIdentifier()
    if not isinstance(master_key_version, int) or isinstance(master_key_version, bool):
        raise InvalidEncryptedCredential()
    if not 1 <= master_key_version <= MASTER_KEY_VERSION_MAX:
        raise InvalidEncryptedCredential()
    return f"{AAD_CONTEXT}:{coordinator_uid}:{master_key_version}".encode("ascii")


@sensitive_variables("key", "master_key")
def encrypt_coordinator_key(
    key,
    *,
    coordinator_uid,
    key_ring: MasterKeyRing,
    master_key_version: int | None = None,
) -> EncryptedCoordinatorKey:
    """Cifra una clave de coordinador con la versión vigente o la indicada.

    Raises:
        InvalidCoordinatorKey: clave o ``uid`` mal formados.
        InvalidEncryptedCredential: versión inválida.
        UnsupportedMasterKeyVersion: la versión no está publicada.
    """
    clean_key = validate_coordinator_key(key)
    version = key_ring.current_version if master_key_version is None else master_key_version
    master_key = key_ring.key_for(version)
    aad = build_coordinator_aad(coordinator_uid, version)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(master_key).encrypt(nonce, clean_key, aad)
    return EncryptedCoordinatorKey(
        ciphertext=ciphertext, nonce=nonce, master_key_version=version
    )


@sensitive_variables("ciphertext", "nonce", "master_key")
def decrypt_coordinator_key(
    ciphertext,
    nonce,
    *,
    coordinator_uid,
    key_ring: MasterKeyRing,
    master_key_version: int,
) -> bytes:
    """Descifra y autentica una credencial; devuelve la clave solo en memoria.

    Raises:
        InvalidEncryptedCredential: longitudes o versión inválidas.
        UnsupportedMasterKeyVersion: versión no publicada.
        CoordinatorCredentialUnreadable: el ciphertext, el nonce o el AAD no
            autentican (manipulación o credencial de otro coordinador).
    """
    clean_ciphertext, clean_nonce = validate_encrypted_credential(ciphertext, nonce)
    master_key = key_ring.key_for(master_key_version)
    aad = build_coordinator_aad(coordinator_uid, master_key_version)
    try:
        key = AESGCM(master_key).decrypt(clean_nonce, clean_ciphertext, aad)
    except InvalidTag:
        raise CoordinatorCredentialUnreadable() from None
    return validate_coordinator_key(key)


@sensitive_variables("key", "signed_bytes")
def compute_lora_hmac(key, signed_bytes, *, context) -> bytes:
    """HMAC-SHA256 del contexto y los bytes firmados de la trama.

    ``context`` es obligatorio; para la trama v1 es
    ``lora_frame.LORA_FRAME_HMAC_CONTEXT``.
    """
    clean_key = validate_coordinator_key(key)
    if not isinstance(context, (bytes, bytearray)) or not bytes(context):
        raise MissingHmacContext()
    if not isinstance(signed_bytes, (bytes, bytearray)):
        raise InvalidLoraTag()
    return hmac.new(clean_key, bytes(context) + bytes(signed_bytes), hashlib.sha256).digest()


@sensitive_variables("key", "signed_bytes", "tag")
def verify_lora_hmac(key, signed_bytes, tag, *, context) -> bool:
    """Compara en tiempo constante el tag recibido con el HMAC completo.

    Devuelve ``False`` si el tag no es ``bytes`` o no mide exactamente
    ``LORA_TAG_BYTES``: la longitud recibida nunca se usa para recortar el HMAC
    esperado. Propaga los errores de configuración (clave inválida, contexto
    ausente).
    """
    expected = compute_lora_hmac(key, signed_bytes, context=context)
    if not isinstance(tag, (bytes, bytearray)):
        return False
    clean_tag = bytes(tag)
    if len(clean_tag) != LORA_TAG_BYTES:
        return False
    return hmac.compare_digest(expected, clean_tag)


@sensitive_variables("frame", "signed_bytes", "tag", "parsed")
def validate_coordinator_frame(frame: object) -> ParsedCoordinatorFrame:
    """Valida forma y coherencia con los bytes firmados antes de tocar la base.

    M-02: vuelve a interpretar ``signed_bytes + tag`` con el parser v1 y exige
    que todos los campos declarados coincidan con lo que dicen esos bytes. Que
    coincidan no autentica nada: los campos solo son de confianza cuando el HMAC
    se verifica sobre ``signed_bytes``.
    """
    if not isinstance(frame, ParsedCoordinatorFrame):
        raise InvalidLoraTag()
    if not is_valid_identifier(frame.coordinator_uid, IdentifierKind.COORDINATOR):
        raise InvalidLoraTag()
    for value in (frame.key_version, frame.boot_counter, frame.seq):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidLoraTag()
    if frame.key_version < 1:
        raise InvalidLoraTag()
    if not isinstance(frame.signed_bytes, (bytes, bytearray)) or not bytes(frame.signed_bytes):
        raise InvalidLoraTag()
    if not isinstance(frame.tag, (bytes, bytearray)):
        raise InvalidLoraTag()
    if len(bytes(frame.tag)) != LORA_TAG_BYTES:
        raise InvalidLoraTag()
    signed_bytes = bytes(frame.signed_bytes)
    tag = bytes(frame.tag)
    try:
        parsed = lora_frame.parse_lora_frame(signed_bytes + tag)
    except lora_frame.LoraFrameError:
        raise InvalidLoraTag() from None
    if (
        parsed.coordinator_uid != frame.coordinator_uid
        or parsed.key_version != frame.key_version
        or parsed.boot_counter != frame.boot_counter
        or parsed.seq != frame.seq
        or parsed.signed_bytes != signed_bytes
        or parsed.tag != tag
    ):
        raise InvalidLoraTag()
    return frame


def master_key_version_supported(key_ring: MasterKeyRing, master_key_version: int) -> bool:
    """Indica si la versión de clave maestra está publicada en este despliegue."""
    return master_key_version in key_ring.supported_versions


__all__ = [
    "AAD_CONTEXT",
    "CIPHERTEXT_BYTES",
    "COORDINATOR_KEY_BYTES",
    "CoordinatorCredentialUnreadable",
    "EncryptedCoordinatorKey",
    "InvalidCoordinatorIdentifier",
    "InvalidCoordinatorKey",
    "InvalidEncryptedCredential",
    "InvalidLoraTag",
    "LORA_TAG_BYTES",
    "MASTER_KEY_VERSION_MAX",
    "MissingHmacContext",
    "NONCE_BYTES",
    "ParsedCoordinatorFrame",
    "UnsupportedMasterKeyVersion",
    "build_coordinator_aad",
    "compute_lora_hmac",
    "decrypt_coordinator_key",
    "encrypt_coordinator_key",
    "generate_coordinator_key",
    "master_key_version_supported",
    "validate_coordinator_frame",
    "validate_coordinator_key",
    "validate_encrypted_credential",
    "verify_lora_hmac",
]