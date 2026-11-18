"""Modo y anillo de claves de la identidad del broker MQTT (BUILD-FLEPSI-02).

Se lee del entorno dentro del módulo IoT (mismo patrón que
``MqttSettings.from_env``) y lo validan al arrancar los comandos
``mqtt_consumer`` y ``mqtt_delivery_worker``. Ningún mensaje de error incluye
el valor de una clave.

Modos:

- ``off`` (por defecto): no se capturan propiedades MQTT; no se exige clave.
- ``record``: se captura y persiste la evidencia y se registran anomalías y
  veredictos; nunca se rechaza una medición por identidad del broker.
- ``enforce``: **no seleccionable en este BUILD**; el arranque falla.

Claves (independientes de cualquier otra clave del proyecto):

- ``IOT_BROKER_IDENTITY_HMAC_KEY``: Base64 de exactamente 32 bytes (vigente).
- ``IOT_BROKER_IDENTITY_HMAC_KEY_VERSION``: entero 1..65535.
- ``IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS``: ``v:base64,...`` (solo comparar).
- ``IOT_BROKER_IDENTITY_HMAC_KEY_NEXT``: ``v:base64`` (solo registrar huellas).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
from dataclasses import dataclass, field
from enum import StrEnum

from django.core.exceptions import ImproperlyConfigured

ENV_MODE = "IOT_BROKER_IDENTITY_MODE"
ENV_KEY = "IOT_BROKER_IDENTITY_HMAC_KEY"
ENV_VERSION = "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION"
ENV_PREVIOUS = "IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS"
ENV_NEXT = "IOT_BROKER_IDENTITY_HMAC_KEY_NEXT"

KEY_BYTES = 32
VERSION_MIN = 1
VERSION_MAX = 65535

# Claves del proyecto de las que la clave de identidad debe ser independiente.
_OTHER_SECRET_SETTINGS = (
    "SECRET_KEY",
    "GATEWAY_CREDENTIAL_HMAC_KEY",
    "INVITATION_CODE_HMAC_KEY",
    "COORDINATOR_KEY_ENCRYPTION_KEY",
)
_OTHER_SECRET_LIST_SETTINGS = ("COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS",)


class BrokerIdentityMode(StrEnum):
    OFF = "off"
    RECORD = "record"
    ENFORCE = "enforce"


class UnavailableKeyVersion(LookupError):
    """La versión pedida no está en el anillo cargado (p. ej. retirada)."""


@dataclass(frozen=True, slots=True)
class BrokerIdentityKeyRing:
    current_version: int
    next_version: int | None
    _keys: dict[int, bytes] = field(repr=False)

    def key_for(self, version: int) -> bytes:
        """Clave para calcular o comparar huellas de esa versión."""
        if type(version) is not int or version not in self._keys:
            raise UnavailableKeyVersion(f"versión de clave de identidad no disponible: {version!r}")
        return self._keys[version]

    @property
    def versions(self) -> frozenset[int]:
        return frozenset(self._keys)

    @property
    def previous_versions(self) -> frozenset[int]:
        excluded = {self.current_version}
        if self.next_version is not None:
            excluded.add(self.next_version)
        return frozenset(self._keys) - excluded

    def describe(self) -> str:
        """Solo versiones; nunca valores."""
        previous = sorted(self.previous_versions)
        return (
            f"vigente=v{self.current_version} next="
            f"{'v' + str(self.next_version) if self.next_version else '-'} "
            f"previous={['v' + str(v) for v in previous]}"
        )


@dataclass(frozen=True, slots=True)
class BrokerIdentityConfig:
    mode: BrokerIdentityMode
    keyring: BrokerIdentityKeyRing | None = None

    def __post_init__(self) -> None:
        # BUILD-FLEPSI-02H: also for objects built by hand, ENFORCE is an
        # explicit error (never a silent OFF) and RECORD needs a key ring.
        require_operational_config(self)

    @property
    def records(self) -> bool:
        return self.mode is BrokerIdentityMode.RECORD


def require_operational_config(config: object) -> None:
    """Valida un objeto de configuración recibido (también construido a mano).

    Solo ``off`` y ``record`` son operativos; cualquier otro modo, tipo o
    combinación falla con ``ImproperlyConfigured``.
    """
    if not isinstance(config, BrokerIdentityConfig):
        raise ImproperlyConfigured("configuración de identidad del broker no válida")
    if config.mode is BrokerIdentityMode.ENFORCE:
        raise ImproperlyConfigured(
            f"{ENV_MODE}=enforce no está habilitado: requiere validar el comportamiento "
            "real de Flespi y una autorización expresa."
        )
    if config.mode is BrokerIdentityMode.RECORD:
        if not isinstance(config.keyring, BrokerIdentityKeyRing):
            raise ImproperlyConfigured("el modo record requiere un anillo de claves válido")
    elif config.mode is not BrokerIdentityMode.OFF:
        raise ImproperlyConfigured("modo de identidad del broker no válido")


def require_operational_mode_from_env(environ: dict[str, str] | None = None) -> BrokerIdentityMode:
    """Modo de entorno para el worker en cada lote: enforce o inválido -> error."""
    values = os.environ if environ is None else environ
    mode = parse_mode(values.get(ENV_MODE))
    if mode is BrokerIdentityMode.ENFORCE:
        raise ImproperlyConfigured(
            f"{ENV_MODE}=enforce no está habilitado: requiere validar el comportamiento "
            "real de Flespi y una autorización expresa."
        )
    return mode


def load_broker_identity_config(
    environ: dict[str, str] | None = None, *, django_settings=None
) -> BrokerIdentityConfig:
    """Lee y valida modo y claves; falla cerrado con ``ImproperlyConfigured``."""
    values = os.environ if environ is None else environ
    mode = parse_mode(values.get(ENV_MODE))
    if mode is BrokerIdentityMode.ENFORCE:
        raise ImproperlyConfigured(
            f"{ENV_MODE}=enforce no está habilitado: requiere validar el comportamiento "
            "real de Flespi y una autorización expresa."
        )
    if mode is BrokerIdentityMode.OFF:
        return BrokerIdentityConfig(mode=mode)
    keyring = build_keyring(
        raw_key=values.get(ENV_KEY),
        raw_version=values.get(ENV_VERSION),
        raw_previous=values.get(ENV_PREVIOUS),
        raw_next=values.get(ENV_NEXT),
    )
    _require_independence(keyring, django_settings=django_settings)
    return BrokerIdentityConfig(mode=mode, keyring=keyring)


def load_admin_keyring(
    environ: dict[str, str] | None = None, *, django_settings=None
) -> BrokerIdentityKeyRing:
    """Anillo para tareas administrativas (registro/rotación), aunque el modo sea off."""
    values = os.environ if environ is None else environ
    if parse_mode(values.get(ENV_MODE)) is BrokerIdentityMode.ENFORCE:
        raise ImproperlyConfigured(f"{ENV_MODE}=enforce no está habilitado.")
    keyring = build_keyring(
        raw_key=values.get(ENV_KEY),
        raw_version=values.get(ENV_VERSION),
        raw_previous=values.get(ENV_PREVIOUS),
        raw_next=values.get(ENV_NEXT),
    )
    _require_independence(keyring, django_settings=django_settings)
    return keyring


def parse_mode(raw: object) -> BrokerIdentityMode:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return BrokerIdentityMode.OFF
    if not isinstance(raw, str):
        raise ImproperlyConfigured(f"{ENV_MODE} debe ser texto.")
    try:
        return BrokerIdentityMode(raw.strip().lower())
    except ValueError:
        raise ImproperlyConfigured(f"{ENV_MODE} debe ser off, record o enforce.") from None


def build_keyring(*, raw_key, raw_version, raw_previous, raw_next) -> BrokerIdentityKeyRing:
    current_version = _parse_version(raw_version, ENV_VERSION, required=True)
    keys = {current_version: _decode_key(raw_key, ENV_KEY)}
    for version, key in _parse_entries(raw_previous, ENV_PREVIOUS).items():
        if version in keys:
            raise ImproperlyConfigured(f"{ENV_PREVIOUS} repite la versión {version}.")
        keys[version] = key
    next_entries = _parse_entries(raw_next, ENV_NEXT)
    if len(next_entries) > 1:
        raise ImproperlyConfigured(f"{ENV_NEXT} admite una sola versión.")
    next_version = None
    for version, key in next_entries.items():
        if version in keys:
            raise ImproperlyConfigured(f"{ENV_NEXT} repite la versión {version}.")
        keys[version] = key
        next_version = version
    materials = list(keys.values())
    for index, key in enumerate(materials):
        for other in materials[index + 1 :]:
            if hmac.compare_digest(key, other):
                raise ImproperlyConfigured(
                    "Las versiones de la clave de identidad del broker deben ser distintas."
                )
    return BrokerIdentityKeyRing(
        current_version=current_version, next_version=next_version, _keys=keys
    )


def _decode_key(value: object, name: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"{name} no está configurada.")
    if value != value.strip():
        raise ImproperlyConfigured(f"{name} no puede tener espacios externos.")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise ImproperlyConfigured(f"{name} debe ser Base64 válido.") from None
    if len(decoded) != KEY_BYTES:
        raise ImproperlyConfigured(f"{name} debe decodificarse a exactamente {KEY_BYTES} bytes.")
    return decoded


def _parse_version(value: object, name: str, *, required: bool) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ImproperlyConfigured(f"{name} es obligatoria.")
        raise ImproperlyConfigured(f"{name} no es válida.")
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or str(int(text)) != text:
        raise ImproperlyConfigured(
            f"{name} debe ser un entero entre {VERSION_MIN} y {VERSION_MAX}."
        )
    version = int(text)
    if not VERSION_MIN <= version <= VERSION_MAX:
        raise ImproperlyConfigured(
            f"{name} debe ser un entero entre {VERSION_MIN} y {VERSION_MAX}."
        )
    return version


def _parse_entries(value: object, name: str) -> dict[int, bytes]:
    if value is None:
        return {}
    if not isinstance(value, str):
        raise ImproperlyConfigured(f"{name} debe ser texto.")
    text = value.strip()
    if not text:
        return {}
    entries: dict[int, bytes] = {}
    for entry in text.split(","):
        entry = entry.strip()
        version_text, separator, key_text = entry.partition(":")
        if not entry or not separator:
            raise ImproperlyConfigured(f"{name} debe usar el formato version:base64.")
        version = _parse_version(version_text, name, required=True)
        if version in entries:
            raise ImproperlyConfigured(f"{name} repite la versión {version}.")
        entries[version] = _decode_key(key_text.strip(), name)
    return entries


def _require_independence(keyring: BrokerIdentityKeyRing, *, django_settings=None) -> None:
    """La clave de identidad no puede coincidir con ninguna otra clave del proyecto."""
    if django_settings is None:
        from django.conf import settings as django_settings
    others: list[bytes] = []
    for name in _OTHER_SECRET_SETTINGS:
        others.extend(_candidate_materials(getattr(django_settings, name, "")))
    for name in _OTHER_SECRET_LIST_SETTINGS:
        raw = getattr(django_settings, name, "") or ""
        for entry in str(raw).split(","):
            _version, _sep, material = entry.partition(":")
            others.extend(_candidate_materials(material.strip()))
    for version in keyring.versions:
        key = keyring.key_for(version)
        encoded = base64.b64encode(key)
        for other in others:
            if hmac.compare_digest(key, other) or hmac.compare_digest(encoded, other):
                raise ImproperlyConfigured(
                    f"{ENV_KEY} (v{version}) debe ser independiente de las demás "
                    "claves del proyecto."
                )


def _candidate_materials(value: object) -> list[bytes]:
    if not isinstance(value, str) or not value.strip():
        return []
    text = value.strip()
    materials = [text.encode("utf-8")]
    try:
        materials.append(base64.b64decode(text.encode("ascii"), validate=True))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        pass
    return materials


__all__ = [
    "BrokerIdentityConfig",
    "BrokerIdentityKeyRing",
    "BrokerIdentityMode",
    "UnavailableKeyVersion",
    "build_keyring",
    "load_admin_keyring",
    "load_broker_identity_config",
    "parse_mode",
    "require_operational_config",
    "require_operational_mode_from_env",
]
