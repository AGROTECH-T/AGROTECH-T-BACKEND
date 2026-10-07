"""Registro con celular y correo obligatorios.

Propósito: rechazar un alta a la que le falta el celular o el correo.
Contexto: ambos datos son necesarios para recuperar la cuenta.
@author Cristian Deysdayr Jimenez
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.constants import CREDENTIAL_FIELD, REGISTER_CREDENTIAL

BODY = {
    "first_name": "Ana",
    "last_name": "Perez",
    "identification": "11223344",
    "phone": "3001234567",
    "correo": "ana@example.com",
    CREDENTIAL_FIELD: REGISTER_CREDENTIAL,
}


@pytest.mark.django_db
def test_register_requires_phone_and_mail():
    """Sin celular o sin correo el alta responde 400."""
    client = APIClient()
    missing_phone = client.post("/api/v1/accounts/", {**BODY, "phone": ""}, format="json")
    missing_mail = client.post("/api/v1/accounts/", {**BODY, "correo": ""}, format="json")
    assert missing_phone.status_code == 400
    assert missing_mail.status_code == 400
