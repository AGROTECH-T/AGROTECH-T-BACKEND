"""Validación de INVITATION_CODE_HMAC_KEY (F10.3.2).

Propósito: una sola fuente de reglas para la clave HMAC de los códigos de
invitación, usada al arrancar (``config.settings``) y en cada uso
(``apps.farms.invitation_codes``).

Contexto: funciones puras, sin leer variables de entorno ni Django settings.
Producción es ``DEBUG=False``, el mismo criterio que ya exige
``DJANGO_SECRET_KEY``, ``REDIS_URL`` y ``FRONTEND_URL``. En desarrollo y en las
pruebas (el orquestador fija ``DJANGO_DEBUG=true``) la clave no se exige al
arrancar; si falta, el servicio falla de forma cerrada al generar o comprobar
un código. Ningún mensaje incluye el valor de la clave.
"""
from __future__ import annotations

import hmac

from django.core.exceptions import ImproperlyConfigured

KEY_NAME = "INVITATION_CODE_HMAC_KEY"
MIN_KEY_LENGTH = 32


def check_invitation_code_key(key: object, *, secret_key: str | None) -> str:
    """Comprueba la clave y la devuelve; falla de forma cerrada sin revelarla.

    Raises:
        ImproperlyConfigured: si falta, está vacía o solo tiene espacios, tiene
            menos de ``MIN_KEY_LENGTH`` caracteres o es igual a ``SECRET_KEY``.
    """
    if not isinstance(key, str) or not key.strip():
        raise ImproperlyConfigured(
            f"{KEY_NAME} no está configurada; los códigos de invitación "
            "no pueden generarse ni comprobarse."
        )
    if len(key) < MIN_KEY_LENGTH:
        raise ImproperlyConfigured(
            f"{KEY_NAME} debe tener al menos {MIN_KEY_LENGTH} caracteres."
        )
    if secret_key and hmac.compare_digest(key.encode("utf-8"), secret_key.encode("utf-8")):
        raise ImproperlyConfigured(f"{KEY_NAME} debe ser independiente de SECRET_KEY.")
    return key


def resolve_invitation_code_key(raw: str | None, *, debug: bool, secret_key: str | None) -> str:
    """Valor de ``settings.INVITATION_CODE_HMAC_KEY`` según el entorno.

    En producción (``debug=False``) la clave es obligatoria y válida al arrancar.
    En desarrollo y pruebas se devuelve tal cual, incluso vacía: no hay valor
    por defecto, ni clave generada, ni alternativa con ``SECRET_KEY``.
    """
    value = "" if raw is None else raw
    if debug:
        return value
    try:
        return check_invitation_code_key(value, secret_key=secret_key)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"{exc} Es obligatoria en producción.") from None
