"""Límites de frecuencia específicos del dominio de fincas.

Cierre F10.3 (D-2): los throttles de aceptación de invitaciones usan una
ventana deslizante atómica. ``SimpleRateThrottle`` de DRF lee el historial,
lo modifica y lo vuelve a escribir sin atomicidad, por lo que bajo concurrencia
real entre procesos dejaba pasar más solicitudes que su cuota (demostrado en
F10.3.4-B). Con django-redis el historial es un ZSET actualizado por un script
Lua; con ``LocMemCache`` (un solo proceso) la lógica original de DRF se
serializa con un cerrojo. Los límites no cambian: ``invitation_accept`` 10 por
cuenta y hora, ``invitation_accept_ip`` 30 por IP y hora (``config/platform.py``).
"""
import hashlib
import uuid

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

from apps.farms.atomic_cache import local_lock, raw_key, redis_client, run_script

# KEYS[1] = historial (ZSET, puntuación = instante en ms).
# ARGV = ahora (ms), duración (ms), límite, miembro único.
_SLIDING_WINDOW_LUA = """
local now = tonumber(ARGV[1])
local duration = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - duration)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, count, oldest[2]}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], duration)
return {1, count + 1, ARGV[1]}
"""


class AtomicRateMixin:
    """Sustituye la comprobación no atómica de ``SimpleRateThrottle``.

    Misma semántica que DRF (se descartan las marcas con antigüedad mayor o
    igual a la duración y se admite mientras haya menos de ``num_requests``),
    pero la lectura y la escritura son una única operación.
    """

    _oldest = None
    _atomic = False

    def _atomic_allow(self, request, view) -> bool:
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        client = redis_client()
        if client is None:
            self._atomic = False
            with local_lock:
                return SimpleRateThrottle.allow_request(self, request, view)
        self._atomic = True
        self.now = self.timer()
        now_ms = int(self.now * 1000)
        allowed, _count, oldest = run_script(
            client,
            _SLIDING_WINDOW_LUA,
            [raw_key(f"{self.key}:zset")],
            [now_ms, int(self.duration * 1000), self.num_requests, f"{now_ms}-{uuid.uuid4().hex}"],
        )
        self._oldest = float(oldest) / 1000
        return bool(allowed)

    def wait(self):
        if not self._atomic:
            return super().wait()
        return max(0.0, self._oldest + self.duration - self.now)


class InvitationAcceptAccountThrottle(AtomicRateMixin, ScopedRateThrottle):
    """Cuota por cuenta del ``throttle_scope`` de la vista, atómica (D-2)."""

    def allow_request(self, request, view):
        # Igual que ScopedRateThrottle: sin scope en la vista no hay límite.
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return self._atomic_allow(request, view)


class InvitationAcceptIpThrottle(AtomicRateMixin, SimpleRateThrottle):
    """Limita las aceptaciones de invitación por dirección de origen."""

    scope = "invitation_accept_ip"

    def get_cache_key(self, request, view):
        """Deriva una clave anónima a partir de REMOTE_ADDR (no usa X-Forwarded-For)."""
        remote_addr = request.META.get("REMOTE_ADDR")
        if not remote_addr:
            return None
        digest = hashlib.sha256(str(remote_addr).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        return self._atomic_allow(request, view)
