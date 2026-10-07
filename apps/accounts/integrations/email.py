"""Entrega códigos de acceso mediante Brevo o SMTP.

@author Cristian Deysdayr Jimenez
"""

import os

import requests
from django.conf import settings
from django.core.mail import send_mail

from apps.accounts.errors import AppError


def send_code(address: str, digits: str) -> None:
    """Envía un OTP mediante la API de Brevo o SMTP."""
    if not settings.DEFAULT_FROM_EMAIL or not address:
        raise AppError("no se pudo enviar el correo", 502)
    message = f"Código {digits}. Caduca en pocos minutos."
    if os.getenv("BREVO_API_KEY"):
        _send_brevo(address, message)
        return
    try:
        delivered = send_mail(
            "Código de acceso AGROTECH-T",
            message,
            settings.DEFAULT_FROM_EMAIL,
            [address],
            fail_silently=False,
        )
    except Exception as exc:
        raise AppError("no se pudo enviar el correo", 502) from exc
    if delivered != 1:
        raise AppError("no se pudo enviar el codigo", 502)


def _send_brevo(address: str, message: str) -> None:
    """Entrega un correo mediante la API HTTPS de Brevo."""
    payload = {
        "sender": {"name": "AGROTECH-T", "email": settings.DEFAULT_FROM_EMAIL},
        "to": [{"email": address}],
        "subject": "Código de acceso AGROTECH-T",
        "textContent": message,
    }
    headers = {"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json"}
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AppError("no se pudo enviar el correo", 502) from exc
    if response.status_code >= 300:
        raise AppError("no se pudo enviar el correo", 502)
