# ruff: noqa: S101, S105, S106
"""Servicios y restricciones de credenciales de gateway (IoT-03.1).

Propósito: emisión, verificación, rotación, revocación, desactivación del
gateway y de la finca, autorización, aislamiento, atomicidad, restricciones de
MariaDB y ausencia del secreto en logs, excepciones, ``repr``, auditoría y
contenido persistido.
Contexto: solo mediante el orquestador desechable.
"""
import json
import logging
import secrets
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.farms.services import deactivate_farm, reactivate_farm
from apps.iot import db_guard
from apps.iot import gateway_credentials as crypto
from apps.iot import services as iot
from apps.iot.models import Gateway, GatewayCredential, RevocationCause
from apps.iot.services import credentials as credential_services
from apps.iot.services.access import IOT_MANAGE_FORBIDDEN
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def key(settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)


@pytest.fixture
def gateway(admin, farm_id):
    return iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway norte")


def _issue(admin, farm_id, gateway):
    return iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status
    return excinfo.value


def _events(event_type):
    return AuditEvent.objects.filter(event_type=event_type)


def _active(gateway):
    return GatewayCredential.objects.filter(gateway=gateway, active_slot=1)


# --- Emisión ---


def test_issue_returns_secret_once_and_stores_only_fingerprint(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    credential = issued.credential
    assert crypto.is_well_formed_secret(issued.secret)
    assert credential.fingerprint == crypto.fingerprint(gateway.uid, issued.secret)
    assert credential.key_version == 1 and credential.is_active
    assert credential.issued_by == admin and credential.revocation_cause == ""
    stored = GatewayCredential.objects.filter(pk=credential.pk).values().get()
    assert issued.secret not in json.dumps(stored, default=str)
    event = _events(EventType.IOT_GATEWAY_CREDENTIAL_ISSUED).get()
    assert event.farm_id == farm_id and event.actor == admin
    assert event.entity_type == "iot_gateway_credential"
    assert event.entity_id == str(credential.pk)
    assert event.after_data == {
        "gwcred_id": credential.pk,
        "gateway_uid": gateway.uid,
        "key_version": 1,
        "active": True,
        "revocation_cause": "",
    }


def test_issue_twice_is_conflict(admin, farm_id, gateway):
    _issue(admin, farm_id, gateway)
    _error(lambda: _issue(admin, farm_id, gateway), 409)
    assert GatewayCredential.objects.count() == 1


def test_issue_requires_active_gateway(admin, farm_id, gateway):
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    _error(lambda: _issue(admin, farm_id, gateway), 409)


def test_issue_in_inactive_farm_is_not_found(admin, farm_id, gateway):
    deactivate_farm(user=admin, farm_id=farm_id)
    _error(lambda: _issue(admin, farm_id, gateway), 404)
    assert not GatewayCredential.objects.exists()


def test_issue_without_key_fails_closed_and_persists_nothing(admin, farm_id, gateway, settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = ""
    events = AuditEvent.objects.count()
    with pytest.raises(ImproperlyConfigured):
        _issue(admin, farm_id, gateway)
    assert not GatewayCredential.objects.exists()
    assert AuditEvent.objects.count() == events


# --- Autorización y aislamiento ---


def _writes(user, farm_id, gateway_uid):
    return [
        lambda: iot.issue_gateway_credential(user=user, farm_id=farm_id, gateway_uid=gateway_uid),
        lambda: iot.rotate_gateway_credential(user=user, farm_id=farm_id, gateway_uid=gateway_uid),
        lambda: iot.revoke_gateway_credential(user=user, farm_id=farm_id, gateway_uid=gateway_uid),
    ]


@pytest.mark.parametrize("role_fixture", ["operator", "viewer"])
def test_non_admin_cannot_manage_credentials(request, farm_id, gateway, role_fixture):
    member = request.getfixturevalue(role_fixture)
    for uid in (gateway.uid, "malformado"):
        for operation in _writes(member, farm_id, uid):
            assert _error(operation, 403).message == IOT_MANAGE_FORBIDDEN
    assert not GatewayCredential.objects.exists()


def test_non_member_gets_not_found(farm_id, gateway, other_admin):
    for operation in _writes(other_admin, farm_id, gateway.uid):
        assert _error(operation, 404).message == "finca no encontrada"


def test_foreign_gateway_is_not_found_from_own_farm(gateway, other_admin, other_farm_id):
    for operation in _writes(other_admin, other_farm_id, gateway.uid):
        assert _error(operation, 404).message == "gateway no encontrado"


def test_engine_requirement_not_revealed_before_authorization(
    monkeypatch, admin, farm_id, gateway, operator
):
    monkeypatch.setattr(db_guard, "connections", {"default": SimpleNamespace(vendor="postgresql")})
    for operation in _writes(operator, farm_id, gateway.uid):
        _error(operation, 403)
    with pytest.raises(ImproperlyConfigured):
        _issue(admin, farm_id, gateway)


def test_invalid_causes_are_validation_errors_for_admin(admin, farm_id, gateway):
    _issue(admin, farm_id, gateway)
    _error(
        lambda: iot.rotate_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid, cause="compromised"
        ),
        400,
    )
    _error(
        lambda: iot.revoke_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid, cause="rotated"
        ),
        400,
    )
    assert _active(gateway).count() == 1


def test_members_can_read_history(admin, farm_id, gateway, viewer):
    _issue(admin, farm_id, gateway)
    history = list(
        iot.list_gateway_credentials(user=viewer, farm_id=farm_id, gateway_uid=gateway.uid)
    )
    assert len(history) == 1


# --- Verificación ---


def test_verify_valid_credential_resolves_identity_from_gateway(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    result = iot.verify_gateway_credential(gateway.uid, issued.secret)
    assert result == iot.VerifiedGateway(
        gateway_id=gateway.pk, gateway_uid=gateway.uid, farm_id=farm_id
    )


def test_verify_rejects_without_distinguishing_reason(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    _issue(admin, farm_id, other)
    for uid, secret in [
        (gateway.uid, crypto.generate_secret()),  # secreto incorrecto
        (other.uid, issued.secret),  # secreto de otro gateway
        ("GW-ZZZZZZZZZZ", issued.secret),  # gateway inexistente
        (gateway.uid.lower(), issued.secret),  # uid no canónico
        (gateway.uid, "basura"),  # secreto mal formado
        (None, None),
    ]:
        assert iot.verify_gateway_credential(uid, secret) is None


def test_verify_rejects_revoked_credential(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    iot.revoke_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None


def test_verify_rejects_unsupported_key_version(admin, farm_id, gateway, caplog):
    issued = _issue(admin, farm_id, gateway)
    GatewayCredential.objects.filter(pk=issued.credential.pk).update(key_version=2)
    with caplog.at_level(logging.WARNING):
        assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None
    assert "versión de clave no soportada" in caplog.text
    assert issued.secret not in caplog.text


def test_verify_does_not_write(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    before = GatewayCredential.objects.filter(pk=issued.credential.pk).values().get()
    events = AuditEvent.objects.count()
    iot.verify_gateway_credential(gateway.uid, issued.secret)
    assert GatewayCredential.objects.filter(pk=issued.credential.pk).values().get() == before
    assert AuditEvent.objects.count() == events


def test_verify_without_key_propagates(admin, farm_id, gateway, settings):
    issued = _issue(admin, farm_id, gateway)
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        iot.verify_gateway_credential(gateway.uid, issued.secret)


# --- Rotación ---


def test_rotation_is_immediate(admin, farm_id, gateway):
    old = _issue(admin, farm_id, gateway)
    new = iot.rotate_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, reason="  rutina  "
    )
    assert iot.verify_gateway_credential(gateway.uid, old.secret) is None
    assert iot.verify_gateway_credential(gateway.uid, new.secret) is not None
    previous = GatewayCredential.objects.get(pk=old.credential.pk)
    assert previous.active_slot is None and previous.revocation_cause == "rotated"
    assert previous.revoked_by == admin and previous.revoked_at == new.credential.issued_at
    assert list(_active(gateway)) == [new.credential]
    event = _events(EventType.IOT_GATEWAY_CREDENTIAL_ROTATED).get()
    assert event.entity_id == str(new.credential.pk) and event.reason == "rutina"
    assert event.metadata == {"cause": "rotated", "previous_gwcred_id": old.credential.pk}
    assert event.before_data["gwcred_id"] == old.credential.pk
    assert event.after_data["gwcred_id"] == new.credential.pk


def test_reissue_after_lost_secret(admin, farm_id, gateway):
    old = _issue(admin, farm_id, gateway)
    new = iot.rotate_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, cause=RevocationCause.REISSUED
    )
    assert GatewayCredential.objects.get(pk=old.credential.pk).revocation_cause == "reissued"
    assert iot.verify_gateway_credential(gateway.uid, new.secret) is not None


def test_rotation_without_active_credential_is_not_found(admin, farm_id, gateway):
    _error(
        lambda: iot.rotate_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid
        ),
        404,
    )


@pytest.mark.parametrize("target", ["audit", "new_credential"])
def test_rotation_failure_keeps_previous_credential(admin, farm_id, gateway, monkeypatch, target):
    old = _issue(admin, farm_id, gateway)
    events = AuditEvent.objects.count()

    def boom(*args, **kwargs):
        raise RuntimeError("fallo simulado")

    if target == "audit":
        monkeypatch.setattr(credential_services, "audit", boom)
    else:
        monkeypatch.setattr(credential_services.crypto, "new_credential_for", boom)
    with pytest.raises(RuntimeError):
        iot.rotate_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    assert list(_active(gateway)) == [old.credential]
    assert GatewayCredential.objects.count() == 1
    assert AuditEvent.objects.count() == events
    assert iot.verify_gateway_credential(gateway.uid, old.secret) is not None


# --- Revocación ---


def test_administrative_revocation(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    revoked = iot.revoke_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, reason="retirado"
    )
    assert revoked.revocation_cause == "admin" and not revoked.is_active
    event = _events(EventType.IOT_GATEWAY_CREDENTIAL_REVOKED).get()
    assert event.reason == "retirado" and event.metadata == {"cause": "admin"}
    assert event.after_data["active"] is False
    _error(
        lambda: iot.revoke_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid
        ),
        404,
    )
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None


def test_compromise_revocation_and_reissue(admin, farm_id, gateway):
    compromised = _issue(admin, farm_id, gateway)
    iot.revoke_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, cause=RevocationCause.COMPROMISED
    )
    event = _events(EventType.IOT_GATEWAY_CREDENTIAL_COMPROMISED).get()
    assert event.metadata == {"cause": "compromised"}
    fresh = _issue(admin, farm_id, gateway)  # recuperar acceso: credencial nueva
    assert iot.verify_gateway_credential(gateway.uid, compromised.secret) is None
    assert iot.verify_gateway_credential(gateway.uid, fresh.secret) is not None
    assert GatewayCredential.objects.filter(gateway=gateway).count() == 2


def test_revoked_credential_cannot_be_reactivated(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    iot.revoke_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    with pytest.raises(IntegrityError), transaction.atomic():
        GatewayCredential.objects.filter(pk=issued.credential.pk).update(active_slot=1)
    assert not hasattr(credential_services, "reactivate_gateway_credential")


# --- Gateway y finca ---


def test_gateway_deactivation_revokes_credential(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="averiado")
    credential = GatewayCredential.objects.get(pk=issued.credential.pk)
    assert credential.revocation_cause == "gateway_deactivated" and not credential.is_active
    cascade = _events(EventType.IOT_GATEWAY_CREDENTIAL_CASCADE_REVOKED).get()
    assert cascade.metadata == {"cause": "gateway_deactivated"} and cascade.reason == "averiado"
    deactivated = _events(EventType.IOT_GATEWAY_DEACTIVATED).get()
    assert deactivated.metadata == {"revoked_authorizations": 0, "revoked_gwcreds": 1}
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None


def test_gateway_reactivation_does_not_restore_credential(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="reparado")
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None
    assert not _active(gateway).exists()
    fresh = _issue(admin, farm_id, gateway)
    assert iot.verify_gateway_credential(gateway.uid, fresh.secret) is not None


def test_audit_failure_in_gateway_deactivation_keeps_credential(
    admin, farm_id, gateway, monkeypatch
):
    issued = _issue(admin, farm_id, gateway)

    def boom(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(credential_services, "audit", boom)
    with pytest.raises(RuntimeError):
        iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    assert Gateway.objects.get(pk=gateway.pk).is_active
    assert list(_active(gateway)) == [issued.credential]


def test_farm_deactivation_blocks_without_revoking(admin, farm_id, gateway):
    issued = _issue(admin, farm_id, gateway)
    deactivate_farm(user=admin, farm_id=farm_id)
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is None
    assert _active(gateway).get() == issued.credential
    reactivate_farm(user=admin, farm_id=farm_id)
    assert iot.verify_gateway_credential(gateway.uid, issued.secret) is not None


# --- Restricciones de MariaDB (escritura directa por ORM) ---


def _row(gateway, **kwargs):
    kwargs.setdefault("fingerprint", secrets.token_hex(32))
    kwargs.setdefault("key_version", 1)
    kwargs.setdefault("issued_at", timezone.now())
    return GatewayCredential.objects.create(gateway=gateway, **kwargs)


def _rejected(callable_):
    with pytest.raises(IntegrityError), transaction.atomic():
        callable_()


def test_single_active_credential_per_gateway(gateway):
    _row(gateway)
    _rejected(lambda: _row(gateway))


def test_revoked_history_is_kept(gateway):
    now = timezone.now()
    for cause in ("rotated", "admin", "compromised"):
        _row(gateway, active_slot=None, issued_at=now, revoked_at=now, revocation_cause=cause)
    _row(gateway)
    assert GatewayCredential.objects.filter(gateway=gateway).count() == 4


def test_fingerprint_is_unique_and_hex(gateway, admin, farm_id):
    fingerprint = secrets.token_hex(32)
    _row(gateway, fingerprint=fingerprint)
    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    _rejected(lambda: _row(other, fingerprint=fingerprint))
    for bad in (fingerprint.upper(), fingerprint[:63], "z" * 64):
        _rejected(lambda bad=bad: _row(other, fingerprint=bad))


def test_key_version_must_be_positive(gateway):
    _rejected(lambda: _row(gateway, key_version=0))


def test_revocation_fields_are_coherent(gateway, admin):
    now = timezone.now()
    for kwargs in [
        {"revoked_at": now},  # activa con fecha de revocación
        {"revoked_by": admin},  # activa con autor de revocación
        {"revocation_cause": "admin"},  # activa con causa
        {"active_slot": None},  # revocada sin fecha ni causa
        {"active_slot": None, "revoked_at": now},  # revocada sin causa
        {"active_slot": None, "revoked_at": now, "revocation_cause": "otra"},
        {"active_slot": 2},
        {"active_slot": None, "issued_at": now, "revoked_at": now - timedelta(seconds=1),
         "revocation_cause": "admin"},
    ]:
        _rejected(lambda kwargs=kwargs: _row(gateway, **kwargs))


def test_gateway_with_credentials_cannot_be_deleted(admin, farm_id, gateway):
    _issue(admin, farm_id, gateway)
    with pytest.raises(ProtectedError):
        Gateway.objects.get(pk=gateway.pk).delete()


def test_engine_is_mariadb():
    assert connection.vendor == "mysql" and connection.mysql_is_mariadb


# --- Ausencia del secreto en todos los canales ---


def _table_dump(table: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")  # noqa: S608
        return json.dumps(cursor.fetchall(), default=str)


def test_secret_never_leaks(admin, farm_id, gateway, operator, caplog):
    caplog.set_level(logging.DEBUG)
    secrets_seen = []
    issued = _issue(admin, farm_id, gateway)
    secrets_seen.append(issued.secret)
    rotated = iot.rotate_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    secrets_seen.append(rotated.secret)
    errors = []
    for operation in (
        lambda: _issue(admin, farm_id, gateway),
        lambda: iot.issue_gateway_credential(
            user=operator, farm_id=farm_id, gateway_uid=gateway.uid
        ),
    ):
        try:
            operation()
        except AppError as exc:
            errors.append(exc)
    iot.verify_gateway_credential(gateway.uid, crypto.generate_secret())
    iot.verify_gateway_credential(gateway.uid, issued.secret)
    iot.revoke_gateway_credential(
        user=admin, farm_id=farm_id, gateway_uid=gateway.uid, cause=RevocationCause.COMPROMISED
    )
    fresh = _issue(admin, farm_id, gateway)
    secrets_seen.append(fresh.secret)
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)

    channels = {
        "logs": caplog.text,
        "excepciones": " ".join(f"{e!s} {e!r} {e.message}" for e in errors),
        "repr": " ".join(
            f"{x!r} {x!s}" for x in (issued, rotated, fresh, issued.credential, fresh.credential)
        ),
        "auditoria": json.dumps(
            list(AuditEvent.objects.values()), default=str, ensure_ascii=False
        ),
        "credenciales": _table_dump("iot_gateway_credentials"),
        "gateways": _table_dump("iot_gateways"),
        "eventos": _table_dump("audit_events"),
    }
    for secret in secrets_seen:
        for name, content in channels.items():
            assert secret not in content, name
    fingerprints = list(GatewayCredential.objects.values_list("fingerprint", flat=True))
    assert len(fingerprints) == 3
    for fingerprint in fingerprints:
        assert fingerprint not in channels["auditoria"]
        assert fingerprint not in channels["eventos"]


def test_audit_sensitive_key_protection_is_not_weakened(farm_id):
    from django.core.exceptions import ValidationError

    from apps.trazabilidad.services import record_audit_event

    for payload in ({"credential_id": 1}, {"secret": "x"}, {"gateway_token": "x"}):
        with pytest.raises(ValidationError):
            record_audit_event(
                event_type=EventType.IOT_GATEWAY_CREDENTIAL_ISSUED,
                entity_type="iot_gateway_credential",
                entity_id="1",
                after=payload,
            )
