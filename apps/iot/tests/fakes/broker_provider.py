"""Dobles de prueba del proveedor MQTT y del verificador de rotación (FLESPI-01).

**SOLO PARA PRUEBAS.** No hacen red ni representan el comportamiento real de
Flespi: simulan un proveedor en memoria. Se marcan con
``AGROTECH_TEST_DOUBLE = True`` y los servicios solo los aceptan si
``settings.IOT_BROKER_TEST_DOUBLES_ALLOWED is True`` (únicamente en pruebas);
la confirmación resultante queda con origen ``test_double``.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from django.utils import timezone

from apps.iot import gateway_credentials as crypto
from apps.iot.brokers.provider import (
    PROVIDER_FLESPI,
    CredentialRequest,
    OneTimeSecret,
    ProviderConfirmation,
    ProviderDisableResult,
    ProviderIdentity,
    ProviderIssueResult,
    ProviderOutcome,
    ProviderStatusResult,
    RotationEvidence,
)

_ALPHABET = string.ascii_letters + string.digits


def fake_broker_secret() -> str:
    """64 caracteres aleatorios con forma de token (no es un token real)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(64))


@dataclass
class FakeBrokerProviderForTests:
    """Proveedor simulado en memoria. **Solo pruebas.**"""

    AGROTECH_TEST_DOUBLE = True

    provider_name: str = PROVIDER_FLESPI
    outcome: ProviderOutcome = ProviderOutcome.ENABLED
    error: Exception | None = None
    observed_at: datetime | None = None
    confirmation_id: UUID | None = None
    secret_fingerprint: str | None = None
    status_override: object | None = None
    calls: list[str] = field(default_factory=list)
    secrets_issued: dict[UUID, str] = field(default_factory=dict, repr=False)

    def describe_identity(self) -> ProviderIdentity:
        self.calls.append("describe_identity")
        return ProviderIdentity(provider=self.provider_name, account_label="fake-account")

    def create_credential(self, request: CredentialRequest) -> ProviderIssueResult:
        self.calls.append("create_credential")
        if self.error is not None:
            raise self.error
        secret = fake_broker_secret()
        self.secrets_issued[request.correlation_id] = secret
        return ProviderIssueResult(
            correlation_id=request.correlation_id,
            outcome=ProviderOutcome.PENDING,
            secret=OneTimeSecret(secret),
        )

    def get_credential_status(self, request: CredentialRequest) -> ProviderStatusResult:
        self.calls.append("get_credential_status")
        if self.error is not None:
            raise self.error
        if self.status_override is not None:
            return self.status_override
        if self.outcome is not ProviderOutcome.ENABLED:
            return ProviderStatusResult(correlation_id=request.correlation_id, outcome=self.outcome)
        secret = self.secrets_issued.setdefault(request.correlation_id, fake_broker_secret())
        fingerprint = self.secret_fingerprint or crypto.broker_secret_fingerprint(
            request.gateway_uid, secret
        )
        confirmation = ProviderConfirmation(
            provider=self.provider_name,
            correlation_id=request.correlation_id,
            outcome=ProviderOutcome.ENABLED,
            confirmation_id=self.confirmation_id or uuid4(),
            observed_at=self.observed_at or timezone.now(),
            secret_fingerprint=fingerprint,
            key_version=crypto.CURRENT_KEY_VERSION,
        )
        return ProviderStatusResult(
            correlation_id=request.correlation_id,
            outcome=ProviderOutcome.ENABLED,
            confirmation=confirmation,
        )

    def disable_credential(self, request: CredentialRequest) -> ProviderDisableResult:
        self.calls.append("disable_credential")
        if self.error is not None:
            raise self.error
        return ProviderDisableResult(
            correlation_id=request.correlation_id, outcome=ProviderOutcome.DISABLED
        )


class UnmarkedProviderForTests(FakeBrokerProviderForTests):
    """Igual que el doble pero sin la marca: representa una fuente no registrada."""

    AGROTECH_TEST_DOUBLE = False


@dataclass
class FakeRotationEvidenceVerifierForTests:
    """Verificador simulado de uso de la credencial nueva. **Solo pruebas.**"""

    AGROTECH_TEST_DOUBLE = True

    has_evidence: bool = True
    evidence_ref: UUID | None = None
    observed_at: datetime | None = None
    gateway_uid_override: str | None = None
    correlation_override: UUID | None = None
    error: Exception | None = None
    calls: list[tuple[str, UUID]] = field(default_factory=list)

    def verify_gateway_uses(self, *, gateway_uid: str, correlation_id: UUID):
        self.calls.append((gateway_uid, correlation_id))
        if self.error is not None:
            raise self.error
        if not self.has_evidence:
            return None
        return RotationEvidence(
            gateway_uid=self.gateway_uid_override or gateway_uid,
            correlation_id=self.correlation_override or correlation_id,
            evidence_ref=self.evidence_ref or uuid4(),
            observed_at=self.observed_at or timezone.now(),
            method="test_double",
        )


class UnmarkedVerifierForTests(FakeRotationEvidenceVerifierForTests):
    AGROTECH_TEST_DOUBLE = False
