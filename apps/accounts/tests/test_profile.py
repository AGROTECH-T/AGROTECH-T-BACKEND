"""Prueba la edición del perfil autenticado.

@author Cristian Deysdayr Jimenez
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.constants import CREDENTIAL_FIELD, VALID_CREDENTIAL


@pytest.fixture
def client() -> APIClient:
    """Retorna un cliente HTTP de DRF sin sesión."""
    return APIClient()


@pytest.fixture
def auth_client(user) -> APIClient:
    """Retorna un cliente con una sesión ya abierta."""
    client = APIClient()
    login = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, CREDENTIAL_FIELD: VALID_CREDENTIAL},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['token']}")
    return client


@pytest.mark.django_db
def test_update_profile_changes_names_mail_and_phone(auth_client, user):
    """El perfil acepta nombre, apellidos, correo y celular."""
    response = auth_client.patch(
        "/api/v1/auth/me/",
        {
            "first_name": "María",
            "last_name": "Rodríguez",
            "correo": "nueva@example.com",
            "phone": "3109998877",
        },
        format="json",
    )
    user.refresh_from_db()
    assert response.status_code == 200
    assert response.data["first_name"] == "María"
    assert response.data["correo"] == "nueva@example.com"
    assert user.phone == "3109998877"


@pytest.mark.django_db
def test_update_profile_keeps_identification(auth_client, user):
    """El documento no cambia aunque se intente enviar."""
    response = auth_client.patch(
        "/api/v1/auth/me/",
        {"identification": "99999999", "first_name": "Ana María"},
        format="json",
    )
    user.refresh_from_db()
    assert response.status_code == 200
    assert user.identification == "12345678"
    assert response.data["identification"] == "12345678"


@pytest.mark.django_db
def test_update_profile_rejects_invalid_data(auth_client):
    """Un correo o un nombre inválidos se rechazan sin guardar."""
    mail = auth_client.patch("/api/v1/auth/me/", {"correo": "sin-arroba"}, format="json")
    name = auth_client.patch("/api/v1/auth/me/", {"first_name": "A1"}, format="json")
    assert mail.status_code == 400
    assert name.status_code == 400


@pytest.mark.django_db
def test_update_profile_requires_session(client):
    """Sin sesión el perfil no se puede editar."""
    response = client.patch("/api/v1/auth/me/", {"first_name": "Ana"}, format="json")
    assert response.status_code in {401, 403}
