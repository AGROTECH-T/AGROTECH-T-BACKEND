"""Limita operaciones sensibles por cuenta sin exponer identificaciones.

@author Cristian Deysdayr Jimenez
"""

import hashlib
import ipaddress

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
    """Cuota por IP del cliente, nunca por cuenta.

    En Render la toma de ``CF-Connecting-IP``, que Cloudflare escribe y el
    cliente no puede falsificar. Sin esa cabecera usa ``REMOTE_ADDR``.
    No lee ``X-Forwarded-For``.
    """

    scope_attr = "throttle_scope"

    def __init__(self):
        """El alcance lo define la vista, así que la cuota se calcula después."""

    def get_cache_key(self, request, view):
        """Huella de la IP del cliente, atada al alcance de la vista."""
        client_ip = _client_ip(request)
        if not client_ip:
            return None
        digest = hashlib.sha256(client_ip.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

    def allow_request(self, request, view):
        """Toma el alcance de la vista y cuenta solo por IP."""
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


def _client_ip(request) -> str | None:
    """IP del cliente: la de Cloudflare si viene, si no la de la conexión."""
    reported = str(request.META.get("HTTP_CF_CONNECTING_IP", "")).strip()
    if reported:
        try:
            return str(ipaddress.ip_address(reported))
        except ValueError:
            reported = ""
    remote_addr = request.META.get("REMOTE_ADDR")
    if not remote_addr:
        return None
    return str(remote_addr)
