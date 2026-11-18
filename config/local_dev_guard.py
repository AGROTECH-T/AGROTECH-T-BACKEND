"""Comprobaciones del arranque de desarrollo local (cierre F10.3).

Propósito: que ``config.settings_local`` no arranque sin Redis ni sin una clave
HMAC válida, y que el correo de ese proceso no salga por Brevo.
Contexto: funciones puras, probadas en ``tests/test_local_dev_guard.py`` sin
importar la configuración local.
@author Cristian Deysdayr Jimenez
"""
from django.core.exceptions import ImproperlyConfigured

REDIS_CACHE_BACKEND = "django_redis.cache.RedisCache"
MIN_HMAC_KEY_LENGTH = 32


def validate_local_settings(*, debug, caches, hmac_key, secret_key, environ) -> None:
    """Rechaza un arranque local sin DEBUG, sin Redis o sin clave HMAC válida."""
    problems = []
    if not debug:
        problems.append("config.settings_local solo se usa con DJANGO_DEBUG=true")
    if not environ.get("REDIS_URL") or caches["default"]["BACKEND"] != REDIS_CACHE_BACKEND:
        problems.append("REDIS_URL es obligatoria: la caché local debe ser Redis")
    if len(hmac_key or "") < MIN_HMAC_KEY_LENGTH:
        problems.append(
            f"INVITATION_CODE_HMAC_KEY es obligatoria (al menos {MIN_HMAC_KEY_LENGTH} caracteres)"
        )
    elif hmac_key == secret_key:
        problems.append("INVITATION_CODE_HMAC_KEY no puede ser igual a SECRET_KEY")
    if problems:
        raise ImproperlyConfigured("Configuración local inválida: " + "; ".join(problems))


def disable_external_email(environ) -> None:
    """Retira Brevo del entorno de este proceso para que el correo vaya a la consola."""
    environ.pop("BREVO_API_KEY", None)
