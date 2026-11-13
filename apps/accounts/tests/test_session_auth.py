"""Prueba la autenticación por sesión Django y el endpoint CSRF.

@author Cristian Deysdayr Jimenez
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

ME_URL = "/api/v1/auth/me/"
CSRF_URL = "/api/v1/auth/csrf/"


@pytest.fixture
def client() -> APIClient:
    """Retorna un cliente con verificación CSRF activa."""
    return APIClient(enforce_csrf_checks=True)


def _login_session(client: APIClient, user: User) -> APIClient:
    """Registra ``user_id`` en la sesión Django del cliente."""
    session = client.session
    session["user_id"] = user.pk
    session.save()
    return client


def _csrf_token(client: APIClient) -> str:
    """Obtiene el token CSRF desde el endpoint oficial."""
    response = client.get(CSRF_URL)
    assert response.status_code == 200
    return response.data["token"]


# --- Endpoint CSRF ---


@pytest.mark.django_db
def test_csrf_endpoint_returns_token_without_auth(client):
    """El token se entrega en el cuerpo sin exigir autenticación."""
    response = client.get(CSRF_URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert set(response.data.keys()) == {"token"}
    assert response.data["token"]
    assert "csrftoken" in client.cookies


@pytest.mark.django_db
def test_csrf_endpoint_does_not_expose_session_data(client):
    """La respuesta solo contiene el token, sin datos de sesión."""
    response = client.get(CSRF_URL)
    assert response.status_code == 200
    assert response.data == {"token": response.data["token"]}


# --- SessionCookieAuthentication ---


@pytest.mark.django_db
def test_unauthenticated_without_session(client):
    """Sin sesión ni credencial Bearer, la respuesta es 401."""
    assert client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_unauthenticated_when_session_has_no_user_id(client):
    """Una sesión sin ``user_id`` no autentica."""
    session = client.session
    session["other"] = "x"
    session.save()
    assert client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_unauthenticated_when_user_id_does_not_exist(client):
    """Un ``user_id`` inexistente no autentica."""
    session = client.session
    session["user_id"] = 999999
    session.save()
    assert client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_rejects_inactive_user(client, user):
    """Un usuario inactivo no autentica."""
    user.is_active = False
    user.save(update_fields=["is_active"])
    _login_session(client, user)
    assert client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_authenticates_valid_user(client, user):
    """Un usuario activo con sesión válida se autentica."""
    _login_session(client, user)
    response = client.get(ME_URL)
    assert response.status_code == 200
    assert response.data["identification"] == user.identification


# --- Métodos HTTP y CSRF ---


@pytest.mark.django_db
def test_get_does_not_require_csrf(client, user):
    """GET no exige token CSRF."""
    _login_session(client, user)
    assert client.get(ME_URL).status_code == 200


@pytest.mark.django_db
def test_options_does_not_require_csrf(client, user):
    """OPTIONS no exige token CSRF."""
    _login_session(client, user)
    assert client.options(ME_URL).status_code == 200


@pytest.mark.django_db
def test_head_does_not_require_csrf(client, user):
    """HEAD no es bloqueado por CSRF."""
    _login_session(client, user)
    assert client.head(ME_URL).status_code != 403


@pytest.mark.django_db
def test_write_requires_csrf(client, user):
    """Una escritura sin token CSRF es rechazada con 403."""
    _login_session(client, user)
    response = client.post(ME_URL, {}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_write_with_csrf_passes_check(client, user):
    """Una escritura con token CSRF válido supera la verificación."""
    _login_session(client, user)
    token = _csrf_token(client)
    response = client.post(ME_URL, {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 405
