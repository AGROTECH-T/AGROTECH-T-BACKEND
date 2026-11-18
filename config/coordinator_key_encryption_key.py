"""Validación de la clave maestra de cifrado de claves de coordinador (IoT-03.2).

Propósito: una sola fuente de reglas para ``COORDINATOR_KEY_ENCRYPTION_KEY``,
la clave AES-256-GCM con la que se cifran en reposo las claves HMAC de los
coordinadores.

Contexto: mismo patrón que ``config.gateway_credential_key``, con sus propias
reglas porque aquí la clave debe ser material binario exacto, no una cadena
arbitraria:

- Formato: Base64 válido que, al decodificarse, produzca exactamente 32 bytes.
  Se rechazan frases de paso, valores vacíos, Base64 inválido y resultados de
  otra longitud. No hay valor por defecto ni alternativa con ``SECRET_KEY``.
- Versiones: ``COORDINATOR_KEY_ENCRYPTION_KEY_VERSION`` indica la versión
  vigente (con la que se cifran las credenciales nuevas) y
  ``COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS`` permite publicar versiones
  históricas adicionales con el formato ``version:base64[,version:base64]``.
  Así una credencial antigua sigue descifrándose con la versión con la que se
  creó, sin asumir que todas usan la actual.
- Producción (``DEBUG=False``): se valida al arrancar (``config.settings``).
  Desarrollo y pruebas: no se exige al arrancar; las operaciones criptográficas
  fallan de forma cerrada al cargar el anillo de claves.

Funciones puras: no leen variables de entorno. ``load_master_key_ring`` es la
única que consulta ``django.conf.settings`` y lo hace dentro de la función para
no crear un ciclo al importar ``config.settings``. Ningún mensaje incluye el
valor de la clave.
"""
from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field

from django.core.exceptions import ImproperlyConfigured

KEY_NAME = "COORDINATOR_KEY_ENCRYPTION_KEY"
VERSION_NAME = "COORDINATOR_KEY_ENCRYPTION_KEY_VERSION"
PREVIOUS_NAME = "COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS"
KEY_BYTES = 32
CURRENT_VERSION_DEFAULT = 1
# Límite de ``PositiveSmallIntegerField`` (SMALLINT UNSIGNED en MariaDB).
MASTER_KEY_VERSION_MAX = 65535


class UnsupportedMasterKeyVersion(ValueError):
    """La credencial se cifró con una versión de clave maestra no publicada."""


@dataclass(frozen=True)
class MasterKeyRing:
    """Versiones de la clave maestra disponibles en este despliegue.

    ``keys`` se excluye de ``repr`` para que un registro accidental de este
    objeto no exponga las claves.
    """

    current_version: int
    keys: Mapping[int, bytes] = field(repr=False)

    @property
    def supported_versions(self) -> frozenset[int]:
        """Versiones explícitamente soportadas."""
        return frozenset(self.keys)

    def key_for(self, version: int) -> bytes:
        """Clave de la versión indicada; falla de forma cerrada si no existe."""
        try:
            return self.keys[version]
        except KeyError:
            raise UnsupportedMasterKeyVersion(
                f"versión de clave maestra no soportada: {version!r}"
            ) from None


def decode_master_key(value: object) -> bytes:
    """Decodifica y valida una clave maestra Base64 de exactamente 32 bytes.

    Raises:
        ImproperlyConfigured: si falta, no es texto, no es Base64 válido o el
            resultado no mide 32 bytes. El mensaje nunca incluye el valor.
    """
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(
            f"{KEY_NAME} no está configurada; las credenciales de coordinador "
            "no pueden emitirse ni verificarse."
        )
    if value != value.strip():
        raise ImproperlyConfigured(f"{KEY_NAME} no puede tener espacios externos.")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise ImproperlyConfigured(
            f"{KEY_NAME} debe ser Base64 válido (se rechazan frases de paso)."
        ) from None
    if len(decoded) != KEY_BYTES:
        raise ImproperlyConfigured(
            f"{KEY_NAME} debe decodificarse a exactamente {KEY_BYTES} bytes."
        )
    return decoded


def parse_master_key_version(value: object) -> int:
    """Interpreta la versión vigente: entero entre 1 y 65535."""
    invalid = (
        f"{VERSION_NAME} debe ser un entero entre 1 y {MASTER_KEY_VERSION_MAX}."
    )
    if isinstance(value, bool):
        raise ImproperlyConfigured(invalid)
    if isinstance(value, int):
        version = value
    elif isinstance(value, str) and value.strip():
        try:
            version = int(value.strip())
        except ValueError:
            raise ImproperlyConfigured(invalid) from None
    else:
        raise ImproperlyConfigured(invalid)
    if not 1 <= version <= MASTER_KEY_VERSION_MAX:
        raise ImproperlyConfigured(invalid)
    return version


def parse_previous_master_keys(value: object) -> dict[int, bytes]:
    """Interpreta las versiones históricas con formato ``version:base64``.

    Admite ``""``/``None``. Cada entrada se separa por comas; no se permiten
    versiones repetidas. El mensaje de error nombra la versión, nunca la clave.
    """
    if value is None:
        return {}
    if not isinstance(value, str):
        raise ImproperlyConfigured(f"{PREVIOUS_NAME} debe ser texto.")
    text = value.strip()
    if not text:
        return {}
    keys: dict[int, bytes] = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            raise ImproperlyConfigured(f"{PREVIOUS_NAME} contiene una entrada vacía.")
        version_text, separator, key_text = entry.partition(":")
        if not separator:
            raise ImproperlyConfigured(
                f"{PREVIOUS_NAME} debe usar el formato version:base64."
            )
        version = parse_master_key_version(version_text.strip())
        if version in keys:
            raise ImproperlyConfigured(
                f"{PREVIOUS_NAME} repite la versión {version}."
            )
        keys[version] = decode_master_key(key_text.strip())
    return keys


def build_master_key_ring(
    *,
    raw_key: object,
    raw_version: object = CURRENT_VERSION_DEFAULT,
    raw_previous: object = None,
    secret_key: str | None = None,
) -> MasterKeyRing:
    """Construye el anillo validado; no hay valores por defecto de clave."""
    current_version = parse_master_key_version(raw_version)
    if isinstance(raw_key, str) and secret_key:
        if raw_key.strip() and hmac.compare_digest(
            raw_key.encode("utf-8"), secret_key.encode("utf-8")
        ):
            raise ImproperlyConfigured(f"{KEY_NAME} debe ser independiente de SECRET_KEY.")
    current_key = decode_master_key(raw_key)
    previous = parse_previous_master_keys(raw_previous)
    if current_version in previous:
        raise ImproperlyConfigured(
            f"{PREVIOUS_NAME} no puede repetir la versión vigente ({current_version})."
        )
    keys = dict(previous)
    keys[current_version] = current_key
    return MasterKeyRing(current_version=current_version, keys=keys)


def validate_coordinator_master_key(
    *,
    raw_key: object,
    raw_version: object,
    raw_previous: object,
    debug: bool,
    secret_key: str | None,
) -> None:
    """Valida la configuración al arrancar solo en producción.

    En desarrollo y pruebas no lanza: el servicio falla cerrado al cargar el
    anillo cuando la clave falta o es inválida.
    """
    if debug:
        return
    try:
        build_master_key_ring(
            raw_key=raw_key,
            raw_version=raw_version,
            raw_previous=raw_previous,
            secret_key=secret_key,
        )
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"{exc} Es obligatoria en producción.") from None


def load_master_key_ring() -> MasterKeyRing:
    """Anillo de claves del despliegue; falla cerrado si la configuración no vale.

    Se usa en cada operación criptográfica, también en desarrollo: sin clave
    válida no se cifra ni se descifra nada.
    """
    from django.conf import settings

    return build_master_key_ring(
        raw_key=getattr(settings, KEY_NAME, ""),
        raw_version=getattr(settings, VERSION_NAME, CURRENT_VERSION_DEFAULT),
        raw_previous=getattr(settings, PREVIOUS_NAME, ""),
        secret_key=getattr(settings, "SECRET_KEY", "") or "",
    )