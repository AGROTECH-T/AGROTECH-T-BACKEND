"""Contrato común de entrega de correo con resultado explícito.

Propósito: unificar la entrega de correo de VERIFICATION e INVITATION bajo un
único resultado ``DeliveryOutcome``, sin mantener bloqueos de base de datos y
sin registrar datos sensibles.

Contexto: los remitentes (Brevo o SMTP) se resuelven según la configuración y
el resultado permite decidir si se libera, confirma o conserva una reserva de
entrega previamente persistida.
"""
import os
from enum import Enum

import requests
from django.conf import settings
from django.core.mail import send_mail


class DeliveryOutcome(str, Enum):
    """Resultado de una entrega de correo frente al proveedor."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INDETERMINATE = "indeterminate"


def deliver_email(*, to: str, subject: str, text: str, html: str, timeout: int = 15) -> DeliveryOutcome:
    """Entrega un correo por Brevo o SMTP y devuelve el resultado del proveedor.

    Un resultado ``INDETERMINATE`` no es un rechazo definitivo: la reserva de
    entrega debe conservarse y la invitación o el código siguen disponibles.
    """
    if not settings.DEFAULT_FROM_EMAIL or not to:
        return DeliveryOutcome.REJECTED
    if os.getenv("BREVO_API_KEY"):
        return _deliver_brevo(to, subject, text, html, timeout)
    return _deliver_smtp(to, subject, text, html)


def _deliver_brevo(to: str, subject: str, text: str, html: str, timeout: int) -> DeliveryOutcome:
    """Envía mediante la API de Brevo y traduce la respuesta HTTP."""
    key = os.getenv("BREVO_API_KEY", "")
    if not key:
        return DeliveryOutcome.INDETERMINATE
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json={
                "sender": {"name": "AGROTECH-T", "email": settings.DEFAULT_FROM_EMAIL},
                "to": [{"email": to}],
                "subject": subject,
                "textContent": text,
                "htmlContent": html,
            },
            headers={"api-key": key, "content-type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException:
        return DeliveryOutcome.INDETERMINATE
    if 200 <= response.status_code < 300:
        return DeliveryOutcome.ACCEPTED
    if response.status_code == 429:
        return DeliveryOutcome.INDETERMINATE
    if 400 <= response.status_code < 500:
        return DeliveryOutcome.REJECTED
    return DeliveryOutcome.INDETERMINATE


def _deliver_smtp(to: str, subject: str, text: str, html: str) -> DeliveryOutcome:
    """Envía por SMTP; la ausencia de configuración o la incertidumbre son indeterminadas."""
    if not (settings.EMAIL_HOST and settings.EMAIL_HOST_USER):
        return DeliveryOutcome.INDETERMINATE
    try:
        delivered = send_mail(
            subject,
            text,
            settings.DEFAULT_FROM_EMAIL,
            [to],
            fail_silently=False,
            html_message=html,
        )
    except OSError:
        return DeliveryOutcome.INDETERMINATE
    if delivered == 1:
        return DeliveryOutcome.ACCEPTED
    return DeliveryOutcome.INDETERMINATE
