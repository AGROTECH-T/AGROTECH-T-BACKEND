"""Acceso atómico a la caché compartida para límites de intentos (cierre F10.3).

Propósito: dar a ``code_attempts`` y ``throttles`` operaciones indivisibles.
Con django-redis (producción y ``-Validate -WithRedis``) se usan scripts Lua,
que Redis ejecuta de forma atómica entre procesos y servidores. Con cualquier
otro backend (``LocMemCache`` de desarrollo y pruebas, que solo vive dentro de
un proceso) se usa un cerrojo de proceso.

Contexto: los errores de Redis no se ocultan; se propagan y la solicitud falla
cerrada (500), igual que el resto de accesos a la caché.
"""
from __future__ import annotations

import threading

from django.core.cache import cache

local_lock = threading.RLock()


def redis_client():
    """Cliente redis-py de la caché ``default`` si es django-redis; si no, ``None``."""
    client = getattr(cache, "client", None)
    get_client = getattr(client, "get_client", None)
    if get_client is None:
        return None
    return get_client(write=True)


def raw_key(name: str) -> str:
    """Clave real en Redis (prefijo y versión de la caché de Django)."""
    return cache.make_key(name)


def run_script(client, source: str, keys: list, args: list):
    """Ejecuta un script Lua (EVALSHA con reintento EVAL que hace redis-py)."""
    return client.register_script(source)(keys=keys, args=args)
