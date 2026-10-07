"""Entrega códigos de acceso mediante Brevo o SMTP.

@author Cristian Deysdayr Jimenez
"""

import os

from django.conf import settings
from django.core.mail import send_mail

from apps.accounts.errors import AppError
from apps.accounts.integrations.brevo_client import post

FAILURE = "no se pudo enviar el correo"


def send_code(address: str, digits: str) -> None:
    """Envía un OTP mediante la API de Brevo o SMTP."""
    if not settings.DEFAULT_FROM_EMAIL or not address:
        raise AppError(FAILURE, 502)
    message = f"Código {digits}. Caduca en pocos minutos."
    if os.getenv("BREVO_API_KEY"):
        post(
            "/smtp/email",
            {
                "sender": {"name": "AGROTECH-T", "email": settings.DEFAULT_FROM_EMAIL},
                "to": [{"email": address}],
                "subject": "Código de acceso AGROTECH-T",
                "textContent": message,
            },
            FAILURE,
        )
        return
    _send_smtp(address, message)


def _send_smtp(address: str, message: str) -> None:
    """Entrega el mismo texto por el correo SMTP configurado."""
    try:
        delivered = send_mail(
            "Código de acceso AGROTECH-T",
            message,
            settings.DEFAULT_FROM_EMAIL,
            [address],
            fail_silently=False,
        )
    except OSError as exc:
        raise AppError(FAILURE, 502) from exc
    if delivered != 1:
        raise AppError(FAILURE, 502)
