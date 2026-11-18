"""Validación de GATEWAY_CREDENTIAL_HMAC_KEY (IoT-03.1, DEC-13 §12).

Propósito: una sola fuente de reglas para la clave HMAC con la que se calculan
las huellas de las credenciales de gateway, usada al arrancar
(``config.settings``) y en cada uso (``apps.iot.gateway_credentials``).

Contexto: mismo patrón que ``config.invitation_code_key``, con clave propia:
cada uso tiene su clave (separación de dominios). Funciones puras, sin leer
variables de entorno ni Django settings. En producción (``DEBUG=False``) la
clave es obligatoria y se valida al arrancar; en desarrollo y pruebas no se
exige al arrancar y el servicio falla de forma cerrada al usarla si falta. No
hay valor por defecto ni alternativa con ``SECRET_KEY``. Ningún mensaje incluye
el valor de la clave.
"""
from __future__ import annotations

import hmac

from django.core.exceptions import ImproperlyConfigured

KEY_NAME = "GATEWAY_CREDENTIAL_HMAC_KEY"
MIN_KEY_LENGTH = 32


def check_gateway_credential_key(key: object, *, secret_key: str | None) -> str:
    """Comprueba la clave y la devuelve; falla de forma cerrada sin revelarla.

    Raises:
        ImproperlyConfigured: si falta, está vacía o solo tiene espacios, tiene
            menos de ``MIN_KEY_LENGTH`` caracteres o es igual a ``SECRET_KEY``.
    """
    if not isinstance(key, str) or not key.strip():
        raise ImproperlyConfigured(
            f"{KEY_NAME} no está configurada; las credenciales de gateway "
            "no pueden emitirse ni verificarse."
        )
    if len(key) < MIN_KEY_LENGTH:
        raise ImproperlyConfigured(f"{KEY_NAME} debe tener al menos {MIN_KEY_LENGTH} caracteres.")
    if secret_key and hmac.compare_digest(key.encode("utf-8"), secret_key.encode("utf-8")):
        raise ImproperlyConfigured(f"{KEY_NAME} debe ser independiente de SECRET_KEY.")
    return key


def resolve_gateway_credential_key(
    raw: str | None, *, debug: bool, secret_key: str | None
) -> str:
    """Valor de ``settings.GATEWAY_CREDENTIAL_HMAC_KEY`` según el entorno.

    En producción (``debug=False``) la clave es obligatoria y válida al arrancar.
    En desarrollo y pruebas se devuelve tal cual, incluso vacía: no hay valor
    por defecto, ni clave generada, ni alternativa con ``SECRET_KEY``.
    """
    value = "" if raw is None else raw
    if debug:
        return value
    try:
        return check_gateway_credential_key(value, secret_key=secret_key)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"{exc} Es obligatoria en producción.") from None
