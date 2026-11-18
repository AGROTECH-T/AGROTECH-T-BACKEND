# ruff: noqa: S101, S105, S106
"""Ciclo de vida de las credenciales de broker sobre MariaDB (FLESPI-01).

Propósito: registro, confirmación por proveedor confiable, coexistencia durante
la rotación, finalización protegida por evidencia, abandono, revocación,
cascada al desactivar el gateway, restricciones de MariaDB, auditoría,
atomicidad, ausencia de secretos, concurrencia y barrera de reversión de 0021.
Contexto: solo mediante el orquestador desechable IoT. El proveedor y el
verificador son dobles de prueba (``apps.iot.tests.fakes``); no hay red.
"""

import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.farms.services import deactivate_farm
from apps.iot import gateway_credentials as crypto
from apps.iot import services as iot
from apps.iot.api.serializers import BrokerCredentialSerializer
from apps.iot.brokers.provider import (
    ProviderConfirmation,
    ProviderOutcome,
    ProviderRejected,
    ProviderStatusResult,
    ProviderUnavailable,
)
from apps.iot.models import (
    BrokerCredential,
    BrokerCredentialRevocationCause,
    BrokerCredentialState,
    Gateway,
    GatewayCredential,
)
from apps.iot.services import broker_credentials as service
from apps.iot.services.access import IOT_MANAGE_FORBIDDEN
from apps.iot.tests.fakes.broker_provider import (
    FakeBrokerProviderForTests,
    FakeRotationEvidenceVerifierForTests,
    UnmarkedProviderForTests,
    UnmarkedVerifierForTests,
)
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db

PROCESS = "worker:flespi-01"
State = BrokerCredentialState
Cause = BrokerCredentialRevocationCause


@pytest.fixture(autouse=True)
def key(settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)


@pytest.fixture(autouse=True)
def test_doubles(settings):
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = True


@pytest.fixture
def gateway(admin, farm_id):
    return iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway norte")


@pytest.fixture
def provider():
    return FakeBrokerProviderForTests()


@pytest.fixture
def verifier():
    return FakeRotationEvidenceVerifierForTests()


def _register(admin, farm_id, gateway):
    return iot.register_pending_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid
    )


def _confirm(provider, gateway, credential, process=PROCESS):
    return iot.confirm_broker_credential(
        provider=provider,
        gateway_uid=gateway.uid,
        correlation_id=credential.correlation_id,
        process_id=process,
    )


def _finalize(admin, farm_id, gateway, verifier, reason="gateway verificado"):
    return iot.finalize_broker_rotation(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, verifier=verifier, reason=reason
    )


def _active_credential(admin, farm_id, gateway):
    """Primera credencial registrada y activada (sin otra activa)."""
    credential = _register(admin, farm_id, gateway)
    result = _confirm(FakeBrokerProviderForTests(), gateway, credential)
    assert result.activated
    return result.credential


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status
    return excinfo.value


def _events(event_type, credential=None):
    rows = AuditEvent.objects.filter(event_type=event_type)
    if credential is not None:
        rows = rows.filter(entity_type="iot_broker_credential", entity_id=str(credential.pk))
    return rows


def _fresh(credential):
    return BrokerCredential.objects.get(pk=credential.pk)


# --- Registro ------------------------------------------------------------------


def test_register_creates_pending_first_version(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    row = _fresh(credential)
    assert row.state == State.PENDING_BROKER
    assert (row.version, row.pending_slot, row.active_slot) == (1, 1, None)
    assert row.gateway_id == gateway.pk and row.provider == "flespi"
    assert row.created_by_id == admin.pk
    assert not row.is_confirmed and row.secret_fingerprint is None
    event = _events(EventType.IOT_BROKER_CREDENTIAL_REGISTERED, row).get()
    assert event.farm_id == farm_id and event.actor_id == admin.pk
    assert event.after_data["state"] == State.PENDING_BROKER
    assert event.after_data["bcred_id"] == row.pk


def test_register_requires_farm_administrator(admin, farm_id, gateway, operator, viewer):
    for user in (operator, viewer):
        error = _error(lambda user=user: _register(user, farm_id, gateway), 403)
        assert error.message == IOT_MANAGE_FORBIDDEN
    assert not BrokerCredential.objects.exists()


def test_register_is_isolated_by_farm(admin, farm_id, gateway, other_admin, other_farm_id):
    with pytest.raises(AppError):
        _register(other_admin, farm_id, gateway)
    _error(
        lambda: iot.register_pending_broker_credential(
            user=other_admin, farm_id=other_farm_id, gateway_uid=gateway.uid
        ),
        404,
    )
    _error(
        lambda: iot.register_pending_broker_credential(
            user=admin, farm_id=farm_id, gateway_uid="no-es-un-uid"
        ),
        404,
    )
    assert not BrokerCredential.objects.exists()


def test_register_rejects_inactive_gateway(admin, farm_id, gateway):
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    _error(lambda: _register(admin, farm_id, gateway), 409)


def test_second_pending_is_rejected(admin, farm_id, gateway):
    _register(admin, farm_id, gateway)
    error = _error(lambda: _register(admin, farm_id, gateway), 409)
    assert error.message == service.ALREADY_PENDING
    assert BrokerCredential.objects.filter(gateway=gateway).count() == 1


# --- Confirmación --------------------------------------------------------------


def test_confirm_without_active_activates_first_credential(admin, farm_id, gateway, provider):
    credential = _register(admin, farm_id, gateway)
    result = _confirm(provider, gateway, credential)
    row = _fresh(credential)
    assert result.activated and result.origin == "test_double"
    assert row.state == State.ACTIVE
    assert (row.pending_slot, row.active_slot) == (None, 1)
    assert row.confirmation_origin == "test_double"
    assert row.confirmed_process_id == PROCESS and row.activated_process_id == PROCESS
    assert row.activated_by_id is None and row.activation_evidence_ref is None
    secret = provider.secrets_issued[credential.correlation_id]
    assert row.secret_fingerprint == crypto.broker_secret_fingerprint(gateway.uid, secret)
    confirmed = _events(EventType.IOT_BROKER_CREDENTIAL_CONFIRMED, row).get()
    assert confirmed.actor_id is None and confirmed.farm_id == farm_id
    assert confirmed.metadata["process_id"] == PROCESS
    assert confirmed.metadata["origin"] == "test_double"
    assert confirmed.metadata["activated"] is True
    activated = _events(EventType.IOT_BROKER_CREDENTIAL_ACTIVATED, row).get()
    assert activated.metadata == {"mode": "initial", "process_id": PROCESS}
    assert iot.get_active_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid
    ).pk == row.pk


def test_untrusted_providers_cannot_confirm(admin, farm_id, gateway, settings):
    credential = _register(admin, farm_id, gateway)
    unmarked = UnmarkedProviderForTests()
    _error(lambda: _confirm(unmarked, gateway, credential), 403)
    assert unmarked.calls == []
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = False
    fake = FakeBrokerProviderForTests()
    _error(lambda: _confirm(fake, gateway, credential), 403)
    assert fake.calls == []
    _error(lambda: _confirm(object(), gateway, credential), 403)
    assert _fresh(credential).state == State.PENDING_BROKER
    assert not _fresh(credential).is_confirmed
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_CONFIRMED).exists()


def test_provider_name_must_match(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    other_name = FakeBrokerProviderForTests(provider_name="otro")
    _error(lambda: _confirm(other_name, gateway, credential), 403)
    assert other_name.calls == []


def test_confirm_rejects_unknown_or_foreign_credential(admin, farm_id, gateway, provider):
    credential = _register(admin, farm_id, gateway)
    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    _error(lambda: _confirm(provider, other, credential), 404)
    _error(
        lambda: iot.confirm_broker_credential(
            provider=provider,
            gateway_uid=gateway.uid,
            correlation_id=str(credential.correlation_id),
            process_id=PROCESS,
        ),
        404,
    )
    _error(
        lambda: iot.confirm_broker_credential(
            provider=provider, gateway_uid=gateway.uid, correlation_id=uuid4(), process_id=PROCESS
        ),
        404,
    )
    _error(lambda: _confirm(provider, gateway, credential, process="mal proceso"), 400)
    assert provider.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [(ProviderUnavailable(), 503), (ProviderRejected(), 502), (RuntimeError("x"), 502)],
)
def test_provider_errors_are_structured(admin, farm_id, gateway, error, status):
    credential = _register(admin, farm_id, gateway)
    message = _error(
        lambda: _confirm(FakeBrokerProviderForTests(error=error), gateway, credential), status
    ).message
    assert message in {service.PROVIDER_UNAVAILABLE, service.PROVIDER_FAILURE}
    assert not _fresh(credential).is_confirmed


@pytest.mark.parametrize("outcome", [ProviderOutcome.DISABLED, ProviderOutcome.NOT_FOUND])
def test_not_enabled_is_not_a_confirmation(admin, farm_id, gateway, outcome):
    credential = _register(admin, farm_id, gateway)
    error = _error(
        lambda: _confirm(FakeBrokerProviderForTests(outcome=outcome), gateway, credential), 409
    )
    assert error.message == service.NOT_CONFIRMED_BY_PROVIDER
    assert not _fresh(credential).is_confirmed


def test_invalid_confirmations_are_rejected(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    now = timezone.now()
    for fake in (
        FakeBrokerProviderForTests(observed_at=credential.created_at - timedelta(seconds=1)),
        FakeBrokerProviderForTests(observed_at=now + timedelta(hours=1)),
        FakeBrokerProviderForTests(status_override="no es un resultado"),
    ):
        _error(lambda fake=fake: _confirm(fake, gateway, credential), 502)
    foreign = uuid4()
    mismatched = ProviderStatusResult(
        correlation_id=foreign,
        outcome=ProviderOutcome.ENABLED,
        confirmation=ProviderConfirmation(
            provider="flespi",
            correlation_id=foreign,
            outcome=ProviderOutcome.ENABLED,
            confirmation_id=uuid4(),
            observed_at=now,
            secret_fingerprint="b" * 64,
            key_version=1,
        ),
    )
    wrong = FakeBrokerProviderForTests(status_override=mismatched)
    _error(lambda: _confirm(wrong, gateway, credential), 502)
    assert not _fresh(credential).is_confirmed
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_CONFIRMED).exists()


def test_repeated_and_reused_confirmations_are_rejected(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    shared_id = uuid4()
    _confirm(FakeBrokerProviderForTests(confirmation_id=shared_id), gateway, credential)
    again = FakeBrokerProviderForTests()
    _error(lambda: _confirm(again, gateway, credential), 409)
    assert again.calls == []  # rechazada antes de consultar al proveedor

    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    second = _register(admin, farm_id, other)
    error = _error(
        lambda: _confirm(FakeBrokerProviderForTests(confirmation_id=shared_id), other, second), 409
    )
    assert error.message == service.CONFIRMATION_REUSED
    reused_fp = _fresh(credential).secret_fingerprint
    _error(
        lambda: _confirm(FakeBrokerProviderForTests(secret_fingerprint=reused_fp), other, second),
        409,
    )
    assert not _fresh(second).is_confirmed


def test_confirm_requires_active_farm_and_gateway(admin, farm_id, gateway, provider):
    credential = _register(admin, farm_id, gateway)
    deactivate_farm(user=admin, farm_id=farm_id)
    _error(lambda: _confirm(provider, gateway, credential), 409)
    assert not _fresh(credential).is_confirmed


# --- Coexistencia y rotación ---------------------------------------------------


def test_confirmation_during_rotation_does_not_activate_or_revoke(admin, farm_id, gateway):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    assert _fresh(old).state == State.ACTIVE  # pendiente y activa coexisten
    result = _confirm(FakeBrokerProviderForTests(), gateway, new)
    assert result.activated is False
    assert _fresh(old).state == State.ACTIVE
    row = _fresh(new)
    assert row.state == State.PENDING_BROKER and row.is_confirmed
    assert row.activated_at is None
    confirmed = _events(EventType.IOT_BROKER_CREDENTIAL_CONFIRMED, row).get()
    assert confirmed.metadata["activated"] is False
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_ACTIVATED, row).exists()
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED).exists()
    _error(lambda: _register(admin, farm_id, gateway), 409)


def test_finalize_rotation_with_evidence_revokes_previous_after_verification(
    admin, farm_id, gateway, verifier
):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    assert _fresh(old).state == State.ACTIVE  # sigue activa hasta verificar la nueva

    activated = _finalize(admin, farm_id, gateway, verifier)

    assert verifier.calls == [(gateway.uid, new.correlation_id)]
    previous, current = _fresh(old), _fresh(activated)
    assert previous.state == State.REVOKED and previous.revocation_cause == Cause.ROTATED
    assert previous.revoked_by_id == admin.pk
    assert current.pk == new.pk and current.state == State.ACTIVE
    assert current.activated_by_id == admin.pk and current.activation_evidence_ref is not None
    revoked = _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED, previous).get()
    assert revoked.metadata == {"cause": "rotated", "replaced_by_bcred_id": current.pk}
    event = _events(EventType.IOT_BROKER_CREDENTIAL_ACTIVATED, current).get()
    assert event.metadata["mode"] == "rotation"
    assert event.metadata["previous_bcred_id"] == previous.pk
    assert event.metadata["evidence_ref"] == str(current.activation_evidence_ref)
    assert event.metadata["evidence_origin"] == "test_double"
    assert event.reason == "gateway verificado"
    history = iot.list_broker_credentials(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    assert [row.version for row in history] == [2, 1]


def test_finalize_without_trusted_evidence_keeps_previous_active(admin, farm_id, gateway, settings):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)

    unmarked = UnmarkedVerifierForTests()
    assert _error(lambda: _finalize(admin, farm_id, gateway, unmarked), 409).message == (
        service.EVIDENCE_UNAVAILABLE
    )
    assert unmarked.calls == []
    _error(lambda: _finalize(admin, farm_id, gateway, object()), 409)
    for bad in (
        FakeRotationEvidenceVerifierForTests(has_evidence=False),
        FakeRotationEvidenceVerifierForTests(correlation_override=uuid4()),
        FakeRotationEvidenceVerifierForTests(gateway_uid_override="GW-ZZZZZZZZZZ"),
        FakeRotationEvidenceVerifierForTests(
            observed_at=_fresh(new).confirmed_at - timedelta(seconds=1)
        ),
        FakeRotationEvidenceVerifierForTests(observed_at=timezone.now() + timedelta(hours=1)),
        FakeRotationEvidenceVerifierForTests(error=RuntimeError("caído")),
    ):
        _error(lambda bad=bad: _finalize(admin, farm_id, gateway, bad), 409)
    settings.IOT_BROKER_TEST_DOUBLES_ALLOWED = False
    _error(lambda: _finalize(admin, farm_id, gateway, FakeRotationEvidenceVerifierForTests()), 409)

    assert _fresh(old).state == State.ACTIVE
    assert _fresh(new).state == State.PENDING_BROKER
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED).exists()


def test_finalize_preconditions(admin, farm_id, gateway, verifier, operator):
    _error(lambda: _finalize(admin, farm_id, gateway, verifier), 409)  # sin pendiente
    _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    error = _error(lambda: _finalize(admin, farm_id, gateway, verifier), 409)
    assert error.message == service.ROTATION_NOT_CONFIRMED
    assert verifier.calls == []
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    _error(lambda: _finalize(operator, farm_id, gateway, verifier), 403)
    _error(lambda: _finalize(admin, farm_id, gateway, verifier, reason=""), 400)
    assert verifier.calls == []


def test_evidence_cannot_be_reused(admin, farm_id, gateway):
    shared = uuid4()
    _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    _finalize(admin, farm_id, gateway, FakeRotationEvidenceVerifierForTests(evidence_ref=shared))

    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    old_other = _active_credential(admin, farm_id, other)
    new_other = _register(admin, farm_id, other)
    _confirm(FakeBrokerProviderForTests(), other, new_other)
    error = _error(
        lambda: _finalize(
            admin, farm_id, other, FakeRotationEvidenceVerifierForTests(evidence_ref=shared)
        ),
        409,
    )
    assert error.message == service.EVIDENCE_REUSED
    assert _fresh(old_other).state == State.ACTIVE


def test_finalize_without_active_activates_confirmed_pending(admin, farm_id, gateway, verifier):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    iot.revoke_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, version=old.version,
        cause=Cause.COMPROMISED,
    )
    assert _fresh(new).state == State.PENDING_BROKER  # revocar la activa no activa nada
    activated = _finalize(admin, farm_id, gateway, verifier)
    assert _fresh(activated).state == State.ACTIVE
    event = _events(EventType.IOT_BROKER_CREDENTIAL_ACTIVATED, activated).get()
    assert event.metadata["previous_bcred_id"] is None


def test_abandoned_rotation_keeps_active(admin, farm_id, gateway):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    revoked = iot.revoke_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, version=new.version,
        cause=Cause.ABANDONED, reason="rotación cancelada",
    )
    assert revoked.state == State.REVOKED and revoked.revocation_cause == Cause.ABANDONED
    assert _fresh(old).state == State.ACTIVE
    event = _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED, revoked).get()
    assert event.metadata == {"cause": "abandoned"} and event.reason == "rotación cancelada"
    third = _register(admin, farm_id, gateway)  # se puede reintentar con una versión nueva
    assert third.version == 3


# --- Revocación ----------------------------------------------------------------


def test_manual_revocation_rules(admin, farm_id, gateway, operator):
    active = _active_credential(admin, farm_id, gateway)

    def revoke(cause, version=active.version, user=admin):
        return iot.revoke_broker_credential(
            user=user, farm_id=farm_id, gateway_uid=gateway.uid, version=version, cause=cause
        )

    _error(lambda: revoke(Cause.ABANDONED), 400)  # solo para pendientes
    _error(lambda: revoke(Cause.ROTATED), 400)
    _error(lambda: revoke(Cause.GATEWAY_DEACTIVATED), 400)
    _error(lambda: revoke("otra"), 400)
    _error(lambda: revoke(Cause.ADMIN, version=0), 400)
    _error(lambda: revoke(Cause.ADMIN, version="1"), 400)
    _error(lambda: revoke(Cause.ADMIN, version=9), 404)
    _error(lambda: revoke(Cause.ADMIN, user=operator), 403)
    revoked = revoke(Cause.ADMIN)
    assert revoked.state == State.REVOKED and revoked.revoked_by_id == admin.pk
    _error(lambda: revoke(Cause.ADMIN), 409)
    assert iot.get_active_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid
    ) is None


def test_revoked_credential_never_returns(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    iot.revoke_broker_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, version=1, cause=Cause.ADMIN
    )
    _error(lambda: _confirm(FakeBrokerProviderForTests(), gateway, credential), 409)
    with pytest.raises(IntegrityError), transaction.atomic():
        BrokerCredential.objects.filter(pk=credential.pk).update(
            state=State.ACTIVE, active_slot=1, activated_at=timezone.now()
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        BrokerCredential.objects.filter(pk=credential.pk).update(
            state=State.PENDING_BROKER, pending_slot=1
        )
    assert _fresh(credential).state == State.REVOKED


def test_gateway_deactivation_cascades_to_pending_and_active(admin, farm_id, gateway):
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    active = _active_credential(admin, farm_id, gateway)
    pending = _register(admin, farm_id, gateway)

    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="averiado")

    for row in (_fresh(active), _fresh(pending)):
        assert row.state == State.REVOKED
        assert row.revocation_cause == Cause.GATEWAY_DEACTIVATED
        event = _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED, row).get()
        assert event.metadata == {"cause": "gateway_deactivated"} and event.reason == "averiado"
    # El evento de desactivación conserva exactamente su contrato anterior.
    deactivated = AuditEvent.objects.get(event_type=EventType.IOT_GATEWAY_DEACTIVATED)
    assert deactivated.metadata == {"revoked_authorizations": 0, "revoked_gwcreds": 1}
    assert not GatewayCredential.objects.filter(gateway=gateway, active_slot=1).exists()

    iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="reparado")
    assert not BrokerCredential.objects.filter(gateway=gateway).exclude(
        state=State.REVOKED
    ).exists()
    assert BrokerCredential.objects.filter(gateway=gateway).count() == 2  # historial intacto


# --- Lectura -------------------------------------------------------------------


def test_history_is_readable_by_members_only(admin, farm_id, gateway, viewer, other_admin):
    _active_credential(admin, farm_id, gateway)
    rows = iot.list_broker_credentials(user=viewer, farm_id=farm_id, gateway_uid=gateway.uid)
    assert [row.version for row in rows] == [1]
    with pytest.raises(AppError):
        iot.list_broker_credentials(user=other_admin, farm_id=farm_id, gateway_uid=gateway.uid)
    _error(
        lambda: iot.get_active_broker_credential(
            user=admin, farm_id=farm_id, gateway_uid="GW-ZZZZZZZZZZ"
        ),
        404,
    )


# --- Restricciones de MariaDB --------------------------------------------------


def _row(gateway, **fields):
    values = {
        "gateway": gateway,
        "version": 1,
        "correlation_id": uuid4(),
        "state": State.PENDING_BROKER,
        "pending_slot": 1,
        # En el pasado: así el CHECK de fechas no interfiere con el caso probado.
        "created_at": timezone.now() - timedelta(days=1),
    }
    values.update(fields)
    return BrokerCredential(**values)


def _active_fields():
    confirmed = _confirmed_fields()
    return {
        **confirmed,
        "state": State.ACTIVE,
        "pending_slot": None,
        "active_slot": 1,
        "activated_at": confirmed["confirmed_at"],
    }


def _confirmed_fields():
    return {
        "confirmation_ref": uuid4(),
        "confirmation_origin": "test_double",
        "confirmed_at": timezone.now(),
        "confirmed_process_id": PROCESS,
        "secret_fingerprint": secrets.token_hex(32),
        "key_version": 1,
    }


ACTIVE_SLOTS = {"state": State.ACTIVE, "pending_slot": None, "active_slot": 1}
REVOKED_SLOTS = {"state": State.REVOKED, "pending_slot": None}


@pytest.mark.parametrize(
    "fields",
    [
        {"provider": "mosquitto"},
        {"version": 0},
        {"pending_slot": None},
        {"pending_slot": 2},
        {"active_slot": 1},
        {"revocation_cause": "admin"},
        {"secret_fingerprint": "a" * 64},  # confirmación parcial
        {**_confirmed_fields(), "secret_fingerprint": "A" * 64},
        {**_confirmed_fields(), "confirmation_origin": "otro"},
        # activa sin confirmación
        {**ACTIVE_SLOTS, "activated_at": timezone.now()},
        # revocada sin fecha, sin causa, o ``rotated`` sin haber estado activa
        {**REVOKED_SLOTS, "revoked_at": None, "revocation_cause": "admin"},
        {**REVOKED_SLOTS, "revoked_at": timezone.now(), "revocation_cause": ""},
        {**REVOKED_SLOTS, "revoked_at": timezone.now(), "revocation_cause": "rotated"},
        {"state": "otro"},
    ],
)
def test_check_constraints_reject_inconsistent_rows(gateway, fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(gateway, **fields).save()


def test_unique_slots_versions_and_protect(admin, farm_id, gateway):
    _row(gateway).save()
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(gateway, version=2).save()  # segunda pendiente
    _row(gateway, version=2, **_active_fields()).save()  # pendiente + activa: válido
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(gateway, version=3, **_active_fields()).save()  # segunda activa
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(gateway, version=1, pending_slot=None, state=State.REVOKED,
             revoked_at=timezone.now(), revocation_cause="admin").save()  # versión repetida
    with pytest.raises(ProtectedError):
        Gateway.objects.filter(pk=gateway.pk).delete()


# --- Atomicidad, secretos y auditoría --------------------------------------------


def test_finalize_is_atomic_when_audit_fails(admin, farm_id, gateway, verifier, monkeypatch):
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    original = service.record_audit_event

    def failing(**kwargs):
        if kwargs["event_type"] == EventType.IOT_BROKER_CREDENTIAL_ACTIVATED:
            raise RuntimeError("auditoría caída")
        return original(**kwargs)

    monkeypatch.setattr(service, "record_audit_event", failing)
    with pytest.raises(RuntimeError, match="auditoría caída"):
        _finalize(admin, farm_id, gateway, verifier)
    assert _fresh(old).state == State.ACTIVE
    assert _fresh(new).state == State.PENDING_BROKER
    assert not _events(EventType.IOT_BROKER_CREDENTIAL_REVOKED).exists()


def test_confirm_is_atomic_when_audit_fails(admin, farm_id, gateway, monkeypatch):
    credential = _register(admin, farm_id, gateway)

    def failing(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(service, "record_audit_event", failing)
    with pytest.raises(RuntimeError):
        _confirm(FakeBrokerProviderForTests(), gateway, credential)
    row = _fresh(credential)
    assert row.state == State.PENDING_BROKER and not row.is_confirmed


def test_no_secret_in_logs_audit_or_serializer(admin, farm_id, gateway, caplog):
    caplog.set_level(logging.DEBUG)
    provider = FakeBrokerProviderForTests()
    old = _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(provider, gateway, new)
    _finalize(admin, farm_id, gateway, FakeRotationEvidenceVerifierForTests())
    _error(
        lambda: _confirm(FakeBrokerProviderForTests(error=ProviderUnavailable()), gateway, new),
        409,
    )
    rows = list(BrokerCredential.objects.filter(gateway=gateway))
    forbidden = set(provider.secrets_issued.values())
    for row in rows:
        forbidden |= {row.secret_fingerprint, str(row.confirmation_ref)}
    forbidden.discard(None)
    audit = json.dumps(
        list(
            AuditEvent.objects.filter(entity_type="iot_broker_credential").values(
                "before_data", "after_data", "metadata", "reason"
            )
        ),
        default=str,
    )
    serialized = json.dumps(BrokerCredentialSerializer(rows, many=True).data, default=str)
    logs = caplog.text
    secrets_and_fingerprints = forbidden - {str(r.confirmation_ref) for r in rows}
    for value in secrets_and_fingerprints:
        assert value not in logs and value not in audit and value not in serialized
    for row in rows:
        assert str(row.confirmation_ref) not in logs and str(row.confirmation_ref) not in serialized
    # Ninguna columna guarda un secreto emitido por el proveedor.
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM iot_broker_credentials")
        stored = json.dumps(cursor.fetchall(), default=str)
    for secret in provider.secrets_issued.values():
        assert secret not in stored
    assert old.pk != new.pk


def test_flespi01_migrations_match_models():
    """0021 y trazabilidad.0013 coinciden con los modelos (sin escribir archivos).

    Solo se evalúan ``BrokerCredential`` y ``trazabilidad``: el autodetector
    reporta además diferencias de Meta/CHECK preexistentes en otros modelos IoT
    (FrameConflict, ReceivedFrame, IotMqttDeliveryPause, MeasurementCorrection,
    SensorSource) que no pertenecen a FLESPI-01.
    """
    from django.apps import apps
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(None, ignore_no_migrations=True)
    changes = MigrationAutodetector(
        loader.project_state(), ProjectState.from_apps(apps)
    ).changes(graph=loader.graph)
    operations = [
        (app, operation)
        for app, migrations in changes.items()
        for migration in migrations
        for operation in migration.operations
    ]
    assert [op for app, op in operations if app == "trazabilidad"] == []
    broker = [
        op
        for app, op in operations
        if app == "iot"
        and str(getattr(op, "model_name", None) or getattr(op, "name", "")).lower()
        == "brokercredential"
    ]
    assert broker == []


# --- Concurrencia (transacciones reales) ---------------------------------------


def _parallel(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        try:
            barrier.wait(timeout=5)
            return operation()
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures = [pool.submit(run, op) for op in operations]
        return [f.result(timeout=30) for f in futures]


def _open_rows(gateway):
    return BrokerCredential.objects.filter(gateway=gateway).exclude(state=State.REVOKED)


@pytest.mark.django_db(transaction=True)
def test_parallel_registrations_leave_one_pending(admin, farm_id, gateway):
    results = _parallel(*(lambda: _register(admin, farm_id, gateway) for _ in range(2)))
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert _open_rows(gateway).count() == 1


@pytest.mark.django_db(transaction=True)
def test_parallel_confirmations_confirm_once(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    results = _parallel(
        *(lambda: _confirm(FakeBrokerProviderForTests(), gateway, credential) for _ in range(2))
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert _events(EventType.IOT_BROKER_CREDENTIAL_CONFIRMED).count() == 1
    assert _fresh(credential).state == State.ACTIVE


@pytest.mark.django_db(transaction=True)
def test_finalize_racing_deactivation_leaves_no_open_credential(admin, farm_id, gateway):
    _active_credential(admin, farm_id, gateway)
    new = _register(admin, farm_id, gateway)
    _confirm(FakeBrokerProviderForTests(), gateway, new)
    results = _parallel(
        lambda: _finalize(admin, farm_id, gateway, FakeRotationEvidenceVerifierForTests()),
        lambda: iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid),
    )
    assert not isinstance(results[1], AppError)
    if isinstance(results[0], AppError):
        assert results[0].status_code == 409  # la desactivación ganó
    assert not _open_rows(gateway).exists()


@pytest.mark.django_db(transaction=True)
def test_confirm_racing_deactivation_leaves_no_open_credential(admin, farm_id, gateway):
    credential = _register(admin, farm_id, gateway)
    results = _parallel(
        lambda: _confirm(FakeBrokerProviderForTests(), gateway, credential),
        lambda: iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid),
    )
    assert not isinstance(results[1], AppError)
    if isinstance(results[0], AppError):
        assert results[0].status_code == 409
    assert not _open_rows(gateway).exists()


# --- Barrera de reversión de 0021 ---------------------------------------------

IOT_0020 = ("iot", "0020_broker_identity_overflow_evidence")
IOT_0021 = ("iot", "0021_broker_credential")


def _executor():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _restore_leaves():
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _has_table():
    with connection.cursor() as cursor:
        return "iot_broker_credentials" in connection.introspection.table_names(cursor)


@pytest.mark.django_db(transaction=True)
def test_0021_rollback_is_refused_by_default(settings):
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = False
    try:
        with pytest.raises(IrreversibleError, match="iot.0021"):
            _executor().migrate([IOT_0020])
        assert _has_table()
    finally:
        _restore_leaves()


@pytest.mark.django_db(transaction=True)
def test_0021_explicit_rollback_and_forward(settings):
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = True
    try:
        _executor().migrate([IOT_0020])
        assert not _has_table()
        _executor().migrate([IOT_0021])
        assert _has_table()
    finally:
        _restore_leaves()
