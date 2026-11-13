"""Prueba el registro de auditoría de apertura y cierre de sesión.

@author Cristian Deysdayr Jimenez
"""

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.accounts.models import Session, User
from apps.accounts.tests.constants import CREDENTIAL_FIELD, VALID_CREDENTIAL, WRONG_CREDENTIAL
from apps.trazabilidad.models import AuditEvent, EventType

LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP de DRF sin verificación CSRF (para flujo de sesión)."""
    return APIClient()


def _credentials(user, password=VALID_CREDENTIAL) -> dict:
    """Credenciales del usuario de la fixture."""
    return {"identification": user.identification, CREDENTIAL_FIELD: password}


def _make_user(identification: str) -> User:
    """Crea una cuenta adicional con el identificador indicado."""
    return User.objects.create(
        first_name="Otro",
        last_name="Usuario",
        identification=identification,
        password_hash=make_password(VALID_CREDENTIAL),
    )


def _login_token(client: APIClient, user) -> str:
    """Autentica una cuenta y retorna su token Bearer."""
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    return response.data["token"]


@pytest.mark.django_db
def test_login_records_session_opened(client, user):
    """El login exitoso registra un evento SESSION_OPENED."""
    response = client.post(LOGIN_URL, _credentials(user), format="json")
    assert response.status_code == 200
    event = AuditEvent.objects.get(event_type=EventType.SESSION_OPENED)
    assert event.actor_id == user.pk
    assert event.actor_identifier == user.pk
    assert event.entity_type == "Session"
    assert event.entity_id == str(user.pk)


@pytest.mark.django_db
def test_logout_records_session_closed(client, user):
    """El logout exitoso registra un evento SESSION_CLOSED."""
    client.post(LOGIN_URL, _credentials(user), format="json")
    response = client.post(LOGOUT_URL, format="json")
    assert response.status_code == 200
    event = AuditEvent.objects.get(event_type=EventType.SESSION_CLOSED)
    assert event.actor_id == user.pk
    assert event.entity_type == "Session"
    assert event.entity_id == str(user.pk)


@pytest.mark.django_db
def test_failed_login_does_not_record_session_opened(client, user):
    """Un login fallido no genera eventos de apertura de sesión."""
    response = client.post(LOGIN_URL, _credentials(user, WRONG_CREDENTIAL), format="json")
    assert response.status_code == 401
    assert not AuditEvent.objects.filter(event_type=EventType.SESSION_OPENED).exists()


@pytest.mark.django_db
def test_session_events_exclude_sensitive_data(client, user):
    """Los eventos de sesión no conservan tokens ni claves de sesión."""
    client.post(LOGIN_URL, _credentials(user), format="json")
    event = AuditEvent.objects.get(event_type=EventType.SESSION_OPENED)
    assert event.before_data is None
    assert event.after_data is None
    assert event.metadata == {}


@pytest.mark.django_db
def test_bearer_only_logout_does_not_record_session_closed(client, user):
    """Un logout solo por Bearer, sin sesión Django, no registra SESSION_CLOSED."""
    token = _login_token(client, user)
    fresh = APIClient()
    response = fresh.post(LOGOUT_URL, HTTP_AUTHORIZATION=f"Bearer {token}", format="json")
    assert response.status_code == 200
    assert not AuditEvent.objects.filter(event_type=EventType.SESSION_CLOSED).exists()


@pytest.mark.django_db
def test_bearer_only_logout_still_revokes_jwe(client, user):
    """El logout solo por Bearer conserva la revocación JWE y no registra cierre."""
    token = _login_token(client, user)
    assert Session.objects.filter(user=user, closed_at__isnull=True).exists()
    fresh = APIClient()
    response = fresh.post(LOGOUT_URL, HTTP_AUTHORIZATION=f"Bearer {token}", format="json")
    assert response.status_code == 200
    assert not Session.objects.filter(user=user, closed_at__isnull=True).exists()
    assert not AuditEvent.objects.filter(event_type=EventType.SESSION_CLOSED).exists()


@pytest.mark.django_db
def test_logout_with_cookie_and_bearer_records_single_session_closed(client, user):
    """Cookie Django y Bearer del mismo usuario registran un único SESSION_CLOSED."""
    token = _login_token(client, user)
    assert client.session["user_id"] == user.pk
    response = client.post(LOGOUT_URL, HTTP_AUTHORIZATION=f"Bearer {token}", format="json")
    assert response.status_code == 200
    events = AuditEvent.objects.filter(event_type=EventType.SESSION_CLOSED)
    assert events.count() == 1
    assert events.get().actor_id == user.pk
    assert "user_id" not in client.session


@pytest.mark.django_db
def test_logout_bearer_a_with_django_session_b_does_not_record_closed(client, user):
    """Bearer del usuario A con cookie Django del usuario B no registra un cierre falso."""
    other = _make_user("87654321")
    token = _login_token(client, user)
    mixed = APIClient()
    session = mixed.session
    session["user_id"] = other.pk
    session.save()
    response = mixed.post(LOGOUT_URL, HTTP_AUTHORIZATION=f"Bearer {token}", format="json")
    assert response.status_code == 200
    assert not AuditEvent.objects.filter(event_type=EventType.SESSION_CLOSED).exists()
    assert not Session.objects.filter(user=user, closed_at__isnull=True).exists()
    assert "user_id" not in mixed.session
