"""Envía el código por WhatsApp con Brevo.

Propósito: entregar el OTP en la plantilla de WhatsApp.
Contexto: recuperación de acceso, junto al correo y el SMS.
@author Cristian Deysdayr Jimenez
"""
import os

from apps.accounts.errors import AppError
from apps.accounts.integrations.brevo_client import post
from apps.accounts.integrations.phone_number import international

FAILURE = "no se pudo enviar el codigo por WhatsApp"


def send_code(phone: str, digits: str) -> None:
    """Entrega el código con la plantilla configurada en Brevo.

    @param phone: Celular de la cuenta.
    @param digits: Código de seis cifras. La plantilla debe usar la variable 1.
    """
    sender = international(os.getenv("BREVO_WHATSAPP_SENDER", ""))
    template = os.getenv("BREVO_WHATSAPP_TEMPLATE_ID", "")
    if not sender or not template.isdigit():
        raise AppError(FAILURE, 503)
    post(
        "/whatsapp/sendMessage",
        {
            "senderNumber": sender,
            "contactNumbers": [international(phone)],
            "templateId": int(template),
            "params": {"1": digits},
        },
        FAILURE,
    )
