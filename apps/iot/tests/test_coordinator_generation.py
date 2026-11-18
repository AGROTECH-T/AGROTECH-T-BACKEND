# ruff: noqa: S101
"""Generaciones de credenciales de coordinador (IoT-04.3-C1).

Propósito: primera emisión, rotaciones, reemisión, legado sin contador,
fail-closed, comparación de generación tras el HMAC, agotamiento uint16,
restricciones de MariaDB, atomicidad y auditoría.
Contexto: solo mediante el orquestador desechable.
"""

import base64
import secrets

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.utils import timezone

from apps.iot import coordinator_credentials as crypto
from apps.iot import services as iot
from apps.iot.models import (
    COORDINATOR_KEY_VERSION_MAX,
    CoordinatorCredential,
    CoordinatorKeyGeneration,
    CoordinatorRevocationCause,
)
from apps.iot.services import coordinator_credentials as coordinator_services
from apps.iot.services.coordinator_credentials import (
    CoordinatorGenerationExhausted,
    CoordinatorGenerationInconsistent,
    LegacyGenerationUnresolved,
)
from apps.iot.tests import lora_vectors
from apps.trazabilidad.models import AuditEvent, EventType
from config.coordinator_key_encryption_key import load_master_key_ring

pytestmark = pytest.mark.django_db

CONTEXT = b"contexto-de-prueba"
EXHAUSTED_EVENT = EventType.IOT_COORDINATOR_CREDENTIAL_GENERATION_EXHAUSTED


def _master_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture(autouse=True)
def master_key(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = _master_key()
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""


@pytest.fixture
def coordinator(admin, farm_id):
    return iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador norte")


def _issue(admin, farm_id, coordinator):
    return iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )


def _rotate(admin, farm_id, coordinator, **kwargs):
    return iot.rotate_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, **kwargs
    )


def _revoke(admin, farm_id, coordinator, **kwargs):
    return iot.revoke_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, **kwargs
    )


def _active(coordinator):
    return CoordinatorCredential.objects.get(coordinator=coordinator, active_slot=1)


def _counter(coordinator) -> CoordinatorKeyGeneration:
    return CoordinatorKeyGeneration.objects.get(coordinator=coordinator)


def _frame(coordinator, key, *, key_version, boot=1, seq=1):
    signed = lora_vectors.signed_bytes(
        uid=coordinator.uid,
        key_version=key_version,
        boot_counter=boot,
        seq=seq,
    )
    return crypto.ParsedCoordinatorFrame(
        coordinator_uid=coordinator.uid,
        key_version=key_version,
        boot_counter=boot,
        seq=seq,
        signed_bytes=signed,
        tag=crypto.compute_lora_hmac(key, signed, context=CONTEXT),
    )


def _legacy_credential(coordinator, *, active=True):
    key = crypto.generate_coordinator_key()
    encrypted = crypto.encrypt_coordinator_key(
        key,
        coordinator_uid=coordinator.uid,
        key_ring=load_master_key_ring(),
    )
    now = timezone.now()
    credential = CoordinatorCredential.objects.create(
        coordinator=coordinator,
        ciphertext=encrypted.ciphertext,
        nonce=encrypted.nonce,
        master_key_version=encrypted.master_key_version,
        key_version=None,
        active_slot=1 if active else None,
        issued_at=now,
        revoked_at=None if active else now,
        revocation_cause=("" if active else CoordinatorRevocationCause.ADMIN),
    )
    return credential, key


def _revoked_credential(coordinator, key_version):
    now = timezone.now()
    return CoordinatorCredential.objects.create(
        coordinator=coordinator,
        ciphertext=bytes(48),
        nonce=bytes(12),
        master_key_version=1,
        key_version=key_version,
        active_slot=None,
        issued_at=now,
        revoked_at=now,
        revocation_cause=CoordinatorRevocationCause.ADMIN,
    )


# --- Contador y emisión ---


def test_first_issue_creates_counter_at_generation_one(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    counter = _counter(coordinator)
    assert issued.credential.key_version == 1
    assert counter.last_key_version == 1
    assert counter.updated_at is not None


def test_rotations_increment_generation_and_keep_counter_coherent(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    second = _rotate(admin, farm_id, coordinator)
    third = _rotate(admin, farm_id, coordinator)
    assert [
        first.credential.key_version,
        second.credential.key_version,
        third.credential.key_version,
    ] == [1, 2, 3]
    assert _active(coordinator).pk == third.credential.pk
    assert _counter(coordinator).last_key_version == 3


def test_revocation_does_not_reset_generation(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    _revoke(admin, farm_id, coordinator)
    assert _counter(coordinator).last_key_version == 1
    second = _issue(admin, farm_id, coordinator)
    assert second.credential.key_version == 2
    assert _counter(coordinator).last_key_version == 2
    first.credential.refresh_from_db()
    assert first.credential.key_version == 1


def test_reissue_increments_generation(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    second = _rotate(
        admin,
        farm_id,
        coordinator,
        cause=CoordinatorRevocationCause.REISSUED,
    )
    first.credential.refresh_from_db()
    assert first.credential.revocation_cause == CoordinatorRevocationCause.REISSUED
    assert second.credential.key_version == 2
    assert _counter(coordinator).last_key_version == 2


def test_master_key_version_is_not_used_as_generation(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    ciphertext = bytes(first.credential.ciphertext)
    nonce = bytes(first.credential.nonce)
    second = _rotate(admin, farm_id, coordinator)
    first.credential.refresh_from_db()
    assert first.credential.master_key_version == 1
    assert second.credential.master_key_version == 1
    assert first.credential.key_version == 1
    assert second.credential.key_version == 2
    assert bytes(first.credential.ciphertext) == ciphertext
    assert bytes(first.credential.nonce) == nonce


# --- Legado y fail-closed ---


def test_legacy_history_without_counter_blocks_issue(admin, farm_id, coordinator):
    _legacy_credential(coordinator, active=False)
    events = AuditEvent.objects.count()
    with pytest.raises(LegacyGenerationUnresolved):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorKeyGeneration.objects.exists()
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == 1
    assert not CoordinatorCredential.objects.filter(active_slot=1).exists()
    assert AuditEvent.objects.count() == events


def test_legacy_active_without_counter_blocks_rotation(admin, farm_id, coordinator):
    _legacy_credential(coordinator, active=True)
    events = AuditEvent.objects.count()
    with pytest.raises(LegacyGenerationUnresolved):
        _rotate(admin, farm_id, coordinator)
    assert not CoordinatorKeyGeneration.objects.exists()
    assert _active(coordinator).key_version is None
    assert AuditEvent.objects.count() == events


def test_legacy_active_with_null_generation_does_not_authenticate(admin, farm_id, coordinator):
    _, key = _legacy_credential(coordinator, active=True)
    assert (
        iot.verify_coordinator_frame(
            frame=_frame(coordinator, key, key_version=1), hmac_context=CONTEXT
        )
        is None
    )


def test_legacy_revoked_null_generation_is_allowed(admin, farm_id, coordinator):
    credential, _ = _legacy_credential(coordinator, active=False)
    credential.refresh_from_db()
    assert credential.key_version is None
    assert not credential.is_active


# --- Comparación de generación ---


def test_generation_is_compared_after_the_hmac(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    authenticated = iot.verify_coordinator_frame(
        frame=_frame(coordinator, first.key, key_version=1),
        hmac_context=CONTEXT,
    )
    assert authenticated is not None
    assert authenticated.credential_id == first.credential.pk
    assert authenticated.key_version == 1

    second = _rotate(admin, farm_id, coordinator)
    assert (
        iot.verify_coordinator_frame(
            frame=_frame(coordinator, second.key, key_version=1),
            hmac_context=CONTEXT,
        )
        is None
    )
    assert (
        iot.verify_coordinator_frame(
            frame=_frame(coordinator, second.key, key_version=3),
            hmac_context=CONTEXT,
        )
        is None
    )
    accepted = iot.verify_coordinator_frame(
        frame=_frame(coordinator, second.key, key_version=2),
        hmac_context=CONTEXT,
    )
    assert accepted is not None
    assert accepted.credential_id == second.credential.pk


def test_tampered_hmac_with_correct_generation_is_rejected(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    frame = _frame(coordinator, first.key, key_version=1)
    forged = crypto.ParsedCoordinatorFrame(
        coordinator_uid=frame.coordinator_uid,
        key_version=frame.key_version,
        boot_counter=frame.boot_counter,
        seq=frame.seq + 1,
        signed_bytes=lora_vectors.signed_bytes(
            uid=coordinator.uid, key_version=1, boot_counter=1, seq=2
        ),
        tag=frame.tag,
    )
    assert iot.verify_coordinator_frame(frame=forged, hmac_context=CONTEXT) is None


# --- Coherencia y restricciones ---


def test_counter_ahead_of_active_credential_is_inconsistent(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    CoordinatorKeyGeneration.objects.filter(coordinator=coordinator).update(last_key_version=2)
    with pytest.raises(CoordinatorGenerationInconsistent):
        _rotate(admin, farm_id, coordinator)
    assert _active(coordinator).pk == first.credential.pk
    assert _counter(coordinator).last_key_version == 2


def test_active_generation_ahead_of_counter_is_inconsistent(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    CoordinatorCredential.objects.filter(pk=first.credential.pk).update(key_version=2)
    with pytest.raises(CoordinatorGenerationInconsistent):
        _rotate(admin, farm_id, coordinator)
    assert _counter(coordinator).last_key_version == 1


def test_counter_below_history_blocks_new_issue(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _rotate(admin, farm_id, coordinator)
    _revoke(admin, farm_id, coordinator)
    CoordinatorKeyGeneration.objects.filter(coordinator=coordinator).update(last_key_version=1)
    with pytest.raises(CoordinatorGenerationInconsistent):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorCredential.objects.filter(active_slot=1).exists()


def test_generation_unique_constraint(admin, farm_id, coordinator):
    _revoked_credential(coordinator, 1)
    with pytest.raises(IntegrityError), transaction.atomic():
        _revoked_credential(coordinator, 1)


def _key_version_column_type() -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'iot_coordinator_credentials' "
            "AND COLUMN_NAME = 'key_version'"
        )
        row = cursor.fetchone()
    assert row is not None
    return str(row[0]).lower()


def _generation_check_clause() -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'iot_coordinator_credentials' "
            "AND CONSTRAINT_NAME = 'iot_coordcred_generation_range'"
        )
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def test_generation_zero_is_rejected_by_the_minimum_check(admin, farm_id, coordinator):
    column_type = _key_version_column_type()
    assert column_type.startswith("smallint")
    assert "unsigned" in column_type
    clause = _generation_check_clause()
    assert "key_version" in clause
    assert "65535" in clause

    with pytest.raises(IntegrityError) as excinfo:
        with transaction.atomic():
            _revoked_credential(coordinator, 0)

    code, message = excinfo.value.args[:2]
    assert code == 4025
    assert "iot_coordcred_generation_range" in message
    assert not CoordinatorCredential.objects.filter(coordinator=coordinator).exists()


def test_generation_above_uint16_is_rejected_by_the_column_range(admin, farm_id, coordinator):
    column_type = _key_version_column_type()
    assert column_type.startswith("smallint")
    assert "unsigned" in column_type

    with pytest.raises(DataError) as excinfo:
        with transaction.atomic():
            _revoked_credential(coordinator, COORDINATOR_KEY_VERSION_MAX + 1)

    code, message = excinfo.value.args[:2]
    assert code == 1264
    assert "key_version" in message
    assert "out of range" in message.lower()
    assert not CoordinatorCredential.objects.filter(coordinator=coordinator).exists()


def test_null_generations_are_allowed_for_legacy_rows(admin, farm_id, coordinator):
    _revoked_credential(coordinator, None)
    _revoked_credential(coordinator, None)
    assert CoordinatorCredential.objects.filter(key_version__isnull=True).count() == 2


# --- Agotamiento uint16 ---


def test_rotation_at_uint16_max_is_exhausted_and_keeps_current_credential(
    admin, farm_id, coordinator
):
    first = _issue(admin, farm_id, coordinator)
    CoordinatorCredential.objects.filter(pk=first.credential.pk).update(
        key_version=COORDINATOR_KEY_VERSION_MAX
    )
    CoordinatorKeyGeneration.objects.filter(coordinator=coordinator).update(
        last_key_version=COORDINATOR_KEY_VERSION_MAX
    )
    with pytest.raises(CoordinatorGenerationExhausted):
        _rotate(admin, farm_id, coordinator)

    first.credential.refresh_from_db()
    assert first.credential.is_active
    assert first.credential.key_version == COORDINATOR_KEY_VERSION_MAX
    assert _active(coordinator).pk == first.credential.pk
    assert _counter(coordinator).last_key_version == COORDINATOR_KEY_VERSION_MAX
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == 1
    event = AuditEvent.objects.get(event_type=EXHAUSTED_EVENT)
    assert event.metadata["operation"] == "rotate"
    assert event.after_data["last_key_version"] == COORDINATOR_KEY_VERSION_MAX


def test_issue_at_uint16_max_is_exhausted(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _revoke(admin, farm_id, coordinator)
    CoordinatorKeyGeneration.objects.filter(coordinator=coordinator).update(
        last_key_version=COORDINATOR_KEY_VERSION_MAX
    )
    with pytest.raises(CoordinatorGenerationExhausted):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorCredential.objects.filter(active_slot=1).exists()
    assert _counter(coordinator).last_key_version == COORDINATOR_KEY_VERSION_MAX
    assert AuditEvent.objects.filter(event_type=EXHAUSTED_EVENT).count() == 1


# --- Atomicidad ---


def test_failed_first_issue_rolls_back_counter(admin, farm_id, coordinator, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(coordinator_services, "_create", boom)
    with pytest.raises(RuntimeError, match="fallo simulado"):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorCredential.objects.exists()
    assert not CoordinatorKeyGeneration.objects.exists()
    assert not AuditEvent.objects.filter(
        event_type=EventType.IOT_COORDINATOR_CREDENTIAL_ISSUED
    ).exists()


def test_failed_rotation_rolls_back_generation_and_keeps_old_credential(
    admin, farm_id, coordinator, monkeypatch
):
    first = _issue(admin, farm_id, coordinator)
    events = AuditEvent.objects.count()

    def boom(**kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(coordinator_services, "_create", boom)
    with pytest.raises(RuntimeError, match="fallo simulado"):
        _rotate(admin, farm_id, coordinator)

    first.credential.refresh_from_db()
    assert first.credential.is_active and first.credential.key_version == 1
    assert _counter(coordinator).last_key_version == 1
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == 1
    assert AuditEvent.objects.count() == events
