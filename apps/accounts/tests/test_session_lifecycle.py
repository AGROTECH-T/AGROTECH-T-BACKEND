"""Prueba la integración de sesiones Django en login y logout.

@author Cristian Deysdayr Jimenez
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Session
from apps.accounts.tests.constants import CREDENTIAL_FIELD, VALID_CREDENTIAL

LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"
CSRF_URL = "/api/v1/auth/csrf/"


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP de DRF sin verificación CSRF (para flujo de sesión)."""
    return APIClient()


def _credentials(user) -> dict:
    """Credenciales válidas del usuario de la fixture."""
    return {"identification": user.identification, CREDENTIAL_FIELD: VALID_CREDENTIAL}


@pytest.mark.django_db
def test_login_creates_django_session_with_user_id(client, user):
    """El login guarda ``user_id`` en la sesión Django."""
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    assert client.session["user_id"] == user.pk


@pytest.mark.django_db
def test_login_preserves_token_and_ok(client, user):
    """La respuesta conserva el token JWE y añade ``ok``."""
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    assert response.data["ok"] is True
    assert response.data["token"]


@pytest.mark.django_db
def test_login_rotates_session_key(client, user):
    """El login renueva la clave de sesión (anti session-fixation)."""
    session = client.session
    session["before"] = "x"
    session.save()
    old_key = client.session.session_key
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    assert client.session.session_key != old_key
    assert client.session["user_id"] == user.pk


@pytest.mark.django_db
def test_login_rotates_csrf_token(client, user):
    """El login renueva el secreto CSRF."""
    before = client.get(CSRF_URL).cookies["csrftoken"].value
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    assert client.cookies["csrftoken"].value != before


@pytest.mark.django_db
def test_logout_destroys_django_session(client, user):
    """El logout elimina ``user_id`` de la sesión Django."""
    client.post(LOGIN_URL, _credentials(user), format="json")
    assert client.session["user_id"] == user.pk
    response = client.post(LOGOUT_URL, format="json")
    assert response.status_code == 200
    assert "user_id" not in client.session


@pytest.mark.django_db
def test_session_after_logout_gets_401(client, user):
    """Tras el logout, la sesión Django ya no autentica."""
    client.post(LOGIN_URL, _credentials(user), format="json")
    assert client.get(ME_URL).status_code == 200
    client.post(LOGOUT_URL, format="json")
    assert client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_logout_preserves_jwe_revocation(client, user):
    """El logout conserva la revocación de la sesión JWE."""
    client.post(LOGIN_URL, _credentials(user), format="json")
    assert Session.objects.filter(user=user, closed_at__isnull=True).exists()
    client.post(LOGOUT_URL, format="json")
    assert not Session.objects.filter(user=user, closed_at__isnull=True).exists()


@pytest.mark.django_db
def test_me_works_with_session_and_bearer(client, user):
    """``me`` autentica tanto por sesión Django como por Bearer JWE."""
    token = client.post(LOGIN_URL, _credentials(user), format="json").data["token"]
    assert client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200
    assert client.get(ME_URL).status_code == 200
