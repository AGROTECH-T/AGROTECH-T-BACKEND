"""Número internacional para Brevo.

Propósito: agregar el indicativo de Colombia cuando falta.
Contexto: mensaje de texto y WhatsApp.
@author Cristian Deysdayr Jimenez
"""


def international(phone: str) -> str:
    """Devuelve el celular con indicativo y sin signos.

    @param phone: Número guardado o el remitente configurado.
    @returns Dígitos con 57 si el número tiene 10 cifras.
    """
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) == 10:
        return f"57{digits}"
    return digits
