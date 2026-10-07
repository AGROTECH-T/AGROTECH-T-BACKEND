"""Cliente HTTPS de Brevo.

Propósito: enviar JSON autenticado sin repetir cabeceras.
Contexto: correo, mensaje de texto y WhatsApp.
@author Cristian Deysdayr Jimenez
"""
import os
import re

import requests

from apps.accounts.errors import AppError

IP_ADDRESS = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def post(path: str, payload: dict, failure: str) -> None:
    """Publica un mensaje en la API de Brevo.

    @param path: Ruta desde /v3, por ejemplo /smtp/email.
    @param payload: Cuerpo que espera ese canal.
    @param failure: Texto si falta la clave o Brevo rechaza el envío.
    """
    key = os.getenv("BREVO_API_KEY", "")
    if not key:
        raise AppError(failure, 503)
    try:
        response = requests.post(
            f"https://api.brevo.com/v3{path}",
            json=payload,
            headers={"api-key": key, "content-type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AppError(failure, 502) from exc
    if response.status_code >= 300:
        raise AppError(_readable(response, failure), 502)


def _readable(response: requests.Response, failure: str) -> str:
    """Traduce el bloqueo de IP de Brevo a un aviso usable."""
    reader = getattr(response, "json", None)
    try:
        payload = reader() if callable(reader) else {}
    except (ValueError, requests.RequestException):
        payload = {}
    message = str(payload.get("message", "")) if isinstance(payload, dict) else ""
    address = IP_ADDRESS.search(message)
    if address and "IP" in message:
        return f"No se pudo enviar. En Brevo autoriza la IP {address.group()}."
    return failure
