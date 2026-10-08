"""Entrega códigos de acceso mediante Brevo o SMTP.

@author Cristian Deysdayr Jimenez
"""

import os

from django.conf import settings
from django.core.mail import send_mail

from apps.accounts.errors import AppError
from apps.accounts.integrations.brevo_client import post
from apps.accounts.integrations.otp_mail_body import html as otp_html
from apps.accounts.integrations.otp_mail_body import plain as otp_plain

FAILURE = "no se pudo enviar el correo"


def send_code(address: str, digits: str, minutes: int = 5) -> None:
    """Envía un OTP mediante la API de Brevo o SMTP.

    @param address: Correo destino.
    @param digits: Código de seis cifras.
    @param minutes: Vigencia mostrada en el mensaje.
    """
    if not settings.DEFAULT_FROM_EMAIL or not address:
        raise AppError(FAILURE, 502)
    text = otp_plain(digits, minutes)
    rich = otp_html(digits, minutes)
    if os.getenv("BREVO_API_KEY"):
        post(
            "/smtp/email",
            {
                "sender": {"name": "AGROTECH-T", "email": settings.DEFAULT_FROM_EMAIL},
                "to": [{"email": address}],
                "subject": "Código de acceso AGROTECH-T",
                "textContent": text,
                "htmlContent": rich,
            },
            FAILURE,
        )
        return
    _send_smtp(address, text, rich)


def _send_smtp(address: str, text: str, rich: str) -> None:
    """Entrega el mismo contenido por SMTP."""
    try:
        delivered = send_mail(
            "Código de acceso AGROTECH-T",
            text,
            settings.DEFAULT_FROM_EMAIL,
            [address],
            fail_silently=False,
            html_message=rich,
        )
    except OSError as exc:
        raise AppError(FAILURE, 502) from exc
    if delivered != 1:
        raise AppError(FAILURE, 502)
