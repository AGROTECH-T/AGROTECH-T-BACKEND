"""Ajustes de API, caché, correo y seguridad HTTP.

Propósito: aislar la plataforma de autenticación del núcleo Django.
Contexto: JWT, Redis, CORS, SMTP y logs JSON de AGROTECH-T.
@author Cristian Deysdayr Jimenez
"""
import os

from django.core.exceptions import ImproperlyConfigured

from config.frontend_url import normalize_frontend_url

DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
FIVE_REQUESTS_PER_HOUR = "5/hour"

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

REDIS_URL = os.getenv("REDIS_URL", "")
if not REDIS_URL and not DEBUG:
    raise ImproperlyConfigured("REDIS_URL es obligatoria en producción")

CACHES = {
    "default": (
        {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
        if REDIS_URL
        else {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "agrotech-development",
        }
    )
}

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "apps.accounts.exceptions.api_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.api.authentication.JWEAuthentication",
        "apps.accounts.api.authentication.SessionCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "register": FIVE_REQUESTS_PER_HOUR,
        "login": "10/minute",
        "login_account": "5/minute",
        "recovery": "30/minute",
        "recovery_account": "30/minute",
        "confirm": "30/minute",
        "confirm_account": "30/minute",
        "reset": "10/hour",
        "reset_account": FIVE_REQUESTS_PER_HOUR,
        "renew": "30/minute",
        "verification_request": FIVE_REQUESTS_PER_HOUR,
        "verification_confirm": "10/minute",
        "invitation_create": "30/hour",
        "invitation_resend": "10/hour",
        "invitation_accept": "10/hour",
        "invitation_accept_ip": "30/hour",
    },
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "AGROTECH-T Backend API",
    "DESCRIPTION": "Registro, autenticación, recuperación y sesiones.",
    "VERSION": "1.0.0",
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 86400
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("SMTP_HOST", "smtp-relay.brevo.com")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("SMTP_FROM", "")

FRONTEND_URL = normalize_frontend_url(os.getenv("FRONTEND_URL"), debug=DEBUG)

# Clave HMAC independiente de los códigos de invitación (F10.3.1). Sin valor por
# defecto ni alternativa. config.settings la valida al arrancar en producción
# (config.invitation_code_key, F10.3.2); apps.farms.invitation_codes falla de
# forma cerrada al usarla si falta.
INVITATION_CODE_HMAC_KEY = os.getenv("INVITATION_CODE_HMAC_KEY", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "common.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
