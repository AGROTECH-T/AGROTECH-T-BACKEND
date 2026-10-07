"""Configuración general de Django para AGROTECH-T-BACKEND.

Propósito: ensamblar dominios, MariaDB y la plataforma de autenticación.
Contexto: una sola base de datos; SQLite queda reservado a las pruebas.
@author Cristian Deysdayr Jimenez
"""
import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config import platform
from config.database import database

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatoria en producción")
    SECRET_KEY = secrets.token_urlsafe(50)

_hosts = os.getenv("DJANGO_ALLOWED_HOSTS") or os.getenv(
    "ALLOWED_HOSTS", "localhost,127.0.0.1"
)
ALLOWED_HOSTS = [host.strip() for host in _hosts.split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "apps.accounts",
    "apps.farms",
    "apps.production.avicultura",
    "apps.production.piscicultura",
    "apps.production.porcicultura",
    "apps.production.agricultura",
    "apps.production.hidroponia",
    "apps.inventario",
    "apps.dashboard",
    "apps.alertas",
    "apps.trazabilidad",
    "apps.iot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": database(BASE_DIR)}
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = platform.CACHES
CORS_ALLOWED_ORIGINS = platform.CORS_ALLOWED_ORIGINS
CSRF_COOKIE_SECURE = platform.CSRF_COOKIE_SECURE
DEFAULT_FROM_EMAIL = platform.DEFAULT_FROM_EMAIL
EMAIL_BACKEND = platform.EMAIL_BACKEND
EMAIL_HOST = platform.EMAIL_HOST
EMAIL_HOST_PASSWORD = platform.EMAIL_HOST_PASSWORD
EMAIL_HOST_USER = platform.EMAIL_HOST_USER
EMAIL_PORT = platform.EMAIL_PORT
EMAIL_USE_TLS = platform.EMAIL_USE_TLS
LOGGING = platform.LOGGING
PASSWORD_HASHERS = platform.PASSWORD_HASHERS
REST_FRAMEWORK = platform.REST_FRAMEWORK
SECURE_CONTENT_TYPE_NOSNIFF = platform.SECURE_CONTENT_TYPE_NOSNIFF
SECURE_HSTS_INCLUDE_SUBDOMAINS = platform.SECURE_HSTS_INCLUDE_SUBDOMAINS
SECURE_HSTS_PRELOAD = platform.SECURE_HSTS_PRELOAD
SECURE_HSTS_SECONDS = platform.SECURE_HSTS_SECONDS
SECURE_PROXY_SSL_HEADER = platform.SECURE_PROXY_SSL_HEADER
SECURE_SSL_REDIRECT = platform.SECURE_SSL_REDIRECT
SESSION_COOKIE_SECURE = platform.SESSION_COOKIE_SECURE
SPECTACULAR_SETTINGS = platform.SPECTACULAR_SETTINGS
