# ruff: noqa: S101, S105, S106
"""Pruebas de F9: reenvío, cambio de rol y cancelación de invitaciones por HTTP.

Propósito: verificar los contratos que consume el frontend (estados, rotación
del token, entrega, auditoría, permisos y rollback) y que ningún token aparece
en respuestas, eventos ni registros.
Contexto: el envío de correo se sustituye siempre por un doble; los tokens se
fijan con un generador determinista. Solo corre en la base de validación o en
el orquestador desechable (guardas de conftest.py).
"""
import hashlib
import json
import logging
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_codes, invitation_services, services
from apps.farms.models import (
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.farms.roles import ROLES
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db

INVITED_EMAIL = "invitada@example.com"


class Delivery:
    """Doble del envío: registra los códigos enviados y devuelve un resultado fijo.

    F10.3.3-A: el correo lleva el código; el token ya no se entrega.
    """

    def __init__(self, outcome=DeliveryOutcome.ACCEPTED):
        self.outcome = outcome
        self.codes: list[str] = []

    def __call__(self, *, email, code, farm_name, role, expires_at):
        self.codes.append(code)
        return self.outcome


@pytest.fixture
def delivery(monkeypatch):
    stub = Delivery()
    monkeypatch.setattr(invitation_services, "_send_invitation", stub)
    return stub


@pytest.fixture
def tokens(monkeypatch):
    """Genera tokens predecibles: tok-1, tok-2, ..."""
    issued: list[str] = []

    def new_token():
        issued.append(f"tok-{len(issued) + 1}-" + "x" * 20)
        return issued[-1]

    monkeypatch.setattr(invitation_services, "_new_token", new_token)
    return issued


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _setup(make_user, login):
    """Administrador con finca y una invitación pendiente creada por el servicio."""
    admin = make_user("11111111")
    farm_id = services.create_farm(user=admin, name="Finca").farm_id
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=INVITED_EMAIL, role=FarmRole.OPERADOR
    )
    return admin, farm_id, invitation


def _invited(make_user):
    return make_user("33333333", correo=INVITED_EMAIL, email_verified=True)


def _accept(login, account, token):
    return login(account).post("/api/v1/invitations/accept/", {"token": token}, format="json")


def _events(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type)


def _resend_url(farm_id, invitation_id):
    return f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/resend/"


def _detail_url(farm_id, invitation_id):
    return f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/"


def _assert_no_secret(texts, secrets_):
    for text in texts:
        for secret in secrets_:
            assert secret not in text


def _event_texts(farm_id):
    return [
        json.dumps(
            {
                "before": event.before_data,
                "after": event.after_data,
                "metadata": event.metadata,
                "reason": event.reason,
            },
            default=str,
        )
        for event in AuditEvent.objects.filter(farm_id=farm_id)
    ]


# --- Reenvío ---------------------------------------------------------------


def test_resend_pending_rotates_token_and_returns_invitation(
    make_user, login, delivery, tokens
):
    admin, farm_id, invitation = _setup(make_user, login)
    old_expiry = invitation.expires_at
    response = login(admin).post(_resend_url(farm_id, invitation.pk), format="json")
    assert response.status_code == 200
    assert response.data["id"] == invitation.pk
    assert response.data["status"] == InvitationStatus.PENDING
    assert response.data["delivery_pending"] is False
    assert response.data["last_sent_at"] is not None
    invitation.refresh_from_db()
    assert invitation.token_hash == _sha(tokens[1])
    assert invitation.expires_at >= old_expiry
    # El correo reenviado lleva un código nuevo; la huella guardada es la suya.
    assert len(delivery.codes) == 2 and delivery.codes[0] != delivery.codes[1]
    assert invitation.code_hash == invitation_codes.code_hash(INVITED_EMAIL, delivery.codes[1])


def test_resend_old_token_invalid_new_token_valid(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    assert login(admin).post(_resend_url(farm_id, invitation.pk)).status_code == 200
    invited = _invited(make_user)
    assert _accept(login, invited, tokens[0]).status_code == 404
    assert _accept(login, invited, tokens[1]).status_code == 200


def test_resend_expired_invitation_conflict(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    FarmInvitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    api = login(admin)
    response = api.post(_resend_url(farm_id, invitation.pk))
    assert response.status_code == 409
    assert response.data == {"error": "la invitación ya no está pendiente"}
    invitation.refresh_from_db()
    # El 409 revierte la transacción completa, incluida la expiración perezosa:
    # no se renueva ni se rota nada, y el listado la expira de forma definitiva.
    assert invitation.token_hash == _sha(tokens[0])
    assert len(delivery.codes) == 1
    listing = api.get(f"/api/v1/farms/{farm_id}/invitations/")
    assert listing.data[0]["status"] == InvitationStatus.EXPIRED
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.EXPIRED


def test_resend_cancelled_invitation_conflict(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    invitation_services.cancel_invitation(user=admin, farm_id=farm_id, invitation_id=invitation.pk)
    response = login(admin).post(_resend_url(farm_id, invitation.pk))
    assert response.status_code == 409
    assert len(delivery.codes) == 1


def test_resend_successful_delivery_records_events(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    login(admin).post(_resend_url(farm_id, invitation.pk))
    assert _events(farm_id, EventType.INVITATION_RESEND_STARTED).count() == 1
    resent = _events(farm_id, EventType.INVITATION_RESENT).get()
    assert resent.entity_id == str(invitation.pk)
    assert resent.actor_id == admin.pk


def test_resend_delivery_rejected_returns_502_and_stays_pending(
    make_user, login, delivery, tokens
):
    admin, farm_id, invitation = _setup(make_user, login)
    invitation.refresh_from_db()
    previous_sent = invitation.last_sent_at
    delivery.outcome = DeliveryOutcome.REJECTED
    response = login(admin).post(_resend_url(farm_id, invitation.pk))
    assert response.status_code == 502
    assert response.json() == {
        "error": "no se pudo entregar la invitación",
        "invitation_id": invitation.pk,
    }
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.delivery_pending is False
    # El token sí rotó: el enlace anterior ya no vale y last_sent_at no cambia.
    assert invitation.token_hash == _sha(tokens[1])
    assert invitation.last_sent_at == previous_sent
    assert _events(farm_id, EventType.INVITATION_DELIVERY_FAILED).count() == 1
    assert not _events(farm_id, EventType.INVITATION_RESENT).exists()
    _assert_no_secret([response.content.decode()], [tokens[1], _sha(tokens[1])])


def test_resend_unauthorized(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    operator = make_user("22222222")
    FarmMembership.objects.create(farm_id=farm_id, user=operator, role=FarmRole.OPERADOR)
    outsider = make_user("44444444")
    assert login(operator).post(_resend_url(farm_id, invitation.pk)).status_code == 403
    assert login(outsider).post(_resend_url(farm_id, invitation.pk)).status_code == 404
    # Cliente nuevo: el de login conserva la cookie de sesión.
    assert APIClient().post(_resend_url(farm_id, invitation.pk)).status_code == 401
    invitation.refresh_from_db()
    assert invitation.token_hash == _sha(tokens[0])


def test_resend_unknown_invitation_404(make_user, login, delivery, tokens):
    admin, farm_id, _ = _setup(make_user, login)
    assert login(admin).post(_resend_url(farm_id, 999999)).status_code == 404


# --- Cambio de rol ---------------------------------------------------------


def test_change_role_valid(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    response = login(admin).patch(
        _detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["role"] == FarmRole.CONSULTA
    assert response.data["status"] == InvitationStatus.PENDING
    invitation.refresh_from_db()
    assert invitation.role == FarmRole.CONSULTA


def test_change_role_unknown_role_400(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    response = login(admin).patch(
        _detail_url(farm_id, invitation.pk), {"role": "superadmin"}, format="json"
    )
    assert response.status_code == 400
    invitation.refresh_from_db()
    assert invitation.role == FarmRole.OPERADOR
    assert invitation.token_hash == _sha(tokens[0])


def test_change_role_not_modifiable_when_accepted(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    invited = _invited(make_user)
    assert _accept(login, invited, tokens[0]).status_code == 200
    response = login(admin).patch(
        _detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json"
    )
    assert response.status_code == 409
    membership = FarmMembership.objects.get(farm_id=farm_id, user=invited)
    assert membership.role == FarmRole.OPERADOR


def test_change_role_rotates_token_old_invalid(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    login(admin).patch(_detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json")
    invitation.refresh_from_db()
    assert invitation.token_hash == _sha(tokens[1])
    assert invitation.code_hash == invitation_codes.code_hash(INVITED_EMAIL, delivery.codes[-1])
    invited = _invited(make_user)
    assert _accept(login, invited, tokens[0]).status_code == 404
    accepted = _accept(login, invited, tokens[1])
    assert accepted.status_code == 200
    assert accepted.data["role"] == FarmRole.CONSULTA


def test_change_role_audit_before_after(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    login(admin).patch(_detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json")
    event = _events(farm_id, EventType.INVITATION_ROLE_CHANGED).get()
    assert event.before_data == {"role": FarmRole.OPERADOR}
    assert event.after_data == {"role": FarmRole.CONSULTA}
    assert event.reason == ""


def test_change_role_does_not_touch_existing_membership(make_user, login, delivery, tokens):
    """Afecta solo a la invitación: una membresía revocada previa conserva su rol."""
    admin, farm_id, invitation = _setup(make_user, login)
    invited = _invited(make_user)
    previous = FarmMembership.objects.create(
        farm_id=farm_id,
        user=invited,
        role=FarmRole.CONSULTA,
        status=MembershipStatus.REVOKED,
    )
    login(admin).patch(
        _detail_url(farm_id, invitation.pk), {"role": "administrador"}, format="json"
    )
    previous.refresh_from_db()
    assert previous.role == FarmRole.CONSULTA
    assert previous.status == MembershipStatus.REVOKED


def test_change_role_delivery_rejected_502_role_kept(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    delivery.outcome = DeliveryOutcome.REJECTED
    response = login(admin).patch(
        _detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json"
    )
    assert response.status_code == 502
    assert response.json()["invitation_id"] == invitation.pk
    invitation.refresh_from_db()
    assert invitation.role == FarmRole.CONSULTA
    assert invitation.status == InvitationStatus.PENDING


def test_change_role_unauthorized(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    operator = make_user("22222222")
    FarmMembership.objects.create(farm_id=farm_id, user=operator, role=FarmRole.OPERADOR)
    response = login(operator).patch(
        _detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json"
    )
    assert response.status_code == 403
    invitation.refresh_from_db()
    assert invitation.role == FarmRole.OPERADOR


def test_change_role_audit_failure_rolls_back(make_user, login, delivery, tokens, monkeypatch):
    admin, farm_id, invitation = _setup(make_user, login)
    invitation.refresh_from_db()
    before = (invitation.role, invitation.token_hash, invitation.expires_at)

    def fail(**kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(invitation_services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        invitation_services.change_invitation_role(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
        )
    invitation.refresh_from_db()
    assert (invitation.role, invitation.token_hash, invitation.expires_at) == before
    assert len(delivery.codes) == 1


# --- Catálogo de roles ----------------------------------------------------


def test_role_catalog_matches_invitation_contract(auth_client):
    assert {role["id"] for role in ROLES} == set(FarmRole.values)
    response = auth_client.get("/api/v1/configuracion/roles-permisos/")
    assert response.status_code == 200
    assert [role["id"] for role in response.data["roles"]] == [
        FarmRole.ADMINISTRADOR,
        FarmRole.OPERADOR,
        FarmRole.CONSULTA,
    ]


# --- Cancelación -----------------------------------------------------------


def test_cancel_valid_returns_204(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    response = login(admin).delete(_detail_url(farm_id, invitation.pk))
    assert response.status_code == 204
    assert response.content == b""
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.CANCELLED
    assert invitation.cancelled_at is not None
    assert invitation.token_hash == ""
    assert invitation.delivery_pending is False


def test_cancel_already_cancelled_conflict(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    assert login(admin).delete(_detail_url(farm_id, invitation.pk)).status_code == 204
    assert login(admin).delete(_detail_url(farm_id, invitation.pk)).status_code == 409
    assert _events(farm_id, EventType.INVITATION_CANCELLED).count() == 1


def test_cancel_accepted_conflict(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    invited = _invited(make_user)
    assert _accept(login, invited, tokens[0]).status_code == 200
    assert login(admin).delete(_detail_url(farm_id, invitation.pk)).status_code == 409
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.ACCEPTED


def test_cancel_invalidates_token_and_later_accept_rejected(
    make_user, login, delivery, tokens
):
    admin, farm_id, invitation = _setup(make_user, login)
    login(admin).delete(_detail_url(farm_id, invitation.pk))
    invited = _invited(make_user)
    response = _accept(login, invited, tokens[0])
    assert response.status_code == 404
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=invited).exists()
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.CANCELLED


def test_cancel_audit_event(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    login(admin).delete(_detail_url(farm_id, invitation.pk))
    event = _events(farm_id, EventType.INVITATION_CANCELLED).get()
    assert event.entity_id == str(invitation.pk)
    assert event.after_data == {"status": InvitationStatus.CANCELLED}
    assert event.actor_id == admin.pk


def test_cancel_unauthorized(make_user, login, delivery, tokens):
    admin, farm_id, invitation = _setup(make_user, login)
    operator = make_user("22222222")
    FarmMembership.objects.create(farm_id=farm_id, user=operator, role=FarmRole.OPERADOR)
    assert login(operator).delete(_detail_url(farm_id, invitation.pk)).status_code == 403
    assert APIClient().delete(_detail_url(farm_id, invitation.pk)).status_code == 401
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING


def test_cancel_audit_failure_rolls_back(make_user, login, delivery, tokens, monkeypatch):
    admin, farm_id, invitation = _setup(make_user, login)

    def fail(**kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(invitation_services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        invitation_services.cancel_invitation(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk
        )
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.token_hash == _sha(tokens[0])


# --- Tokens fuera de respuestas, eventos y registros -----------------------


def test_tokens_absent_from_responses_events_and_logs(
    make_user, login, delivery, tokens, caplog
):
    caplog.set_level(logging.DEBUG)
    admin, farm_id, invitation = _setup(make_user, login)
    api = login(admin)
    bodies = [
        api.post(_resend_url(farm_id, invitation.pk)).content.decode(),
        api.patch(
            _detail_url(farm_id, invitation.pk), {"role": "consulta"}, format="json"
        ).content.decode(),
        api.get(f"/api/v1/farms/{farm_id}/invitations/").content.decode(),
        api.get(f"/api/v1/farms/{farm_id}/history/").content.decode(),
        api.delete(_detail_url(farm_id, invitation.pk)).content.decode(),
    ]
    codes = [*delivery.codes, *(code.replace("-", "") for code in delivery.codes)]
    code_hashes = [invitation_codes.code_hash(INVITED_EMAIL, code) for code in delivery.codes]
    secrets_ = [*tokens, *(_sha(token) for token in tokens), *codes, *code_hashes]
    assert len(tokens) == 3
    assert len(delivery.codes) == 3
    _assert_no_secret(bodies, secrets_)
    _assert_no_secret(_event_texts(farm_id), secrets_)
    _assert_no_secret([caplog.text], secrets_)
