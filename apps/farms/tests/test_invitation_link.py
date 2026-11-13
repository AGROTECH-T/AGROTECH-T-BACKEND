# ruff: noqa: S101
"""Pruebas del enlace de aceptación de invitaciones (P3/H1).

Desde F10.3.3-A el enlace solo existe para invitaciones antiguas: ningún
correo nuevo lo incluye. ``_accept_link`` conserva su formato heredado.
"""
import pytest
from django.test import override_settings

from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_services
from apps.farms.invitation_services import _accept_link

FRONTEND_URL = "https://agrotech.example.com/app"


def test_accept_link_basic():
    with override_settings(FRONTEND_URL=FRONTEND_URL, DEBUG=False):
        assert _accept_link("tok") == f"{FRONTEND_URL}/invitations/accept#token=tok"


def test_accept_link_preserves_base_path():
    with override_settings(FRONTEND_URL=FRONTEND_URL, DEBUG=False):
        link = _accept_link("token-123")
    assert link.startswith(f"{FRONTEND_URL}/invitations/accept#token=")
    assert link.split("#token=", 1)[1] == "token-123"


def test_accept_link_normalizes_trailing_slash():
    with override_settings(FRONTEND_URL=f"{FRONTEND_URL}///", DEBUG=False):
        assert _accept_link("tok") == f"{FRONTEND_URL}/invitations/accept#token=tok"


def test_accept_link_has_no_duplicate_slashes():
    with override_settings(FRONTEND_URL=f"{FRONTEND_URL}/", DEBUG=False):
        link = _accept_link("tok")
    assert f"{FRONTEND_URL}//invitations" not in link
    assert link == f"{FRONTEND_URL}/invitations/accept#token=tok"


def test_accept_link_debug_empty_uses_local_fallback():
    with override_settings(FRONTEND_URL="", DEBUG=True):
        assert (
            _accept_link("tok")
            == "http://localhost:3000/invitations/accept#token=tok"
        )


@pytest.mark.django_db
def test_accept_link_does_not_depend_on_host_header(auth_client, monkeypatch):
    captured = []

    def fake_deliver_email(*, to, subject, text, html, timeout=15):
        captured.append(text)
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(invitation_services, "deliver_email", fake_deliver_email)
    monkeypatch.setattr(invitation_services, "_new_token", lambda: "fixed-token")

    with override_settings(FRONTEND_URL=FRONTEND_URL, DEBUG=False):
        farm = auth_client.post("/api/v1/farms/", {"name": "Finca"}, format="json")
        assert farm.status_code == 201
        farm_id = farm.data["id"]
        first = auth_client.post(
            f"/api/v1/farms/{farm_id}/invitations/",
            {"email": "one@example.com", "role": "operador"},
            format="json",
            HTTP_HOST="localhost",
        )
        second = auth_client.post(
            f"/api/v1/farms/{farm_id}/invitations/",
            {"email": "two@example.com", "role": "operador"},
            format="json",
            HTTP_HOST="127.0.0.1",
        )

    # F10.3.3-A: los correos nuevos llevan un código y ningún enlace, así que
    # el Host de la petición no puede influir en su contenido.
    assert first.status_code == 201
    assert second.status_code == 201
    assert len(captured) == 2
    for text in captured:
        assert "fixed-token" not in text
        assert "http" not in text
        assert "localhost" not in text and "127.0.0.1" not in text
        assert FRONTEND_URL not in text
