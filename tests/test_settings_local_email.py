"""Pruebas de config.settings_local_email (desarrollo local con correo real por Brevo).

Aislamiento: ``config.settings`` se sustituye en ``sys.modules`` por un módulo
falso, de modo que no se lee ``.env`` ni la configuración real; el entorno se
modifica solo con ``monkeypatch`` (se restaura al terminar). No se envía ningún
correo: solo se importa la configuración.
"""
import ast
import importlib
import sys
import types
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

MODULE = "config.settings_local_email"
SOURCE = Path(__file__).resolve().parent.parent / "config" / "settings_local_email.py"
REDIS = {"default": {"BACKEND": "django_redis.cache.RedisCache"}}
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_SETTINGS = {
    "EMAIL_BACKEND": SMTP_BACKEND,
    "EMAIL_HOST": "smtp.ejemplo.invalid",
    "EMAIL_PORT": 587,
    "EMAIL_USE_TLS": True,
    "EMAIL_HOST_USER": "usuario-ficticio",
    "EMAIL_HOST_PASSWORD": "valor-ficticio",
    "DEFAULT_FROM_EMAIL": "no-responder@ejemplo.invalid",
}


def _load(monkeypatch, *, debug=True, caches=REDIS, hmac_key="k" * 64,
          secret_key="s" * 50, redis_url="redis://:x@127.0.0.1:6379/0"):
    fake = types.ModuleType("config.settings")
    values = dict(DEBUG=debug, CACHES=caches, INVITATION_CODE_HMAC_KEY=hmac_key,
                  SECRET_KEY=secret_key, **EMAIL_SETTINGS)
    for name, value in values.items():
        setattr(fake, name, value)
    monkeypatch.setitem(sys.modules, "config.settings", fake)
    # Si la configuración intentara importar settings_local, fallaría.
    monkeypatch.setitem(sys.modules, "config.settings_local", None)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    if redis_url is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("BREVO_API_KEY", "valor-ficticio-brevo")
    try:
        return importlib.import_module(MODULE)
    finally:
        sys.modules.pop(MODULE, None)


def test_valid_configuration_imports(monkeypatch):
    module = _load(monkeypatch)
    assert module.DEBUG is True
    assert module.CACHES == REDIS


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"debug": False}, "DJANGO_DEBUG"),
        ({"redis_url": None}, "REDIS_URL"),
        ({"caches": LOCMEM}, "REDIS_URL"),
        ({"hmac_key": ""}, "INVITATION_CODE_HMAC_KEY"),
        ({"hmac_key": "s" * 50}, "SECRET_KEY"),
    ],
)
def test_invalid_configuration_is_rejected(monkeypatch, overrides, fragment):
    with pytest.raises(ImproperlyConfigured, match=fragment):
        _load(monkeypatch, **overrides)


def test_brevo_key_is_kept(monkeypatch):
    import os

    _load(monkeypatch)
    assert os.environ.get("BREVO_API_KEY") == "valor-ficticio-brevo"


def test_inherited_email_settings_are_preserved(monkeypatch):
    module = _load(monkeypatch)
    for name, value in EMAIL_SETTINGS.items():
        assert getattr(module, name) == value, name


def test_does_not_import_settings_local():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "config.settings" in imported
    assert "config.settings_local" not in imported
    assert "disable_external_email" not in SOURCE.read_text(encoding="utf-8")
