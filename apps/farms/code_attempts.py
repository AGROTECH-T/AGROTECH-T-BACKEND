"""Límite de intentos fallidos con códigos de invitación (F10.3.3-B, cierre F10.3).

Propósito: como máximo 5 intentos fallidos por cuenta en una ventana de 15
minutos, compartidos entre previsualizar y aceptar, para frenar la fuerza
bruta.

Contexto: el estado vive en la caché ``default`` de Django. En producción es
Redis (``REDIS_URL`` es obligatoria con ``DEBUG=False``), así que se comparte
entre procesos y servidores; cada operación es un script Lua atómico
(``apps.farms.atomic_cache``). En desarrollo y pruebas sin Redis la caché es
``LocMemCache`` (un solo proceso) y las operaciones se serializan con un
cerrojo. No se guarda nada en MariaDB, ni el código, ni la huella: la clave es
el id de la cuenta.

Algoritmo (ventana fija desde el primer intento):
1. Cada ventana guarda un identificador aleatorio, los fallos confirmados
   (``f``) y los intentos en curso (``p``).
2. ``begin()`` reserva un intento en curso de forma atómica *antes* de mirar
   el código, solo si ``f + p < MAX_FAILED_ATTEMPTS``. Así nunca se evalúan más
   de 5 códigos sin resolver, ni siquiera con una ráfaga concurrente.
3. Si el límite se alcanzó solo por intentos en curso (``f < MAX``), la
   solicitud espera hasta ``PENDING_WAIT_SECONDS`` a que se libere un hueco en
   lugar de recibir 429 (D-3). Si ya hay ``MAX`` fallos, 429 inmediato. Una
   solicitud rechazada no consume nada.
4. Cada reserva tiene un token. ``failed()`` (404) la convierte en fallo y
   ``not_failed()`` (200, 409, etc.) la devuelve, solo si su ventana sigue
   vigente y el token sigue reservado: una petición antigua nunca toca una
   ventana nueva (D-1), cada reserva se resuelve una sola vez y los contadores
   nunca bajan de 0.
5. Una reserva que nunca se resuelve (por ejemplo, un error 500) cuenta hasta
   que vence la ventana: falla cerrado.
"""
from __future__ import annotations

import math
import secrets
import time

from django.core.cache import cache
from rest_framework.exceptions import Throttled

from apps.farms.atomic_cache import local_lock, raw_key, redis_client, run_script

MAX_FAILED_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
PENDING_WAIT_SECONDS = 2.0
_PENDING_POLL_SECONDS = 0.05
_PREFIX = "farms:invitation-code-attempts"

_clock = time.time
_monotonic = time.monotonic
_sleep = time.sleep

# KEYS[1] = ventana (hash: w = id, f = fallos, p = en curso); KEYS[2] = reservas (set).
# ARGV = máximo, segundos de ventana, id de ventana nueva, token de la reserva.
# Devuelve {permitido, id de ventana, segundos restantes, fallos}.
_RESERVE_LUA = """
local window = redis.call('HGET', KEYS[1], 'w')
if not window then
  redis.call('DEL', KEYS[2])
  redis.call('HSET', KEYS[1], 'w', ARGV[3], 'f', 0, 'p', 0)
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  window = ARGV[3]
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  ttl = tonumber(ARGV[2])
end
local failures = tonumber(redis.call('HGET', KEYS[1], 'f'))
local pending = tonumber(redis.call('HGET', KEYS[1], 'p'))
if failures + pending >= tonumber(ARGV[1]) then
  return {0, window, ttl, failures}
end
redis.call('HINCRBY', KEYS[1], 'p', 1)
redis.call('SADD', KEYS[2], ARGV[4])
redis.call('EXPIRE', KEYS[2], ttl)
return {1, window, ttl, failures}
"""

# KEYS como arriba; ARGV = id de la ventana de la reserva, token, 'fail' u 'ok'.
_RESOLVE_LUA = """
if redis.call('HGET', KEYS[1], 'w') ~= ARGV[1] then
  return -1
end
if redis.call('SREM', KEYS[2], ARGV[2]) == 0 then
  return -2
end
if tonumber(redis.call('HGET', KEYS[1], 'p')) > 0 then
  redis.call('HINCRBY', KEYS[1], 'p', -1)
end
if ARGV[3] == 'fail' then
  redis.call('HINCRBY', KEYS[1], 'f', 1)
end
return 1
"""


def _names(user_pk: int) -> tuple[str, str]:
    return f"{_PREFIX}:{user_pk}:window", f"{_PREFIX}:{user_pk}:reservations"


def _as_text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _local_state(window_name: str, now: float, new_window: str | None = None):
    state = cache.get(window_name)
    if state and state["expires"] > now:
        return state
    if new_window is None:
        return None
    return {"w": new_window, "f": 0, "p": 0, "tokens": [], "expires": now + WINDOW_SECONDS}


def _reserve(user_pk: int, token: str) -> tuple[bool, str, float, int]:
    """Reserva un intento. Devuelve (permitido, ventana, segundos restantes, fallos)."""
    window_name, reservations_name = _names(user_pk)
    new_window = secrets.token_hex(16)
    client = redis_client()
    if client is not None:
        allowed, window, ttl, failures = run_script(
            client,
            _RESERVE_LUA,
            [raw_key(window_name), raw_key(reservations_name)],
            [MAX_FAILED_ATTEMPTS, WINDOW_SECONDS, new_window, token],
        )
        return bool(allowed), _as_text(window), float(ttl), int(failures)
    with local_lock:
        now = _clock()
        state = _local_state(window_name, now, new_window)
        remaining = state["expires"] - now
        if state["f"] + state["p"] >= MAX_FAILED_ATTEMPTS:
            return False, state["w"], remaining, state["f"]
        state["p"] += 1
        state["tokens"].append(token)
        cache.set(window_name, state, max(1, math.ceil(remaining)))
        return True, state["w"], remaining, state["f"]


def _resolve(user_pk: int, window: str, token: str, failed: bool) -> None:
    """Resuelve la reserva ``token``: fallo o devolución, solo en su ventana."""
    window_name, reservations_name = _names(user_pk)
    client = redis_client()
    if client is not None:
        run_script(
            client,
            _RESOLVE_LUA,
            [raw_key(window_name), raw_key(reservations_name)],
            [window, token, "fail" if failed else "ok"],
        )
        return
    with local_lock:
        now = _clock()
        state = _local_state(window_name, now)
        if state is None or state["w"] != window or token not in state["tokens"]:
            return
        state["tokens"].remove(token)
        if state["p"] > 0:
            state["p"] -= 1
        if failed:
            state["f"] += 1
        cache.set(window_name, state, max(1, math.ceil(state["expires"] - now)))


class CodeAttemptGuard:
    """Reserva un intento para la cuenta y lo resuelve como fallo o no fallo."""

    def __init__(self, user_pk: int):
        self.user_pk = user_pk
        self._window: str | None = None
        self._token: str | None = None

    def begin(self) -> None:
        """Reserva el intento o lanza ``Throttled`` (429) si se alcanzó el límite."""
        token = secrets.token_hex(16)
        deadline = _monotonic() + PENDING_WAIT_SECONDS
        while True:
            allowed, window, remaining, failures = _reserve(self.user_pk, token)
            if allowed:
                self._window, self._token = window, token
                return
            if failures >= MAX_FAILED_ATTEMPTS:
                raise Throttled(wait=max(1, int(remaining)))
            if _monotonic() >= deadline:
                # Bloqueada solo por intentos aún en curso: reintento breve.
                raise Throttled(wait=1)
            _sleep(_PENDING_POLL_SECONDS)

    def failed(self) -> None:
        """Registra el intento reservado como fallido (404)."""
        self._resolve(failed=True)

    def not_failed(self) -> None:
        """Devuelve la unidad de un intento que no fue fallido (como mucho una vez)."""
        self._resolve(failed=False)

    def _resolve(self, failed: bool) -> None:
        if self._token is None:
            return
        window, token = self._window, self._token
        self._window = self._token = None
        _resolve(self.user_pk, window, token, failed)


def failed_attempts(user_pk: int) -> int:
    """Unidades en uso en la ventana actual: fallos confirmados + intentos en curso.

    Para pruebas y soporte. Sin intentos en curso coincide con los fallos.
    """
    window_name, _reservations = _names(user_pk)
    client = redis_client()
    if client is not None:
        failures, pending = client.hmget(raw_key(window_name), ["f", "p"])
        return int(failures or 0) + int(pending or 0)
    with local_lock:
        state = _local_state(window_name, _clock())
        return 0 if state is None else int(state["f"]) + int(state["p"])
