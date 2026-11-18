# ruff: noqa: S101, S105, S106
"""Correcciones de auditoría de credenciales de coordinador (IoT-03.2-R2).

Propósito: reemisión que reinicia la ventana anti-replay (F-01), rango de
versiones y errores de integridad específicos (F-04), coherencia de identidad y
finca en la aceptación de secuencia (F-05), coherencia de revocación (F-06),
endurecimiento temporal (F-07) y las pruebas adicionales A y B.
Contexto: solo mediante el orquestador desechable.
"""
import base64
import secrets

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.farms.models import FarmRole
from apps.iot import coordinator_credentials as crypto
from apps.iot import services as iot
from apps.iot.models import (
    CoordinatorBootWindow,
    CoordinatorCredential,
    CoordinatorGateway,
    CoordinatorRevocationCause,
    Sensor,
)
from apps.iot.services import coordinator_credentials as coordinator_services
from apps.iot.services.coordinator_credentials import (
    AuthenticatedCoordinatorFrame,
    _create,
)
from apps.iot.tests import lora_vectors
from apps.iot.tests.conftest import make_source_uid
from apps.trazabilidad.models import AuditEvent, EventType
from config.coordinator_key_encryption_key import (
    UnsupportedMasterKeyVersion,
    load_master_key_ring,
    parse_master_key_version,
)

pytestmark = pytest.mark.django_db

CONTEXT = b"contexto-de-prueba"


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


def _stored_key(credential):
    return crypto.decrypt_coordinator_key(
        credential.ciphertext,
        credential.nonce,
        coordinator_uid=credential.coordinator.uid,
        key_ring=load_master_key_ring(),
        master_key_version=credential.master_key_version,
    )


def _tag(key, signed, context=CONTEXT):
    return crypto.compute_lora_hmac(key, signed, context=context)


def _frame(uid, key, *, boot=1, seq=1, signed=None, key_version=1, context=CONTEXT):
    # Trama v1 real y coherente con los campos (M-02, IoT-04.2).
    if signed is None:
        signed = lora_vectors.signed_bytes(
            uid=uid, key_version=key_version, boot_counter=boot, seq=seq
        )
    return crypto.ParsedCoordinatorFrame(
        coordinator_uid=uid,
        key_version=key_version,
        boot_counter=boot,
        seq=seq,
        signed_bytes=signed,
        tag=_tag(key, signed, context),
    )


def _authenticated(coordinator, farm_id, boot, seq=1):
    return AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=boot,
        seq=seq,
        key_version=1,
    )


def _accept(coordinator, farm_id, boot, seq=1):
    with transaction.atomic():
        iot.accept_coordinator_sequence(frame=_authenticated(coordinator, farm_id, boot, seq))


def _accept_frame(frame):
    with transaction.atomic():
        iot.accept_coordinator_sequence(frame=frame)


def _boots(coordinator):
    return list(
        CoordinatorBootWindow.objects.filter(coordinator=coordinator)
        .order_by("boot_counter")
        .values_list("boot_counter", flat=True)
    )


def _active(coordinator):
    return CoordinatorCredential.objects.filter(coordinator=coordinator, active_slot=1)


def _events(event_type):
    return AuditEvent.objects.filter(event_type=event_type)


# --- F-01: reemisión reinicia la ventana anti-replay ---


def test_reissue_resets_the_window_and_accepts_the_new_boot_zero(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    _accept(coordinator, farm_id, 50)
    assert _boots(coordinator) == [50]

    second = _rotate(
        admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED
    )
    assert _boots(coordinator) == []

    _accept(coordinator, farm_id, 0)
    assert _boots(coordinator) == [0]

    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, first.key), hmac_context=CONTEXT
    )
    assert iot.verify_coordinator_frame(
        frame=_frame(
            coordinator.uid,
            second.key,
            key_version=second.credential.key_version,
        ),
        hmac_context=CONTEXT,
    )
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert event.metadata["boot_window_rows_reset"] == 1


def test_ordinary_rotation_does_not_reset_the_window(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _accept(coordinator, farm_id, 50)
    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.ROTATED)
    assert _boots(coordinator) == [50]
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert "boot_window_rows_reset" not in event.metadata


def test_reissue_reset_only_touches_its_coordinator(admin, farm_id, coordinator):
    other = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador sur")
    _issue(admin, farm_id, coordinator)
    _issue(admin, farm_id, other)
    _accept(coordinator, farm_id, 50)
    _accept(other, farm_id, 70)

    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    assert _boots(coordinator) == []
    assert _boots(other) == [70]


def test_reissue_reset_rolls_back_when_audit_fails(
    admin, farm_id, coordinator, monkeypatch
):
    first = _issue(admin, farm_id, coordinator)
    _accept(coordinator, farm_id, 50)
    credentials = CoordinatorCredential.objects.filter(coordinator=coordinator).count()
    events = AuditEvent.objects.count()

    def boom(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(coordinator_services, "audit", boom)
    with pytest.raises(RuntimeError):
        _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    first.credential.refresh_from_db()
    assert first.credential.is_active
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == credentials
    assert _boots(coordinator) == [50]
    assert AuditEvent.objects.count() == events


def test_reissue_reset_rolls_back_when_issuing_fails(
    admin, farm_id, coordinator, monkeypatch
):
    first = _issue(admin, farm_id, coordinator)
    _accept(coordinator, farm_id, 50)
    events = AuditEvent.objects.count()

    def boom(**kwargs):
        raise RuntimeError("emisión caída")

    monkeypatch.setattr(coordinator_services, "_create", boom)
    with pytest.raises(RuntimeError):
        _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    first.credential.refresh_from_db()
    assert first.credential.is_active
    assert _active(coordinator).count() == 1
    assert _boots(coordinator) == [50]
    assert AuditEvent.objects.count() == events


# --- B-03 (IoT-03.2-R3): reinicio con la ventana completa (cuatro filas) ---

FULL_WINDOW = [47, 48, 49, 50]


def _fill_window(coordinator, farm_id):
    for boot in FULL_WINDOW:
        _accept(coordinator, farm_id, boot)
    assert _boots(coordinator) == FULL_WINDOW


def test_reissue_resets_all_four_window_rows(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _fill_window(coordinator, farm_id)

    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    assert _boots(coordinator) == []
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert event.metadata["boot_window_rows_reset"] == 4
    _accept(coordinator, farm_id, 0)
    assert _boots(coordinator) == [0]


def test_ordinary_rotation_keeps_all_four_window_rows(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _fill_window(coordinator, farm_id)

    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.ROTATED)

    assert _boots(coordinator) == FULL_WINDOW
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert "boot_window_rows_reset" not in event.metadata


def test_full_window_reset_is_atomic_when_audit_fails(
    admin, farm_id, coordinator, monkeypatch
):
    first = _issue(admin, farm_id, coordinator)
    _fill_window(coordinator, farm_id)
    events = AuditEvent.objects.count()

    def boom(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(coordinator_services, "audit", boom)
    with pytest.raises(RuntimeError):
        _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    first.credential.refresh_from_db()
    assert first.credential.is_active
    assert _active(coordinator).count() == 1
    assert _boots(coordinator) == FULL_WINDOW
    assert AuditEvent.objects.count() == events


def test_full_window_reset_does_not_touch_another_full_window(admin, farm_id, coordinator):
    other = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador sur")
    _issue(admin, farm_id, coordinator)
    _issue(admin, farm_id, other)
    _fill_window(coordinator, farm_id)
    for boot in (70, 71, 72, 73):
        _accept(other, farm_id, boot)

    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)

    assert _boots(coordinator) == []
    assert _boots(other) == [70, 71, 72, 73]
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert event.metadata["boot_window_rows_reset"] == 4


# --- F-04: rango de versiones e IntegrityError específico ---


@pytest.mark.parametrize("value", [1, 65535, "1", "65535"])
def test_master_key_version_accepts_field_range(value):
    assert 1 <= parse_master_key_version(value) <= 65535


@pytest.mark.parametrize("value", [0, -1, 65536, 100000, True, "0", "65536", None])
def test_master_key_version_rejects_out_of_range(value):
    with pytest.raises(ImproperlyConfigured):
        parse_master_key_version(value)


@pytest.mark.parametrize("version", ["0", "65536"])
def test_issue_rejects_unpersistable_master_key_version(
    admin, farm_id, coordinator, settings, version
):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = version
    events = AuditEvent.objects.count()
    with pytest.raises(ImproperlyConfigured):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorCredential.objects.exists()
    assert AuditEvent.objects.count() == events


def test_active_credential_conflict_is_translated_to_conflict(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    with pytest.raises(AppError) as excinfo:
        with transaction.atomic():
            _create(
                coordinator=coordinator, user=admin, now=timezone.now(), key_version=1
            )
    assert excinfo.value.status_code == 409
    assert _active(coordinator).count() == 1


def test_other_integrity_errors_are_not_masked(admin, farm_id, coordinator, monkeypatch):
    def invalid_encryption(key, **kwargs):
        return crypto.EncryptedCoordinatorKey(
            ciphertext=b"x" * 47,
            nonce=b"x" * 12,
            master_key_version=1,
        )

    monkeypatch.setattr(
        coordinator_services.crypto, "encrypt_coordinator_key", invalid_encryption
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _create(
                coordinator=coordinator, user=admin, now=timezone.now(), key_version=1
            )
    assert not CoordinatorCredential.objects.exists()


# --- F-05: identidad y finca en aceptación de secuencia ---


def test_accept_uses_the_persisted_identity(admin, farm_id, coordinator):
    _accept(coordinator, farm_id, 5)
    assert _boots(coordinator) == [5]


def test_accept_rejects_a_mismatched_coordinator_id(admin, farm_id, coordinator):
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk + 999,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=1,
        seq=1,
        key_version=1,
    )
    with pytest.raises(AppError) as excinfo:
        _accept_frame(frame)
    assert excinfo.value.status_code == 404
    assert _boots(coordinator) == []


def test_accept_rejects_a_mismatched_farm(admin, farm_id, coordinator, other_farm_id):
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=other_farm_id,
        credential_id=1,
        boot_counter=1,
        seq=1,
        key_version=1,
    )
    with pytest.raises(AppError) as excinfo:
        _accept_frame(frame)
    assert excinfo.value.status_code == 404
    assert _boots(coordinator) == []


def test_accept_rejects_a_frame_for_another_coordinator(admin, farm_id, coordinator):
    other = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador sur")
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=other.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=1,
        seq=1,
        key_version=1,
    )
    with pytest.raises(AppError) as excinfo:
        _accept_frame(frame)
    assert excinfo.value.status_code == 404
    assert _boots(coordinator) == [] and _boots(other) == []


def test_accept_rejects_a_foreign_coordinator_with_a_local_farm(
    admin, farm_id, other_admin, other_farm_id
):
    foreign = iot.create_coordinator(
        user=other_admin, farm_id=other_farm_id, name="Ajeno"
    )
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=foreign.pk,
        coordinator_uid=foreign.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=1,
        seq=1,
        key_version=1,
    )
    with pytest.raises(AppError) as excinfo:
        _accept_frame(frame)
    assert excinfo.value.status_code == 404
    assert _boots(foreign) == []


def test_accept_rejects_an_inactive_coordinator(admin, farm_id, coordinator):
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    frame = _authenticated(coordinator, farm_id, 1)
    with pytest.raises(AppError) as excinfo:
        _accept_frame(frame)
    assert excinfo.value.status_code == 409
    assert _boots(coordinator) == []


def test_accept_rejects_a_manipulated_frame_without_touching_the_window(
    admin, farm_id, coordinator
):
    other = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador sur")
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=other.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=3,
        seq=3,
        key_version=1,
    )
    with pytest.raises(AppError):
        _accept_frame(frame)
    assert CoordinatorBootWindow.objects.count() == 0


# --- F-06: coherencia de revocación ---


def test_active_credential_has_coherent_state(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    credential = issued.credential
    assert credential.active_slot == 1
    assert credential.revoked_at is None and credential.revoked_by is None
    assert credential.revocation_cause == ""


def test_revoked_credential_has_cause_and_date(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    revoked = iot.revoke_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    revoked.refresh_from_db()
    assert revoked.active_slot is None
    assert revoked.revoked_at is not None
    assert revoked.revocation_cause == CoordinatorRevocationCause.ADMIN
    assert revoked.pk == issued.credential.pk


def test_revoked_without_cause_is_rejected_by_the_database(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CoordinatorCredential.objects.filter(pk=issued.credential.pk).update(
                active_slot=None, revoked_at=timezone.now(), revocation_cause=""
            )


def test_revoked_without_date_is_rejected_by_the_database(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CoordinatorCredential.objects.filter(pk=issued.credential.pk).update(
                active_slot=None, revoked_at=None, revocation_cause="admin"
            )


def test_active_with_revocation_data_is_rejected_by_the_database(
    admin, farm_id, coordinator
):
    issued = _issue(admin, farm_id, coordinator)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CoordinatorCredential.objects.filter(pk=issued.credential.pk).update(
                revocation_cause="admin"
            )


def test_deleting_the_issuer_keeps_the_revocation_history(admin, farm_id, join):
    issuer = join(farm_id, FarmRole.ADMINISTRADOR, "44444444")
    coordinator = iot.create_coordinator(
        user=issuer, farm_id=farm_id, name="Coordinador del emisor"
    )
    issued = _issue(issuer, farm_id, coordinator)
    revoked = iot.revoke_coordinator_credential(
        user=issuer, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    revoked.refresh_from_db()
    assert revoked.revoked_by_id == issuer.pk

    issuer.delete()

    revoked.refresh_from_db()
    assert revoked.pk == issued.credential.pk
    assert revoked.revoked_by_id is None
    assert revoked.issued_by_id is None
    assert revoked.revoked_at is not None
    assert revoked.revocation_cause == CoordinatorRevocationCause.ADMIN


# --- F-07: mismo coste HMAC cuando la credencial es ilegible ---


def test_unreadable_credential_still_runs_the_dummy_hmac(
    admin, farm_id, coordinator, monkeypatch
):
    issued = _issue(admin, farm_id, coordinator)
    CoordinatorCredential.objects.filter(pk=issued.credential.pk).update(
        ciphertext=b"\x00" * 48
    )
    unreadable = _frame(coordinator.uid, issued.key)
    missing = _frame("CO-ZZZZZZZZZZ", issued.key)
    calls = []
    original = crypto.compute_lora_hmac

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(crypto, "compute_lora_hmac", spy)
    assert not iot.verify_coordinator_frame(frame=unreadable, hmac_context=CONTEXT)
    assert len(calls) == 1

    calls.clear()
    assert not iot.verify_coordinator_frame(frame=missing, hmac_context=CONTEXT)
    assert len(calls) == 1


# --- A. Rollback de auditoría en cascada con credencial ---


def test_audit_failure_in_coordinator_deactivation_keeps_all_state(
    admin, farm_id, monkeypatch
):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid),
        channel=1,
        metric="temperature",
    )
    issued = _issue(admin, farm_id, coordinator)
    events = AuditEvent.objects.count()
    associations = CoordinatorGateway.objects.count()

    def boom(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(coordinator_services, "audit", boom)
    with pytest.raises(RuntimeError):
        iot.deactivate_coordinator(
            user=admin, farm_id=farm_id, uid=coordinator.uid, reason="cascada"
        )

    issued.credential.refresh_from_db()
    coordinator.refresh_from_db()
    sensor = Sensor.objects.get(coordinator=coordinator)
    assert coordinator.is_active
    assert issued.credential.is_active
    assert sensor.is_active and sensor.active_channel == 1
    assert CoordinatorGateway.objects.filter(active_pair=1).count() == associations
    assert AuditEvent.objects.count() == events


# --- B. Rotación de la clave maestra ---


def test_master_key_ring_keeps_old_credentials_readable_until_retired(
    admin, farm_id, coordinator, settings
):
    key_one = _master_key()
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = key_one
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    first = _issue(admin, farm_id, coordinator)
    assert first.credential.master_key_version == 1
    assert _stored_key(first.credential) == first.key

    key_two = _master_key()
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = key_two
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "2"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = f"1:{key_one}"
    assert _stored_key(first.credential) == first.key
    assert iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, first.key), hmac_context=CONTEXT
    )

    second_coordinator = iot.create_coordinator(
        user=admin, farm_id=farm_id, name="Coordinador nuevo"
    )
    second = _issue(admin, farm_id, second_coordinator)
    assert second.credential.master_key_version == 2
    assert _stored_key(second.credential) == second.key

    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    with pytest.raises(UnsupportedMasterKeyVersion):
        _stored_key(first.credential)
    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, first.key), hmac_context=CONTEXT
    )
    assert iot.verify_coordinator_frame(
        frame=_frame(second_coordinator.uid, second.key), hmac_context=CONTEXT
    )
