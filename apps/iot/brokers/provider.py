"""Contrato ``BrokerCredentialProvider`` y evidencia de rotación (FLESPI-01).

Propósito: separar los servicios de dominio (``apps.iot.services.
broker_credentials``) de cualquier proveedor MQTT concreto. Este módulo no
hace red, no conoce rutas REST de Flespi y no persiste nada.

Separación de datos:

- Interno de AGROTECH-T: ``gateway_uid``, ``correlation_id`` (UUID público,
  no secreto) y ``version`` (``CredentialRequest``).
- Proveedor: el resultado (``ProviderOutcome``) y su confirmación
  (``ProviderConfirmation``). La referencia persistente al token del proveedor
  **no existe en esta fase**: el ``token_id`` no se guarda en texto (política
  de BUILD-FLESPI-02). Cómo resolver la credencial en el proveedor a partir de
  ``correlation_id`` es responsabilidad del adaptador de FLESPI-03.
- Secreto de un solo uso: ``OneTimeSecret`` (sin ``repr``/``str`` con valor).
- Errores de integración: ``BrokerProviderError`` y derivados, sin secretos.

Confianza (no criptográfica):

Las confirmaciones y evidencias son **estructurales**: un objeto Python dentro
del mismo proceso no puede probar criptográficamente su origen, y firmarlo con
una clave del propio proceso no añadiría garantía real. Por eso:

1. Los servicios nunca aceptan una confirmación o evidencia como argumento:
   la obtienen ellos mismos de un proveedor o verificador.
2. Solo se aceptan proveedores y verificadores cuyo tipo figure en
   ``TRUSTED_PROVIDER_TYPES`` / ``TRUSTED_EVIDENCE_VERIFIER_TYPES``. **Ambos
   están vacíos en FLESPI-01**: en producción ninguna credencial puede
   confirmarse ni rotarse hasta que FLESPI-03 (proveedor) y FLESPI-04/05
   (evidencia del gateway) registren sus implementaciones revisadas.
3. Los dobles de prueba (``AGROTECH_TEST_DOUBLE = True``) solo se aceptan si
   ``settings.IOT_BROKER_TEST_DOUBLES_ALLOWED is True``. Ese ajuste no existe
   en ``config.settings`` ni se lee del entorno; solo las pruebas lo activan
   (fixture ``settings``), igual que ``IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK``.
   La confirmación resultante queda marcada con origen ``test_double``.
4. Ningún servicio de este contrato tiene ruta HTTP: el frontend no puede
   confirmar ni activar enviando datos.

Pendiente de FLESPI-03: verificar contra la API real que el token existe, está
habilitado, pertenece a la cuenta esperada y tiene la ACL del topic del
gateway; definir la referencia persistente compatible con la política del
``token_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from django.conf import settings

PROVIDER_FLESPI = "flespi"
SUPPORTED_PROVIDERS = frozenset({PROVIDER_FLESPI})
TEST_DOUBLES_SETTING = "IOT_BROKER_TEST_DOUBLES_ALLOWED"
TEST_DOUBLE_MARKER = "AGROTECH_TEST_DOUBLE"
PUBLISH_TOPIC_TEMPLATE = "agrotech/v1/gw/{gateway_uid}/up"
METHOD_MAX_LENGTH = 40

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}\Z")
_METHOD_RE = re.compile(r"^[a-z0-9_.:-]{1,40}\Z")


# --- Errores -----------------------------------------------------------------


class BrokerProviderError(Exception):
    """Error de integración con el proveedor. Nunca incluye secretos."""

    retryable = False
    default_message = "error del proveedor de broker"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class ProviderUnavailable(BrokerProviderError):
    """Fallo transitorio (red, tiempo de espera, 5xx): puede reintentarse."""

    retryable = True
    default_message = "proveedor de broker no disponible"


class ProviderRejected(BrokerProviderError):
    """El proveedor rechazó la operación de forma permanente."""

    default_message = "el proveedor de broker rechazó la operación"


class ProviderNotFound(BrokerProviderError):
    """El proveedor no encuentra la credencial solicitada."""

    default_message = "el proveedor de broker no encuentra la credencial"


class ProviderResponseInvalid(BrokerProviderError):
    """Respuesta del proveedor incoherente o con un formato inesperado."""

    default_message = "respuesta del proveedor de broker no válida"


class ProviderConfigurationError(BrokerProviderError):
    """Configuración del adaptador ausente o inválida (no se revela cuál)."""

    default_message = "configuración del proveedor de broker no válida"


class UntrustedSourceError(BrokerProviderError):
    """Proveedor o verificador no registrado como fuente confiable."""

    default_message = "fuente de confirmación no confiable"


# --- Tipos de resultado ------------------------------------------------------


class ProviderOutcome(StrEnum):
    ENABLED = "enabled"
    PENDING = "pending"
    DISABLED = "disabled"
    NOT_FOUND = "not_found"


class ConfirmationOrigin(StrEnum):
    PROVIDER = "provider"
    TEST_DOUBLE = "test_double"


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Identidad pública del proveedor configurado (sin credenciales)."""

    provider: str
    account_label: str

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ProviderResponseInvalid()
        if not isinstance(self.account_label, str) or not self.account_label.strip():
            raise ProviderResponseInvalid()


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    """Datos internos de AGROTECH-T que identifican una credencial."""

    gateway_uid: str
    correlation_id: UUID
    version: int

    @property
    def publish_topic(self) -> str:
        """Único topic en el que el gateway podrá publicar (ADR-FLESPI-00)."""
        return PUBLISH_TOPIC_TEMPLATE.format(gateway_uid=self.gateway_uid)


@dataclass(frozen=True, slots=True)
class OneTimeSecret:
    """Secreto entregado una sola vez. Sin valor en ``repr`` ni ``str``."""

    _value: str = field(repr=False)

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "OneTimeSecret(<oculto>)"

    def __reduce__(self):
        raise TypeError("OneTimeSecret no se puede serializar")


@dataclass(frozen=True, slots=True)
class ProviderIssueResult:
    """Resultado de crear una credencial; el secreto solo vive aquí."""

    correlation_id: UUID
    outcome: ProviderOutcome
    secret: OneTimeSecret = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderConfirmation:
    """Confirmación del proveedor de que la credencial está habilitada.

    ``confirmation_id`` es único: los servicios lo guardan con ``UNIQUE`` y
    rechazan su reutilización. ``secret_fingerprint`` es la huella de dominio
    (``gateway_credentials.broker_secret_fingerprint``) que el adaptador
    calcula con el secreto de un solo uso; nunca el secreto.
    """

    provider: str
    correlation_id: UUID
    outcome: ProviderOutcome
    confirmation_id: UUID
    observed_at: datetime
    secret_fingerprint: str = field(repr=False)
    key_version: int

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ProviderResponseInvalid()
        if not isinstance(self.correlation_id, UUID) or not isinstance(
            self.confirmation_id, UUID
        ):
            raise ProviderResponseInvalid()
        if not isinstance(self.outcome, ProviderOutcome):
            raise ProviderResponseInvalid()
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ProviderResponseInvalid()
        if not isinstance(self.secret_fingerprint, str) or not _FINGERPRINT_RE.match(
            self.secret_fingerprint
        ):
            raise ProviderResponseInvalid()
        if type(self.key_version) is not int or self.key_version < 1:
            raise ProviderResponseInvalid()


@dataclass(frozen=True, slots=True)
class ProviderStatusResult:
    """Estado observado; ``confirmation`` existe solo si está habilitada."""

    correlation_id: UUID
    outcome: ProviderOutcome
    confirmation: ProviderConfirmation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProviderOutcome):
            raise ProviderResponseInvalid()
        if (self.outcome is ProviderOutcome.ENABLED) != (self.confirmation is not None):
            raise ProviderResponseInvalid()
        if self.confirmation is not None and (
            self.confirmation.correlation_id != self.correlation_id
            or self.confirmation.outcome is not ProviderOutcome.ENABLED
        ):
            raise ProviderResponseInvalid()


@dataclass(frozen=True, slots=True)
class ProviderDisableResult:
    correlation_id: UUID
    outcome: ProviderOutcome

    def __post_init__(self) -> None:
        if self.outcome not in (ProviderOutcome.DISABLED, ProviderOutcome.NOT_FOUND):
            raise ProviderResponseInvalid()


@runtime_checkable
class BrokerCredentialProvider(Protocol):
    """Operaciones mínimas de un proveedor de credenciales MQTT por gateway."""

    provider_name: str

    def describe_identity(self) -> ProviderIdentity: ...

    def create_credential(self, request: CredentialRequest) -> ProviderIssueResult: ...

    def get_credential_status(self, request: CredentialRequest) -> ProviderStatusResult: ...

    def disable_credential(self, request: CredentialRequest) -> ProviderDisableResult: ...


# --- Evidencia de uso de la credencial nueva (rotación) ----------------------


@dataclass(frozen=True, slots=True)
class RotationEvidence:
    """Prueba de que el gateway ya opera con la credencial ``correlation_id``.

    La producirá en FLESPI-04/05 un verificador revisado (p. ej. a partir de
    una entrega MQTT recibida con esa credencial). ``evidence_ref`` identifica
    la evidencia de forma única.
    """

    gateway_uid: str
    correlation_id: UUID
    evidence_ref: UUID
    observed_at: datetime
    method: str

    def __post_init__(self) -> None:
        if not isinstance(self.correlation_id, UUID) or not isinstance(self.evidence_ref, UUID):
            raise ProviderResponseInvalid()
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ProviderResponseInvalid()
        if not isinstance(self.method, str) or not _METHOD_RE.match(self.method):
            raise ProviderResponseInvalid()


@runtime_checkable
class RotationEvidenceVerifier(Protocol):
    def verify_gateway_uses(
        self, *, gateway_uid: str, correlation_id: UUID
    ) -> RotationEvidence | None: ...


# --- Registro de fuentes confiables ------------------------------------------

# FLESPI-03 registrará aquí el adaptador revisado de Flespi.
TRUSTED_PROVIDER_TYPES: frozenset[type] = frozenset()
# FLESPI-04/05 registrará aquí el verificador de uso de la credencial nueva.
TRUSTED_EVIDENCE_VERIFIER_TYPES: frozenset[type] = frozenset()


def source_origin(source: object, trusted_types: frozenset[type]) -> ConfirmationOrigin:
    """Clasifica la fuente o lanza ``UntrustedSourceError`` (falla cerrado)."""
    source_type = type(source)
    if source_type in trusted_types:
        return ConfirmationOrigin.PROVIDER
    if (
        getattr(source_type, TEST_DOUBLE_MARKER, False) is True
        and getattr(settings, TEST_DOUBLES_SETTING, False) is True
    ):
        return ConfirmationOrigin.TEST_DOUBLE
    raise UntrustedSourceError()


__all__ = [
    "BrokerCredentialProvider",
    "BrokerProviderError",
    "ConfirmationOrigin",
    "CredentialRequest",
    "OneTimeSecret",
    "PROVIDER_FLESPI",
    "ProviderConfigurationError",
    "ProviderConfirmation",
    "ProviderDisableResult",
    "ProviderIdentity",
    "ProviderIssueResult",
    "ProviderNotFound",
    "ProviderOutcome",
    "ProviderRejected",
    "ProviderResponseInvalid",
    "ProviderStatusResult",
    "ProviderUnavailable",
    "RotationEvidence",
    "RotationEvidenceVerifier",
    "TRUSTED_EVIDENCE_VERIFIER_TYPES",
    "TRUSTED_PROVIDER_TYPES",
    "UntrustedSourceError",
    "source_origin",
]
