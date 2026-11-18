# ruff: noqa: S101, S105, S106
"""Validación de GATEWAY_CREDENTIAL_HMAC_KEY (IoT-03.1, sin base de datos).

Propósito: clave obligatoria en producción y validada al arrancar; en
desarrollo y pruebas se devuelve tal cual; nunca hay alternativa con
``SECRET_KEY``; los mensajes no revelan el valor.
"""
import inspect
import pathlib

import pytest
from django.core.exceptions import ImproperlyConfigured

import config.platform as platform
from config import gateway_credential_key as module
from config.gateway_credential_key import (
    KEY_NAME,
    MIN_KEY_LENGTH,
    check_gateway_credential_key,
    resolve_gateway_credential_key,
)

VALID_KEY = "k" * MIN_KEY_LENGTH
OTHER_SECRET = "s" * 50


@pytest.mark.parametrize("raw", [None, "", "   ", 123])
def test_missing_key_is_rejected(raw):
    with pytest.raises(ImproperlyConfigured, match="no está configurada"):
        check_gateway_credential_key(raw, secret_key=OTHER_SECRET)


def test_short_key_is_rejected_without_echo():
    short = "corta-" + "x" * (MIN_KEY_LENGTH - 7)
    with pytest.raises(ImproperlyConfigured) as excinfo:
        check_gateway_credential_key(short, secret_key=OTHER_SECRET)
    assert str(MIN_KEY_LENGTH) in str(excinfo.value)
    assert short not in str(excinfo.value)


def test_key_equal_to_secret_key_is_rejected_without_echo():
    shared = "compartida-" + "z" * 40
    with pytest.raises(ImproperlyConfigured) as excinfo:
        check_gateway_credential_key(shared, secret_key=shared)
    assert "SECRET_KEY" in str(excinfo.value)
    assert shared not in str(excinfo.value)


def test_valid_key_is_returned():
    assert check_gateway_credential_key(VALID_KEY, secret_key=OTHER_SECRET) == VALID_KEY


@pytest.mark.parametrize("raw", [None, "", "x" * (MIN_KEY_LENGTH - 1)])
def test_production_requires_valid_key_at_startup(raw):
    with pytest.raises(ImproperlyConfigured, match="obligatoria en producción"):
        resolve_gateway_credential_key(raw, debug=False, secret_key=OTHER_SECRET)


def test_production_rejects_secret_key_reuse():
    with pytest.raises(ImproperlyConfigured, match="obligatoria en producción"):
        resolve_gateway_credential_key(OTHER_SECRET, debug=False, secret_key=OTHER_SECRET)


def test_production_accepts_valid_key():
    assert resolve_gateway_credential_key(VALID_KEY, debug=False, secret_key=OTHER_SECRET) == (
        VALID_KEY
    )


@pytest.mark.parametrize("raw", [None, "", "corta"])
def test_development_does_not_block_startup_and_has_no_fallback(raw):
    value = resolve_gateway_credential_key(raw, debug=True, secret_key=OTHER_SECRET)
    assert value == ("" if raw is None else raw)
    assert value != OTHER_SECRET


def test_settings_resolve_the_key_with_debug_and_secret_key():
    source = pathlib.Path("config/settings.py").read_text(encoding="utf-8")
    assert "resolve_gateway_credential_key(" in source
    assert "platform.GATEWAY_CREDENTIAL_HMAC_KEY, debug=DEBUG, secret_key=SECRET_KEY" in source


def test_platform_reads_only_its_own_environment_variable():
    source = inspect.getsource(platform)
    assert 'os.getenv("GATEWAY_CREDENTIAL_HMAC_KEY", "")' in source
    assert KEY_NAME == "GATEWAY_CREDENTIAL_HMAC_KEY"


def test_module_has_no_secret_key_fallback():
    source = inspect.getsource(module)
    assert "settings.SECRET_KEY" not in source
    assert "getenv" not in source
