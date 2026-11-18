# ruff: noqa: S101, S105, S106
"""Servicios de credenciales de coordinador (IoT-03.2).

Propósito: emisión, rotación inmediata, revocación, historial, autenticación de
tramas, integración con la desactivación del coordinador, autorización,
aislamiento, atomicidad, restricciones de MariaDB y ausencia de la clave en
logs, excepciones, ``repr``, auditoría y contenido persistido.
Contexto: solo mediante el orquestador desechable.
"""
import base64
import json
import secrets

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.accounts.errors import AppError
from apps.farms.services import deactivate_farm
from apps.iot import coordinator_credentials as crypto
from apps.iot import services as iot
from apps.iot.models import CoordinatorCredential, CoordinatorRevocationCause
from apps.iot.services.access import IOT_MANAGE_FORBIDDEN
from apps.iot.tests import lora_vectors
from apps.trazabilidad.models import AuditEvent, EventType
from config.coordinator_key_encryption_key import load_master_key_ring

pytestmark = pytest.mark.django_db

CONTEXT = b"contexto-de-prueba"
UID_MALFORMADO = "CO-123"


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


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status
    return excinfo.value


def _events(event_type):
    return AuditEvent.objects.filter(event_type=event_type)


def _stored_key(credential):
    return crypto.decrypt_coordinator_key(
        credential.ciphertext,
        credential.nonce,
        coordinator_uid=credential.coordinator.uid,
        key_ring=load_master_key_ring(),
        master_key_version=credential.master_key_version,
    )


def _frame(uid, key, *, boot=1, seq=1, signed=None, key_version=1, context=CONTEXT):
    # Trama v1 real y coherente con los campos (M-02, IoT-04.2).
    if signed is None:
        signed = lora_vectors.signed_bytes(
            uid=uid, key_version=key_version, boot_counter=boot, seq=seq
        )
    tag = crypto.compute_lora_hmac(key, signed, context=context)
    return crypto.ParsedCoordinatorFrame(
        coordinator_uid=uid,
        key_version=key_version,
        boot_counter=boot,
        seq=seq,
        signed_bytes=signed,
        tag=tag,
    )


def _active(coordinator):
    return CoordinatorCredential.objects.filter(coordinator=coordinator, active_slot=1)


# --- Emisión ---


def test_issue_returns_the_key_once_and_stores_only_ciphertext(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    credential = issued.credential
    assert len(issued.key) == 32
    assert base64.b64decode(issued.provisioning_key()) == issued.key
    assert credential.is_active and credential.master_key_version == 1
    assert credential.issued_by == admin and credential.revocation_cause == ""
    assert len(credential.ciphertext) == 48 and len(credential.nonce) == 12
    assert _stored_key(credential) == issued.key
    stored = CoordinatorCredential.objects.filter(pk=credential.pk).values().get()
    assert issued.key.hex() not in json.dumps(stored, default=str)
    assert issued.provisioning_key() not in json.dumps(stored, default=str)
    assert "key=" not in repr(issued)


def test_issue_audits_without_secret_material(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ISSUED).get()
    assert event.farm_id == farm_id and event.actor == admin
    assert event.entity_type == "iot_coordinator_credential"
    assert event.entity_id == str(issued.credential.pk)
    assert event.after_data == {
        "coordcred_id": issued.credential.pk,
        "coordinator_uid": coordinator.uid,
        "key_version": 1,
        "master_key_version": 1,
        "active": True,
        "revocation_cause": "",
    }
    assert issued.key.hex() not in json.dumps(event.after_data, default=str)


def test_issue_twice_is_conflict(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _error(lambda: _issue(admin, farm_id, coordinator), 409)
    assert CoordinatorCredential.objects.count() == 1


def test_issue_requires_active_coordinator(admin, farm_id, coordinator):
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    _error(lambda: _issue(admin, farm_id, coordinator), 409)
    assert not _active(coordinator).exists()


def test_issue_in_inactive_farm_is_not_found(admin, farm_id, coordinator):
    deactivate_farm(user=admin, farm_id=farm_id)
    _error(lambda: _issue(admin, farm_id, coordinator), 404)
    assert not CoordinatorCredential.objects.exists()


def test_issue_without_master_key_fails_closed(admin, farm_id, coordinator, settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = ""
    events = AuditEvent.objects.count()
    with pytest.raises(ImproperlyConfigured):
        _issue(admin, farm_id, coordinator)
    assert not CoordinatorCredential.objects.exists()
    assert AuditEvent.objects.count() == events


def test_issue_rejects_malformed_uid(admin, farm_id):
    _error(
        lambda: iot.issue_coordinator_credential(
            user=admin, farm_id=farm_id, coordinator_uid=UID_MALFORMADO
        ),
        404,
    )


# --- Rotación ---


def test_rotation_revokes_previous_and_returns_a_new_key(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    second = _rotate(admin, farm_id, coordinator)
    first.credential.refresh_from_db()
    assert not first.credential.is_active
    assert first.credential.revocation_cause == CoordinatorRevocationCause.ROTATED
    assert second.credential.is_active and _active(coordinator).count() == 1
    assert _stored_key(second.credential) == second.key != first.key
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


def test_rotation_audits_cause_and_previous_id(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    second = _rotate(admin, farm_id, coordinator, reason="cambio programado")
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED).get()
    assert event.entity_id == str(second.credential.pk)
    assert event.reason == "cambio programado"
    assert event.metadata == {
        "cause": str(CoordinatorRevocationCause.ROTATED),
        "previous_coordcred_id": first.credential.pk,
    }


def test_reissue_marks_the_previous_key_as_reissued(admin, farm_id, coordinator):
    first = _issue(admin, farm_id, coordinator)
    _rotate(admin, farm_id, coordinator, cause=CoordinatorRevocationCause.REISSUED)
    first.credential.refresh_from_db()
    assert first.credential.revocation_cause == CoordinatorRevocationCause.REISSUED


def test_rotation_rejects_non_rotation_causes(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    for cause in (CoordinatorRevocationCause.ADMIN, CoordinatorRevocationCause.COMPROMISED):
        _error(lambda cause=cause: _rotate(admin, farm_id, coordinator, cause=cause), 400)
    assert _active(coordinator).count() == 1


def test_rotation_without_active_credential_is_not_found(admin, farm_id, coordinator):
    _error(lambda: _rotate(admin, farm_id, coordinator), 404)


# --- Revocación ---


@pytest.mark.parametrize(
    ("cause", "event_type"),
    [
        (CoordinatorRevocationCause.ADMIN, EventType.IOT_COORDINATOR_CREDENTIAL_REVOKED),
        (
            CoordinatorRevocationCause.COMPROMISED,
            EventType.IOT_COORDINATOR_CREDENTIAL_COMPROMISED,
        ),
    ],
)
def test_revoke_marks_the_credential_and_audits_the_cause(
    admin, farm_id, coordinator, cause, event_type
):
    issued = _issue(admin, farm_id, coordinator)
    revoked = _revoke(admin, farm_id, coordinator, cause=cause, reason="motivo")
    assert revoked.pk == issued.credential.pk
    revoked.refresh_from_db()
    assert not revoked.is_active and revoked.revoked_by == admin
    assert revoked.revocation_cause == cause and revoked.revoked_at is not None
    event = _events(event_type).get()
    assert event.entity_id == str(revoked.pk)
    assert event.metadata == {"cause": str(cause)}
    assert not _active(coordinator).exists()


def test_revoke_is_idempotent_but_rejects_rotation_causes(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    _revoke(admin, farm_id, coordinator)
    _error(lambda: _revoke(admin, farm_id, coordinator), 404)
    for cause in (CoordinatorRevocationCause.ROTATED, CoordinatorRevocationCause.REISSUED):
        _error(lambda cause=cause: _revoke(admin, farm_id, coordinator, cause=cause), 400)


def test_revoke_unknown_or_malformed_coordinator_is_not_found(admin, farm_id):
    for uid in (UID_MALFORMADO, "CO-ZZZZZZZZZZ"):
        _error(
            lambda uid=uid: iot.revoke_coordinator_credential(
                user=admin, farm_id=farm_id, coordinator_uid=uid
            ),
            404,
        )


# --- Autorización y aislamiento ---


def _writes(user, farm_id, uid):
    return [
        lambda: iot.issue_coordinator_credential(
            user=user, farm_id=farm_id, coordinator_uid=uid
        ),
        lambda: iot.rotate_coordinator_credential(
            user=user, farm_id=farm_id, coordinator_uid=uid
        ),
        lambda: iot.revoke_coordinator_credential(
            user=user, farm_id=farm_id, coordinator_uid=uid
        ),
    ]


@pytest.mark.parametrize("role_fixture", ["operator", "viewer"])
def test_non_admin_cannot_manage_credentials(
    request, farm_id, coordinator, role_fixture
):
    member = request.getfixturevalue(role_fixture)
    for uid in (coordinator.uid, UID_MALFORMADO):
        for operation in _writes(member, farm_id, uid):
            assert _error(operation, 403).message == IOT_MANAGE_FORBIDDEN
    assert not CoordinatorCredential.objects.exists()


def test_non_member_gets_not_found(farm_id, coordinator, other_admin):
    for operation in _writes(other_admin, farm_id, coordinator.uid):
        assert _error(operation, 404).message == "finca no encontrada"
    assert not CoordinatorCredential.objects.exists()


def test_foreign_coordinator_is_not_found(other_farm_id, admin, other_admin):
    foreign = iot.create_coordinator(
        user=other_admin, farm_id=other_farm_id, name="Ajeno"
    )
    for operation in _writes(admin, other_farm_id, foreign.uid):
        assert _error(operation, 404).message == "finca no encontrada"
    assert not CoordinatorCredential.objects.exists()


# --- Historial ---


def test_list_returns_history_without_key_material(admin, farm_id, coordinator, viewer):
    first = _issue(admin, farm_id, coordinator)
    second = _rotate(admin, farm_id, coordinator)
    rows = list(
        iot.list_coordinator_credentials(
            user=viewer, farm_id=farm_id, coordinator_uid=coordinator.uid
        )
    )
    assert [row.pk for row in rows] == [second.credential.pk, first.credential.pk]
    assert all(not hasattr(row, "key") for row in rows)
    assert first.key.hex() not in repr(rows)


def test_list_unknown_coordinator_is_not_found(admin, farm_id):
    _error(
        lambda: iot.list_coordinator_credentials(
            user=admin, farm_id=farm_id, coordinator_uid="CO-ZZZZZZZZZZ"
        ),
        404,
    )


def test_list_foreign_farm_is_not_found(other_farm_id, admin, other_admin):
    foreign = iot.create_coordinator(
        user=other_admin, farm_id=other_farm_id, name="Ajeno"
    )
    _error(
        lambda: iot.list_coordinator_credentials(
            user=admin, farm_id=other_farm_id, coordinator_uid=foreign.uid
        ),
        404,
    )


def test_list_requires_membership(farm_id, coordinator, other_admin):
    _error(
        lambda: iot.list_coordinator_credentials(
            user=other_admin, farm_id=farm_id, coordinator_uid=coordinator.uid
        ),
        404,
    )


# --- Autenticación de tramas ---


def test_frame_verifies_with_the_active_key_and_resolves_the_farm(
    admin, farm_id, coordinator
):
    issued = _issue(admin, farm_id, coordinator)
    frame = _frame(coordinator.uid, issued.key, boot=3, seq=9)
    authenticated = iot.verify_coordinator_frame(frame=frame, hmac_context=CONTEXT)
    assert authenticated is not None
    assert authenticated.coordinator_id == coordinator.pk
    assert authenticated.coordinator_uid == coordinator.uid
    assert authenticated.farm_id == farm_id
    assert authenticated.credential_id == issued.credential.pk
    assert authenticated.key_version == issued.credential.key_version == 1
    assert (authenticated.boot_counter, authenticated.seq) == (3, 9)


def test_frame_with_wrong_tag_or_context_is_rejected(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    frame = _frame(coordinator.uid, issued.key)
    # Trama v1 estructuralmente válida y coherente (seq=2) con el tag de la
    # original (seq=1): el rechazo debe venir del HMAC, no del parser.
    tampered = crypto.ParsedCoordinatorFrame(
        coordinator_uid=frame.coordinator_uid,
        key_version=frame.key_version,
        boot_counter=frame.boot_counter,
        seq=2,
        signed_bytes=lora_vectors.signed_bytes(
            uid=coordinator.uid, key_version=1, boot_counter=1, seq=2
        ),
        tag=frame.tag,
    )
    assert crypto.validate_coordinator_frame(tampered) is tampered
    assert not iot.verify_coordinator_frame(frame=tampered, hmac_context=CONTEXT)
    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, issued.key, context=b"otro"), hmac_context=CONTEXT
    )


@pytest.mark.parametrize(
    "field_name, forged_value",
    [("key_version", 2), ("boot_counter", 99), ("seq", 99), ("coordinator_uid", "CO-ZZZZZZZZZZ")],
)
def test_authentic_bytes_with_forged_fields_are_rejected_without_queries(
    admin, farm_id, coordinator, field_name, forged_value, django_assert_num_queries
):
    """M-02 (IoT-04.2): bytes y tag auténticos no avalan campos que los contradicen."""
    issued = _issue(admin, farm_id, coordinator)
    valid = _frame(coordinator.uid, issued.key)
    values = {
        "coordinator_uid": valid.coordinator_uid,
        "key_version": valid.key_version,
        "boot_counter": valid.boot_counter,
        "seq": valid.seq,
        "signed_bytes": valid.signed_bytes,
        "tag": valid.tag,
    }
    values[field_name] = forged_value
    forged = crypto.ParsedCoordinatorFrame(**values)
    with django_assert_num_queries(0):
        assert iot.verify_coordinator_frame(frame=forged, hmac_context=CONTEXT) is None
    assert iot.verify_coordinator_frame(frame=valid, hmac_context=CONTEXT) is not None


def test_frame_without_active_credential_or_coordinator_is_rejected(
    admin, farm_id, coordinator
):
    issued = _issue(admin, farm_id, coordinator)
    assert not iot.verify_coordinator_frame(
        frame=_frame("CO-ZZZZZZZZZZ", issued.key), hmac_context=CONTEXT
    )
    _revoke(admin, farm_id, coordinator)
    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, issued.key), hmac_context=CONTEXT
    )


def test_frame_of_inactive_coordinator_or_farm_is_rejected(
    admin, farm_id, coordinator, settings
):
    issued = _issue(admin, farm_id, coordinator)
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, issued.key), hmac_context=CONTEXT
    )
    iot.reactivate_coordinator(
        user=admin, farm_id=farm_id, uid=coordinator.uid, reason="vuelve"
    )
    new = _issue(admin, farm_id, coordinator)
    deactivate_farm(user=admin, farm_id=farm_id)
    assert not iot.verify_coordinator_frame(
        frame=_frame(
            coordinator.uid,
            new.key,
            key_version=new.credential.key_version,
        ),
        hmac_context=CONTEXT,
    )


def test_frame_with_retired_master_version_is_rejected(admin, farm_id, coordinator, settings):
    issued = _issue(admin, farm_id, coordinator)
    CoordinatorCredential.objects.filter(pk=issued.credential.pk).update(
        master_key_version=99
    )
    assert not iot.verify_coordinator_frame(
        frame=_frame(coordinator.uid, issued.key), hmac_context=CONTEXT
    )


def test_invalid_frame_shape_is_rejected_without_touching_the_database(
    coordinator,
):
    assert not iot.verify_coordinator_frame(frame={"uid": coordinator.uid}, hmac_context=CONTEXT)


@pytest.mark.parametrize("length", [1, 8, 16, 31, 33])
def test_frame_with_tag_of_wrong_length_is_rejected_without_queries(
    admin, farm_id, coordinator, length, django_assert_num_queries
):
    """H-01 (R3): un prefijo auténtico del HMAC o un tag ampliado no autentica."""
    issued = _issue(admin, farm_id, coordinator)
    valid = _frame(coordinator.uid, issued.key)
    tag = (valid.tag + valid.tag)[:length]
    forged = crypto.ParsedCoordinatorFrame(
        coordinator_uid=valid.coordinator_uid,
        key_version=valid.key_version,
        boot_counter=valid.boot_counter,
        seq=valid.seq,
        signed_bytes=valid.signed_bytes,
        tag=tag,
    )
    with django_assert_num_queries(0):
        assert iot.verify_coordinator_frame(frame=forged, hmac_context=CONTEXT) is None
    assert iot.verify_coordinator_frame(frame=valid, hmac_context=CONTEXT) is not None


def test_frame_verification_requires_an_explicit_context(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    with pytest.raises(crypto.MissingHmacContext):
        iot.verify_coordinator_frame(
            frame=_frame(coordinator.uid, issued.key), hmac_context=None
        )


# --- Desactivación del coordinador ---


def test_deactivation_revokes_credentials_and_audits_the_cascade(admin, farm_id, coordinator):
    issued = _issue(admin, farm_id, coordinator)
    iot.deactivate_coordinator(
        user=admin, farm_id=farm_id, uid=coordinator.uid, reason="fin de servicio"
    )
    issued.credential.refresh_from_db()
    assert not issued.credential.is_active
    assert (
        issued.credential.revocation_cause
        == CoordinatorRevocationCause.COORDINATOR_DEACTIVATED
    )
    event = _events(EventType.IOT_COORDINATOR_CREDENTIAL_CASCADE_REVOKED).get()
    assert event.entity_id == str(issued.credential.pk)
    assert event.reason == "fin de servicio"
    parent = _events(EventType.IOT_COORDINATOR_DEACTIVATED).get()
    assert parent.metadata["revoked_coordcreds"] == 1


def test_reactivation_does_not_restore_credentials(admin, farm_id, coordinator):
    _issue(admin, farm_id, coordinator)
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    iot.reactivate_coordinator(
        user=admin, farm_id=farm_id, uid=coordinator.uid, reason="vuelve"
    )
    assert not _active(coordinator).exists()
    issued = _issue(admin, farm_id, coordinator)
    assert _active(coordinator).count() == 1
    assert issued.credential.is_active