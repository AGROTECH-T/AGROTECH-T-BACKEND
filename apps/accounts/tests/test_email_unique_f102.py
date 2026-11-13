# ruff: noqa: S101
"""Pruebas de F10.2: una sola cuenta por correo electrónico.

Propósito: el registro y el cambio de correo rechazan un correo que otra
cuenta ya usa, comparando sin mayúsculas ni espacios exteriores.
Contexto: garantía en el servicio; no hay restricción única en la base de
datos (requeriría migración autorizada) ni se modifican cuentas existentes.
"""
import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.accounts.services import auth_service
from apps.accounts.tests.constants import CREDENTIAL_FIELD, REGISTER_CREDENTIAL, VALID_CREDENTIAL

pytestmark = pytest.mark.django_db

BODY = {
    "first_name": "Luisa",
    "last_name": "Gomez",
    "identification": "55667788",
    "phone": "3007654321",
    "correo": "luisa@example.com",
    CREDENTIAL_FIELD: REGISTER_CREDENTIAL,
}


def _account(identification, correo, **kwargs):
    return User.objects.create(
        first_name="Ana",
        last_name="Pérez",
        identification=identification,
        phone="3001234567",
        correo=correo,
        password_hash=make_password(VALID_CREDENTIAL),
        **kwargs,
    )


@pytest.mark.parametrize("variant", ["ana@example.com", "  ANA@Example.COM  "])
def test_register_rejects_email_in_use(user, variant):
    response = APIClient().post("/api/v1/accounts/", {**BODY, "correo": variant}, format="json")
    assert response.status_code == 409
    assert response.data == {"error": auth_service.EMAIL_TAKEN}
    assert not User.objects.filter(identification=BODY["identification"]).exists()
    assert User.objects.filter(correo="ana@example.com").count() == 1


def test_register_accepts_new_email(user):
    response = APIClient().post("/api/v1/accounts/", BODY, format="json")
    assert response.status_code == 201
    assert User.objects.get(identification=BODY["identification"]).correo == "luisa@example.com"


def test_email_in_use_ignores_legacy_case_and_spaces():
    _account("10000001", " Mixta@Example.com ")
    assert auth_service.email_in_use("mixta@example.com") is True
    assert auth_service.email_in_use("otra@example.com") is False
    assert auth_service.email_in_use("") is False


def test_profile_cannot_take_another_accounts_email(user):
    other = _account("10000002", "otra@example.com", email_verified=True)
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    with pytest.raises(AppError) as exc:
        auth_service.update_profile(user, {"correo": " OTRA@example.com "})
    assert exc.value.status_code == 409
    assert exc.value.message == auth_service.EMAIL_TAKEN
    user.refresh_from_db()
    other.refresh_from_db()
    assert user.correo == "ana@example.com"
    assert user.email_verified is True
    assert other.correo == "otra@example.com"


def test_profile_api_reports_email_taken(user):
    _account("10000003", "otra@example.com")
    client = APIClient()
    login = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, CREDENTIAL_FIELD: VALID_CREDENTIAL},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['token']}")
    response = client.patch("/api/v1/auth/me/", {"correo": "otra@example.com"}, format="json")
    assert response.status_code == 409
    assert response.data == {"error": auth_service.EMAIL_TAKEN}


def test_profile_keeps_own_email_in_other_case(user):
    auth_service.update_profile(user, {"correo": "ANA@example.com", "first_name": "Ana"})
    user.refresh_from_db()
    assert user.correo == "ana@example.com"


def test_existing_duplicates_are_left_untouched():
    first = _account("10000004", "dup@example.com")
    second = _account("10000005", "dup@example.com")
    auth_service.update_profile(first, {"first_name": "Nuevo"})
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.first_name == "Nuevo"
    assert first.correo == second.correo == "dup@example.com"
