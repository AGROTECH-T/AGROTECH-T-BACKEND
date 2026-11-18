# ruff: noqa: S101, S105, S106
"""Contrato del proveedor, confianza, huella, serializador y auditoría (FLESPI-01).

Pruebas sin base de datos: tipos inmutables, validación de confirmaciones,
registro de fuentes confiables (vacío en producción), separación del doble de
pruebas, huella de dominio, ausencia de secretos en ``repr``/serializador y
claves de auditoría aceptadas por ``record_audit_event``.
"""

import pickle
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.accounts.errors import AppError
from apps.iot import gateway_credentials as crypto
from apps.iot.api.serializers import BrokerCredentialSerializer
from apps.iot.brokers import provider as contract
from apps.iot.brokers.provider import (
    BrokerCredentialProvider,
    ConfirmationOrigin,
    CredentialRequest,
    OneTimeSecret,
    ProviderConfirmation,
    ProviderDisableResult,
    ProviderIssueResult,
    ProviderOutcome,
    ProviderResponseInvalid,
    ProviderStatusResult,
    ProviderUnavailable,
    RotationEvidence,
    RotationEvidenceVerifier,
    UntrustedSourceError,
    source_origin,
)
from apps.iot.models import (
    BrokerConfirmationOrigin,
    BrokerCredential,
    BrokerCredentialState,
    Gateway,
)
from apps.iot.services import broker_credentials as service
from apps.iot.tests.fakes.broker_provider import (
    FakeBrokerProviderForTests,
    FakeRotationEvidenceVerifierForTests,
    UnmarkedProviderForTests,
    fake_broker_secret,
)
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import _clean_payload

UID = "GW-AAAAAAAAAA"
FINGERPRINT = "a" * 64


@pytest.fixture(autouse=True)
def key(settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)


def _confirmation(**overrides):
    values = {
        "provider": "flespi",
        "correlation_id": uuid4(),
        "outcome": ProviderOutcome.ENABLED,
        "confirmation_id": uuid4(),
        "observed_at": datetime.now(UTC),
        "secret_fingerprint": FINGERPRINT,
        "key_version": 1,
    }
    values.update(overrides)
    return ProviderConfirmation(**values)


# --- Tipos del contrato --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "mosquitto"},
        {"correlation_id": "no-uuid"},
        {"confirmation_id": "no-uuid"},
        {"outcome": "enabled"},
        {"observed_at": datetime(2026, 1, 1)},  # noqa: DTZ001 - sin zona a propósito
        {"secret_fingerprint": "A" * 64},
        {"secret_fingerprint": "a" * 63},
        {"secret_fingerprint": "a" * 64 + "\n"},
        {"key_version": 0},
        {"key_version": True},
        {"key_version": "1"},
    ],
)
def test_confirmation_rejects_invalid_fields(overrides):
    with pytest.raises(ProviderResponseInvalid):
        _confirmation(**overrides)


def test_confirmation_is_frozen_and_hides_fingerprint():
    confirmation = _confirmation()
    with pytest.raises(AttributeError):
        confirmation.key_version = 2
    assert FINGERPRINT not in repr(confirmation)


def test_status_result_coherence():
    correlation = uuid4()
    confirmation = _confirmation(correlation_id=correlation)
    with pytest.raises(ProviderResponseInvalid):
        ProviderStatusResult(correlation_id=correlation, outcome=ProviderOutcome.ENABLED)
    with pytest.raises(ProviderResponseInvalid):
        ProviderStatusResult(
            correlation_id=correlation, outcome=ProviderOutcome.DISABLED, confirmation=confirmation
        )
    with pytest.raises(ProviderResponseInvalid):
        ProviderStatusResult(
            correlation_id=uuid4(), outcome=ProviderOutcome.ENABLED, confirmation=confirmation
        )
    ok = ProviderStatusResult(
        correlation_id=correlation, outcome=ProviderOutcome.ENABLED, confirmation=confirmation
    )
    assert ok.confirmation is confirmation


def test_disable_result_only_accepts_terminal_outcomes():
    with pytest.raises(ProviderResponseInvalid):
        ProviderDisableResult(correlation_id=uuid4(), outcome=ProviderOutcome.ENABLED)
    assert ProviderDisableResult(
        correlation_id=uuid4(), outcome=ProviderOutcome.NOT_FOUND
    ).outcome is ProviderOutcome.NOT_FOUND


def test_one_time_secret_never_shows_its_value():
    value = fake_broker_secret()
    secret = OneTimeSecret(value)
    issue = ProviderIssueResult(
        correlation_id=uuid4(), outcome=ProviderOutcome.PENDING, secret=secret
    )
    for text in (repr(secret), str(secret), repr(issue), str(issue), f"{secret}"):
        assert value not in text
    assert secret.reveal() == value
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_rotation_evidence_validation():
    with pytest.raises(ProviderResponseInvalid):
        RotationEvidence(UID, uuid4(), uuid4(), datetime(2026, 1, 1), "x")  # noqa: DTZ001
    with pytest.raises(ProviderResponseInvalid):
        RotationEvidence(UID, uuid4(), uuid4(), datetime.now(UTC), "con espacios")
    with pytest.raises(ProviderResponseInvalid):
        RotationEvidence(UID, "x", uuid4(), datetime.now(UTC), "ok")


def test_request_topic_is_the_gateway_topic():
    request = CredentialRequest(gateway_uid=UID, correlation_id=uuid4(), version=1)
    assert request.publish_topic == f"agrotech/v1/gw/{UID}/up"


def test_test_doubles_satisfy_the_protocols():
    assert isinstance(FakeBrokerProviderForTests(), BrokerCredentialProvider)
    assert isinstance(FakeRotationEvidenceVerifierForTests(), RotationEvidenceVerifier)


def test_provider_errors_are_typed_and_generic():
    assert ProviderUnavailable().retryable is True
    assert contract.ProviderRejected().retryable is False
    assert issubclass(UntrustedSourceError, contract.BrokerProviderError)
    assert "flespi" not in str(contract.ProviderConfigurationError())


# --- Confianza -----------------------------------------------------------------


def test_production_registries_are_empty():
    assert contract.TRUSTED_PROVIDER_TYPES == frozenset()
    assert contract.TRUSTED_EVIDENCE_VERIFIER_TYPES == frozenset()


def test_test_double_setting_is_not_defined_by_project_settings():
    from config import settings as project_settings

    assert not hasattr(project_settings, contract.TEST_DOUBLES_SETTING)


def test_test_double_rejected_without_opt_in(settings):
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = False
    with pytest.raises(UntrustedSourceError):
        source_origin(FakeBrokerProviderForTests(), contract.TRUSTED_PROVIDER_TYPES)


@pytest.mark.parametrize("value", [1, "true", "True", None])
def test_opt_in_must_be_exactly_true(settings, value):
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = value
    with pytest.raises(UntrustedSourceError):
        source_origin(FakeBrokerProviderForTests(), contract.TRUSTED_PROVIDER_TYPES)


def test_test_double_accepted_only_with_opt_in_and_marked(settings):
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = True
    assert (
        source_origin(FakeBrokerProviderForTests(), contract.TRUSTED_PROVIDER_TYPES)
        is ConfirmationOrigin.TEST_DOUBLE
    )
    for untrusted in (UnmarkedProviderForTests(), object()):
        with pytest.raises(UntrustedSourceError):
            source_origin(untrusted, contract.TRUSTED_PROVIDER_TYPES)


def test_registered_type_is_classified_as_provider():
    assert (
        source_origin(UnmarkedProviderForTests(), frozenset({UnmarkedProviderForTests}))
        is ConfirmationOrigin.PROVIDER
    )


def test_origin_values_match_model_choices():
    assert {o.value for o in ConfirmationOrigin} == set(BrokerConfirmationOrigin.values)


# --- Huella de dominio ---------------------------------------------------------


def test_broker_fingerprint_is_deterministic_bound_and_domain_separated():
    secret = fake_broker_secret()
    first = crypto.broker_secret_fingerprint(UID, secret)
    assert first == crypto.broker_secret_fingerprint(UID, secret)
    assert crypto.is_well_formed_fingerprint(first)
    assert first != crypto.broker_secret_fingerprint("GW-BBBBBBBBBB", secret)
    assert secret not in first
    gws = crypto.generate_secret()
    assert crypto.fingerprint(UID, gws) != crypto.broker_secret_fingerprint(
        UID, (gws + "x" * 64)[:64]
    )


@pytest.mark.parametrize(
    "secret",
    ["a" * 63, "a" * 65, "a" * 63 + " ", "a" * 63 + "\n", "a" * 64 + "\n", "ñ" * 64, None, 64],
)
def test_broker_fingerprint_rejects_malformed_secrets(secret):
    with pytest.raises(crypto.InvalidGatewayCredential) as excinfo:
        crypto.broker_secret_fingerprint(UID, secret)
    assert str(excinfo.value) == crypto.INVALID_CREDENTIAL_MESSAGE


def test_broker_fingerprint_rejects_unknown_key_version():
    with pytest.raises(crypto.UnsupportedKeyVersion):
        crypto.broker_secret_fingerprint(UID, fake_broker_secret(), key_version=2)


# --- Serializador y auditoría --------------------------------------------------


SENSITIVE_FIELDS = {
    "secret_fingerprint",
    "confirmation_ref",
    "activation_evidence_ref",
    "correlation_id",
    "confirmed_process_id",
    "activated_process_id",
    "key_version",
    "created_by",
    "activated_by",
    "revoked_by",
}


def _unsaved_credential():
    gateway = Gateway(uid=UID, name="G")
    return BrokerCredential(
        gateway=gateway,
        version=3,
        correlation_id=uuid4(),
        state=BrokerCredentialState.ACTIVE,
        created_at=datetime.now(UTC),
        confirmation_ref=uuid4(),
        confirmation_origin=BrokerConfirmationOrigin.TEST_DOUBLE,
        confirmed_at=datetime.now(UTC),
        confirmed_process_id="worker:flespi",
        secret_fingerprint=FINGERPRINT,
        key_version=1,
        activated_at=datetime.now(UTC),
        activated_process_id="worker:flespi",
        activation_evidence_ref=uuid4(),
    )


def test_serializer_exposes_only_public_metadata():
    assert SENSITIVE_FIELDS.isdisjoint(BrokerCredentialSerializer.Meta.fields)
    credential = _unsaved_credential()
    data = BrokerCredentialSerializer(credential).data
    assert set(data) == set(BrokerCredentialSerializer.Meta.fields)
    text = repr(dict(data))
    for value in (
        FINGERPRINT,
        str(credential.confirmation_ref),
        str(credential.correlation_id),
        str(credential.activation_evidence_ref),
        "worker:flespi",
    ):
        assert value not in text


def test_audit_keys_pass_the_sensitive_key_filter():
    snapshot = service._snapshot(_unsaved_credential())
    assert _clean_payload(snapshot, "after_data") == snapshot
    metadata = {
        "cause": "rotated",
        "replaced_by_bcred_id": 1,
        "mode": "rotation",
        "previous_bcred_id": 1,
        "evidence_ref": str(uuid4()),
        "evidence_method": "test_double",
        "evidence_origin": "test_double",
        "process_id": "worker:flespi",
        "origin": "test_double",
        "confirmation_ref": str(uuid4()),
        "activated": True,
    }
    assert _clean_payload(metadata, "metadata") == metadata
    assert "secret_fingerprint" not in snapshot and FINGERPRINT not in repr(snapshot)


def test_broker_event_types_exist_and_fit():
    names = {
        EventType.IOT_BROKER_CREDENTIAL_REGISTERED: "iot_broker_credential_registered",
        EventType.IOT_BROKER_CREDENTIAL_CONFIRMED: "iot_broker_credential_confirmed",
        EventType.IOT_BROKER_CREDENTIAL_ACTIVATED: "iot_broker_credential_activated",
        EventType.IOT_BROKER_CREDENTIAL_REVOKED: "iot_broker_credential_revoked",
    }
    for member, value in names.items():
        assert member.value == value
        assert len(value) <= 48


# --- Validación de entrada del proceso -------------------------------------------


@pytest.mark.parametrize(
    "process_id", ["", "   ", "a" * 101, "con espacio", "tab\tx", "x\ny", "-inicio", None, 7]
)
def test_invalid_process_ids_are_rejected(process_id):
    with pytest.raises(AppError) as excinfo:
        service._validate_process_id(process_id)
    assert excinfo.value.status_code == 400


def test_valid_process_id_is_normalized():
    assert service._validate_process_id("  worker:flespi-01  ") == "worker:flespi-01"


def test_confirmation_window_constant_is_bounded():
    assert timedelta(0) < service.CLOCK_SKEW <= timedelta(minutes=10)
