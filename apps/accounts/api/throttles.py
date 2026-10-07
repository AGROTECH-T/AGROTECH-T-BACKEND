"""Limita operaciones sensibles por cuenta sin exponer identificaciones.

@author Cristian Deysdayr Jimenez
"""

import hashlib

from rest_framework.throttling import SimpleRateThrottle


class IdentifierThrottle(SimpleRateThrottle):
    """Construye una cuota a partir de una huella de identificación."""

    field = "identification"

    def get_cache_key(self, request, view):
        """Retorna una clave anónima para el identificador solicitado."""
        value = str(request.data.get(self.field, "")).strip()
        if not value:
            return None
        digest = hashlib.sha256(value.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LoginIdentifierThrottle(IdentifierThrottle):
    """Limita intentos de acceso por cuenta."""

    scope = "login_account"


class RecoveryIdentifierThrottle(IdentifierThrottle):
    """Limita solicitudes de recuperación por cuenta."""

    scope = "recovery_account"


class ResetIdentifierThrottle(IdentifierThrottle):
    """Limita verificaciones de código por cuenta."""

    scope = "reset_account"
