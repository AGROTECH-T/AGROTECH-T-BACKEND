"""Envía el código por mensaje de texto con Brevo.

Propósito: entregar el OTP como SMS transaccional.
Contexto: recuperación de acceso, junto al correo y WhatsApp.
@author Cristian Deysdayr Jimenez
"""
import os

from apps.accounts.errors import AppError
from apps.accounts.integrations.brevo_client import post
from apps.accounts.integrations.phone_number import international

FAILURE = "no se pudo enviar el codigo por mensaje de texto"


def send_code(phone: str, digits: str) -> None:
    """Entrega el código en un SMS de texto.

    @param phone: Celular de la cuenta.
    @param digits: Código de seis cifras.
    """
    sender = os.getenv("BREVO_SMS_SENDER", "")
    if not sender.isalnum() or not 1 <= len(sender) <= 11:
        raise AppError(FAILURE, 503)
    post(
        "/transactionalSMS/send",
        {
            "sender": sender,
            "recipient": international(phone),
            "content": f"Código AGROTECH-T: {digits}. Caduca en pocos minutos.",
            "type": "transactional",
        },
        FAILURE,
    )
