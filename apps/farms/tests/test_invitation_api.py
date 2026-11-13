# ruff: noqa: S101
"""Pruebas HTTP de invitaciones y configuración de límites de frecuencia."""
import hashlib
import json

import pytest
from django.conf import settings

from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_services
from apps.trazabilidad.models import AuditEvent

pytestmark = pytest.mark.django_db


def test_invitation_throttle_scopes_configured():
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["invitation_create"] == "30/hour"
    assert rates["invitation_resend"] == "10/hour"
    assert rates["invitation_accept"] == "10/hour"
    assert rates["verification_request"] == "5/hour"
    assert rates["verification_confirm"] == "10/minute"


def test_create_and_list_invitations_api(auth_client, user, monkeypatch):
    monkeypatch.setattr(
        "apps.farms.invitation_services._send_invitation",
        lambda **kw: DeliveryOutcome.ACCEPTED,
    )
    farm_resp = auth_client.post("/api/v1/farms/", {"name": "Finca"}, format="json")
    farm_id = farm_resp.data["id"]

    create = auth_client.post(
        f"/api/v1/farms/{farm_id}/invitations/",
        {"email": "X@Example.com", "role": "operador"},
        format="json",
    )
    assert create.status_code == 201
    assert create.data["email"] == "x@example.com"
    assert create.data["status"] == "pending"

    listing = auth_client.get(f"/api/v1/farms/{farm_id}/invitations/")
    assert listing.status_code == 200
    assert len(listing.data) == 1


def test_accept_invitation_api(auth_client, user, make_user, login, monkeypatch):
    monkeypatch.setattr(
        "apps.farms.invitation_services._send_invitation",
        lambda **kw: DeliveryOutcome.ACCEPTED,
    )
    monkeypatch.setattr(
        "apps.farms.invitation_services._new_token", lambda: "fixed-token-123"
    )
    farm_resp = auth_client.post("/api/v1/farms/", {"name": "Finca"}, format="json")
    farm_id = farm_resp.data["id"]

    invited = make_user("22222222", correo="invited@example.com", email_verified=True)

    invitation_services.create_invitation(
        user=user, farm_id=farm_id, email="invited@example.com", role="operador"
    )

    invited_client = login(invited)
    response = invited_client.post(
        "/api/v1/invitations/accept/", {"token": "fixed-token-123"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["farm_id"] == farm_id
    assert response.data["role"] == "operador"


def test_invitation_api_does_not_expose_tokens(auth_client, user, monkeypatch):
    secret_token = "super-secret-token-value"
    secret_hash = hashlib.sha256(secret_token.encode()).hexdigest()
    monkeypatch.setattr(
        "apps.farms.invitation_services._send_invitation",
        lambda **kw: DeliveryOutcome.ACCEPTED,
    )
    monkeypatch.setattr(
        "apps.farms.invitation_services._new_token", lambda: secret_token
    )
    farm_resp = auth_client.post("/api/v1/farms/", {"name": "Finca"}, format="json")
    farm_id = farm_resp.data["id"]

    create = auth_client.post(
        f"/api/v1/farms/{farm_id}/invitations/",
        {"email": "x@example.com", "role": "operador"},
        format="json",
    )
    assert create.status_code == 201
    assert "token" not in create.data
    assert "token_hash" not in create.data
    assert secret_token not in create.content.decode()
    assert secret_hash not in create.content.decode()

    listing = auth_client.get(f"/api/v1/farms/{farm_id}/invitations/")
    assert listing.status_code == 200
    for item in listing.data:
        assert "token" not in item
        assert "token_hash" not in item
    assert secret_token not in listing.content.decode()
    assert secret_hash not in listing.content.decode()

    invitation_id = create.data["id"]

    patch = auth_client.patch(
        f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/",
        {"role": "consulta"},
        format="json",
    )
    assert patch.status_code == 200
    assert "token" not in patch.data
    assert "token_hash" not in patch.data
    assert secret_token not in patch.content.decode()
    assert secret_hash not in patch.content.decode()

    resend = auth_client.post(
        f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/resend/",
        format="json",
    )
    assert resend.status_code == 200
    assert "token" not in resend.data
    assert "token_hash" not in resend.data
    assert secret_token not in resend.content.decode()
    assert secret_hash not in resend.content.decode()

    for event in AuditEvent.objects.filter(farm_id=farm_id):
        payload = json.dumps(
            {
                "before": event.before_data,
                "after": event.after_data,
                "metadata": event.metadata,
            },
            default=str,
        )
        assert secret_token not in payload
        assert secret_hash not in payload
