"""Autentica solicitudes DRF mediante sesiones JWE revocables.

@author Cristian Deysdayr Jimenez
"""

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.accounts.errors import AppError
from apps.accounts.services import session_service


class JWEAuthentication(BaseAuthentication):
    """Valida el esquema Bearer y la sesión persistida."""

    def authenticate(self, request: Request):
        """Retorna usuario y claims si existe una credencial Bearer."""
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            raise AuthenticationFailed("credencial de sesión inválida")
        try:
            compact = parts[1].decode()
            return session_service.authenticate(compact)
        except (UnicodeError, AppError) as exc:
            raise AuthenticationFailed("la sesion vencio") from exc

    def authenticate_header(self, request: Request) -> str:
        """Indica el esquema requerido para respuestas 401."""
        return "Bearer"
