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


class ConfirmIdentifierThrottle(IdentifierThrottle):
    """Limita comprobaciones del código por cuenta."""

    scope = "confirm_account"


class ResetIdentifierThrottle(IdentifierThrottle):
    """Limita verificaciones de código por cuenta."""

    scope = "reset_account"


class OriginIpThrottle(SimpleRateThrottle):
    """Cuota por IP de origen, nunca por cuenta.

    Usa ``REMOTE_ADDR``. No lee ``X-Forwarded-For``, para que el cliente no
    elija otra identidad cambiando esa cabecera.
    """

    scope_attr = "throttle_scope"

    def __init__(self):
        """El alcance lo define la vista, así que la cuota se calcula después."""

    def get_cache_key(self, request, view):
        """Huella de la IP de la conexión, atada al alcance de la vista."""
        remote_addr = request.META.get("REMOTE_ADDR")
        if not remote_addr:
            return None
        digest = hashlib.sha256(str(remote_addr).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

    def allow_request(self, request, view):
        """Toma el alcance de la vista y cuenta solo por IP."""
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)
