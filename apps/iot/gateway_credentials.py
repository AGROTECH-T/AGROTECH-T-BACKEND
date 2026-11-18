"""Secretos y huellas de las credenciales de gateway (IoT-03.1, DEC-13 §12).

Propósito: generar el secreto de un gateway, calcular su huella HMAC-SHA256 y
verificarlo en tiempo constante. Sin acceso a base de datos.

Formato del secreto: ``gws_`` + ``secrets.token_urlsafe(32)`` (256 bits; 43
caracteres URL-safe sin relleno; 47 en total). El prefijo permite reconocerlo
en revisiones de código y de logs.

Huella: ``HMAC-SHA256(clave[key_version], "agrotech:gw-cred:v1:{uid}:{secreto}")``
en hexadecimal (64 caracteres). Va ligada al ``uid`` del gateway: el secreto de
un gateway no sirve para otro. El secreto es aleatorio de alta entropía, así que
no necesita un hash lento (Argon2/PBKDF2 se reservan para contraseñas humanas).

Versiones de clave: hoy solo existe ``CURRENT_KEY_VERSION = 1``, que corresponde
a ``settings.GATEWAY_CREDENTIAL_HMAC_KEY``. Cada huella guarda su
``key_version``. Rotar la clave del servidor (no implementado) consistirá en
publicar la clave nueva con una versión mayor, emitir con ella las credenciales
nuevas y seguir verificando las existentes con la versión con que se crearon
hasta que se reemitan; así no se invalida ninguna credencial al rotar. Una
versión desconocida no verifica (falla de forma cerrada).

Reglas de seguridad: el secreto solo se devuelve al servicio que lo emite y
nunca se persiste. Ningún mensaje de error ni ``repr`` incluye el secreto, la
huella ni la clave. Las funciones que reciben el secreto se marcan con
``sensitive_variables`` para que los informes de error de Django no lo muestren;
no es la única protección: tampoco se interpola en excepciones.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from apps.iot.identifiers import IdentifierKind, is_valid_identifier
from config.gateway_credential_key import KEY_NAME, check_gateway_credential_key

SECRET_PREFIX = "gws_"  # noqa: S105 - prefijo público del formato, no un secreto.
SECRET_RANDOM_BYTES = 32
SECRET_RANDOM_LENGTH = 43  # token_urlsafe(32): 32 bytes en Base64 URL sin relleno.
SECRET_LENGTH = len(SECRET_PREFIX) + SECRET_RANDOM_LENGTH
FINGERPRINT_CONTEXT = "agrotech:gw-cred:v1"
FINGERPRINT_LENGTH = 64
CURRENT_KEY_VERSION = 1
SUPPORTED_KEY_VERSIONS = frozenset({CURRENT_KEY_VERSION})
INVALID_CREDENTIAL_MESSAGE = "credencial de gateway no válida"

_SECRET_RE = re.compile(rf"^{SECRET_PREFIX}[A-Za-z0-9_-]{{{SECRET_RANDOM_LENGTH}}}$")
# FLESPI-01: secreto emitido por el proveedor MQTT (token de 64 caracteres).
BROKER_SECRET_FINGERPRINT_CONTEXT = "agrotech:gw-broker-cred:v1"  # noqa: S105 - contexto público
BROKER_SECRET_LENGTH = 64
_BROKER_SECRET_RE = re.compile(rf"^[\x21-\x7e]{{{BROKER_SECRET_LENGTH}}}\Z")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class InvalidGatewayCredential(ValueError):
    """Entrada mal formada. El mensaje nunca incluye la entrada."""

    def __init__(self) -> None:
        super().__init__(INVALID_CREDENTIAL_MESSAGE)


class UnsupportedKeyVersion(ValueError):
    """La huella se creó con una versión de clave que este servidor no tiene."""


def generate_secret() -> str:
    """Genera un secreto nuevo con ``secrets.token_urlsafe``."""
    return SECRET_PREFIX + secrets.token_urlsafe(SECRET_RANDOM_BYTES)


@sensitive_variables("value")
def is_well_formed_secret(value) -> bool:
    """Valida el formato exacto del secreto, sin normalizar."""
    return isinstance(value, str) and value.isascii() and bool(_SECRET_RE.match(value))


def is_well_formed_fingerprint(value) -> bool:
    """64 caracteres hexadecimales en minúscula."""
    return isinstance(value, str) and bool(_FINGERPRINT_RE.match(value))


def _key(key_version: int) -> bytes:
    """Clave de la versión indicada; falla de forma cerrada sin revelarla."""
    if key_version not in SUPPORTED_KEY_VERSIONS:
        raise UnsupportedKeyVersion(f"versión de clave no soportada: {key_version!r}")
    key = check_gateway_credential_key(
        getattr(settings, KEY_NAME, ""),
        secret_key=getattr(settings, "SECRET_KEY", "") or "",
    )
    return key.encode("utf-8")


@sensitive_variables("secret", "message")
def fingerprint(gateway_uid, secret, *, key_version: int = CURRENT_KEY_VERSION) -> str:
    """Calcula la huella del secreto ligada al ``uid`` del gateway.

    Raises:
        InvalidGatewayCredential: ``uid`` o secreto mal formados.
        UnsupportedKeyVersion: versión de clave desconocida.
        ImproperlyConfigured: falta la clave o no es válida.
    """
    if not is_valid_identifier(gateway_uid, IdentifierKind.GATEWAY):
        raise InvalidGatewayCredential()
    if not is_well_formed_secret(secret):
        raise InvalidGatewayCredential()
    message = f"{FINGERPRINT_CONTEXT}:{gateway_uid}:{secret}".encode()
    return hmac.new(_key(key_version), message, hashlib.sha256).hexdigest()


def fingerprints_match(expected, candidate) -> bool:
    """Compara dos huellas en tiempo constante."""
    if not is_well_formed_fingerprint(expected) or not is_well_formed_fingerprint(candidate):
        return False
    return hmac.compare_digest(expected, candidate)


@sensitive_variables("secret")
def verify_secret(gateway_uid, secret, *, expected_fingerprint, key_version: int) -> bool:
    """Indica si ``secret`` corresponde a ``expected_fingerprint``.

    Una entrada mal formada o una versión de clave desconocida devuelven
    ``False``. La falta de clave (``ImproperlyConfigured``) se propaga: es un
    error de despliegue, no una credencial incorrecta.
    """
    try:
        candidate = fingerprint(gateway_uid, secret, key_version=key_version)
    except (InvalidGatewayCredential, UnsupportedKeyVersion):
        return False
    return fingerprints_match(expected_fingerprint, candidate)


@sensitive_variables("secret", "message")
def broker_secret_fingerprint(
    gateway_uid, secret, *, key_version: int = CURRENT_KEY_VERSION
) -> str:
    """Huella del secreto que emite un proveedor MQTT externo (FLESPI-01).

    Mismo HMAC y misma clave que la credencial ``gws_`` (mismo propósito:
    identificar la credencial de un gateway sin poder reconstruirla) con un
    contexto propio, ``agrotech:gw-broker-cred:v1``: una huella nunca es
    válida en el otro dominio. No usa la clave de identidad del broker
    (``IOT_BROKER_IDENTITY_HMAC_KEY``), que debe ser independiente.

    Solo se valida la longitud (64) y que sean caracteres ASCII imprimibles sin
    espacios: el alfabeto exacto del token del proveedor no está verificado.

    Raises:
        InvalidGatewayCredential: ``uid`` o secreto mal formados.
        UnsupportedKeyVersion: versión de clave desconocida.
        ImproperlyConfigured: falta la clave o no es válida.
    """
    if not is_valid_identifier(gateway_uid, IdentifierKind.GATEWAY):
        raise InvalidGatewayCredential()
    if not is_well_formed_broker_secret(secret):
        raise InvalidGatewayCredential()
    message = f"{BROKER_SECRET_FINGERPRINT_CONTEXT}:{gateway_uid}:{secret}".encode()
    return hmac.new(_key(key_version), message, hashlib.sha256).hexdigest()


@sensitive_variables("value")
def is_well_formed_broker_secret(value) -> bool:
    """64 caracteres ASCII imprimibles, sin espacios (sin normalizar)."""
    return isinstance(value, str) and bool(_BROKER_SECRET_RE.match(value))


@sensitive_variables("secret")
def new_credential_for(gateway_uid) -> tuple[str, str, int]:
    """Genera un secreto nuevo y su huella con la versión de clave vigente.

    Returns:
        ``(secreto, huella, key_version)``. El secreto no debe persistirse.
    """
    secret = generate_secret()
    return secret, fingerprint(gateway_uid, secret), CURRENT_KEY_VERSION
