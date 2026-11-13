"""Códigos individuales de invitación a fincas (F10.3.1).

Propósito: generar, normalizar y convertir en huella HMAC-SHA256 los códigos
``XXXXX-XXXXX`` que sustituirán al enlace de aceptación.

Contexto: módulo independiente, sin acceso a base de datos ni a correo. Desde
F10.3.2 lo usan ``invitation_services`` (crear, reenviar y cambiar el rol
guardan la huella); la entrega del código por correo corresponde a F10.3.3.

Reglas de seguridad:
- El código original solo se devuelve al llamador interno que lo entregará; en
  la base se guarda únicamente su huella (``FarmInvitation.code_hash``).
- La huella liga el código al correo normalizado del invitado:
  ``HMAC-SHA256(INVITATION_CODE_HMAC_KEY, f"{correo}:{codigo}")``.
- La clave es obligatoria y exclusiva: no hay valor por defecto ni alternativa
  con ``SECRET_KEY``. Si falta o es débil, se lanza ``ImproperlyConfigured``
  sin revelar su contenido.
- Ningún mensaje de error ni ``repr`` incluye el código, la huella o la clave.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings

from config.invitation_code_key import KEY_NAME, MIN_KEY_LENGTH, check_invitation_code_key

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
CODE_GROUP_SIZE = 5
CODE_SEPARATOR = "-"
CODE_HASH_LENGTH = 64
HMAC_KEY_SETTING = KEY_NAME
HMAC_KEY_MIN_LENGTH = MIN_KEY_LENGTH

_ALLOWED = frozenset(CODE_ALPHABET)
INVALID_CODE_MESSAGE = "código de invitación no válido"


class InvalidInvitationCode(ValueError):
    """Entrada que no es un código bien formado; nunca incluye la entrada."""

    def __init__(self) -> None:
        super().__init__(INVALID_CODE_MESSAGE)


def generate_code() -> str:
    """Genera un código nuevo con ``secrets.choice``, en formato XXXXX-XXXXX.

    Returns:
        El código de presentación, que solo debe llegar al flujo de entrega.
    """
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return format_code(raw)


def format_code(normalized: str) -> str:
    """Agrupa un código normalizado de 10 caracteres como XXXXX-XXXXX."""
    return f"{normalized[:CODE_GROUP_SIZE]}{CODE_SEPARATOR}{normalized[CODE_GROUP_SIZE:]}"


def normalize_code(raw: object) -> str:
    """Normaliza la entrada de una persona y la valida contra el alfabeto.

    Quita los espacios exteriores y los guiones de presentación y pasa a
    mayúsculas. La comprobación ASCII ocurre antes de ``upper()``: algunos
    caracteres Unicode se convierten en letras ASCII al pasar a mayúsculas
    (por ejemplo, la «s» larga se convierte en «S»).

    Raises:
        InvalidInvitationCode: si la entrada no es texto, no es ASCII, no tiene
            exactamente 10 caracteres o usa caracteres fuera del alfabeto.
    """
    if not isinstance(raw, str):
        raise InvalidInvitationCode()
    candidate = raw.strip()
    if not candidate.isascii():
        raise InvalidInvitationCode()
    candidate = candidate.replace(CODE_SEPARATOR, "").upper()
    if len(candidate) != CODE_LENGTH or not set(candidate) <= _ALLOWED:
        raise InvalidInvitationCode()
    return candidate


def normalize_email(value: str) -> str:
    """Normaliza el correo como ``invitation_services._normalize_email``."""
    return value.strip().lower()


def _hmac_key() -> bytes:
    """Lee la clave HMAC y falla de forma cerrada sin revelar su contenido.

    Las reglas están en ``config.invitation_code_key``, compartidas con la
    validación de producción al arrancar.
    """
    key = check_invitation_code_key(
        getattr(settings, HMAC_KEY_SETTING, ""),
        secret_key=getattr(settings, "SECRET_KEY", "") or "",
    )
    return key.encode("utf-8")


def code_hash(email: str, code: str) -> str:
    """Calcula la huella HMAC-SHA256 del código ligada al correo normalizado.

    Args:
        email: Correo del invitado; se normaliza aquí.
        code: Código con o sin guion; se normaliza aquí.

    Returns:
        64 caracteres hexadecimales en minúscula, aptos para ``code_hash``.

    Raises:
        InvalidInvitationCode: si el código está mal formado.
        ImproperlyConfigured: si falta la clave o no es válida.
    """
    normalized_code = normalize_code(code)
    normalized_email = normalize_email(email)
    if not normalized_email:
        raise ValueError("el correo es obligatorio para calcular la huella")
    message = f"{normalized_email}:{normalized_code}".encode()  # UTF-8
    return hmac.new(_hmac_key(), message, hashlib.sha256).hexdigest()


def hashes_match(expected: str, candidate: str) -> bool:
    """Compara dos huellas en tiempo constante."""
    if not isinstance(expected, str) or not isinstance(candidate, str):
        return False
    if not expected.isascii() or not candidate.isascii():
        return False
    return hmac.compare_digest(expected, candidate)


def new_code_for(email: str) -> tuple[str, str]:
    """Genera un código nuevo y su huella para el correo dado.

    Utilidad para F10.3.2: si al guardar la huella el índice único de
    ``code_hash`` produce ``IntegrityError``, el llamador debe volver a llamar a
    esta función y repetir el guardado (ver la estrategia de reintento en
    ``docs/modules/farms-invitation-codes.md``).

    Returns:
        ``(codigo_de_presentacion, huella)``. El código no debe persistirse.
    """
    code = generate_code()
    return code, code_hash(email, code)
