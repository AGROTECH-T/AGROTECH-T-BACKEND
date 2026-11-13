"""Autentica solicitudes DRF mediante sesiones JWE revocables.

@author Cristian Deysdayr Jimenez
"""

from rest_framework.authentication import (
    BaseAuthentication,
    SessionAuthentication,
    get_authorization_header,
)
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.accounts.errors import AppError
from apps.accounts.models import User
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


class SessionCookieAuthentication(SessionAuthentication):
    """Autentica mediante la sesión Django persistida y protege con CSRF."""

    def authenticate(self, request: Request):
        """Retorna el usuario de la sesión o ``None`` para anónimos."""
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return None
        self.enforce_csrf(request)
        return (user, None)

    def authenticate_header(self, request: Request) -> str:
        """Indica el esquema de sesión requerido para respuestas 401."""
        return "Session"
