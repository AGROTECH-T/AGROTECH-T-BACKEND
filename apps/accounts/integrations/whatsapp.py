"""Envía y verifica códigos de acceso con Twilio Verify para WhatsApp.

@author Cristian Deysdayr Jimenez
"""

import os

import requests

from apps.accounts.errors import AppError


def send_code(phone: str) -> None:
    """Solicita a Twilio Verify el envío de un OTP por WhatsApp."""
    response = _twilio_request(
        "Verifications",
        {"To": _e164(phone), "Channel": "whatsapp"},
    )
    if response.status_code >= 300:
        raise AppError("no se pudo enviar el codigo por WhatsApp", 503)


def check_code(phone: str, digits: str) -> bool:
    """Comprueba un OTP de WhatsApp contra Twilio Verify."""
    response = _twilio_request(
        "VerificationCheck",
        {"To": _e164(phone), "Code": digits},
    )
    return response.status_code < 300 and response.json().get("status") == "approved"


def _twilio_request(resource: str, data: dict) -> requests.Response:
    """Ejecuta una petición autenticada a Twilio Verify."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    service = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")
    if not all((sid, token, service)):
        raise AppError("falta configurar Twilio para WhatsApp", 503)
    endpoint = f"https://verify.twilio.com/v2/Services/{service}/{resource}"
    try:
        return requests.post(endpoint, data=data, auth=(sid, token), timeout=10)
    except requests.RequestException as exc:
        raise AppError("no se pudo enviar el codigo por WhatsApp", 503) from exc


def _e164(phone: str) -> str:
    """Convierte un número colombiano al formato E.164."""
    return f"+57{phone}" if len(phone) == 10 else f"+{phone.lstrip('+')}"
