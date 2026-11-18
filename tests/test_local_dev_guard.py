"""Pruebas de la protección del arranque local (config.local_dev_guard, cierre F10.3).

No importan ``config.settings_local`` ni se conectan a nada: validan las
funciones puras que usa esa configuración.
"""
import pytest
from django.core.exceptions import ImproperlyConfigured

from config.local_dev_guard import disable_external_email, validate_local_settings

REDIS = {"default": {"BACKEND": "django_redis.cache.RedisCache"}}
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
KEY = "k" * 64
SECRET = "s" * 50


def _validate(**overrides):
    values = dict(debug=True, caches=REDIS, hmac_key=KEY, secret_key=SECRET,
                  environ={"REDIS_URL": "redis://:x@127.0.0.1:6379/0"})
    values.update(overrides)
    validate_local_settings(**values)


def test_valid_local_configuration_is_accepted():
    _validate()


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"debug": False}, "DJANGO_DEBUG"),
        ({"environ": {}}, "REDIS_URL"),
        ({"caches": LOCMEM}, "REDIS_URL"),
        ({"hmac_key": ""}, "INVITATION_CODE_HMAC_KEY"),
        ({"hmac_key": "corta"}, "al menos 32"),
        ({"hmac_key": SECRET, "secret_key": SECRET}, "SECRET_KEY"),
    ],
)
def test_invalid_local_configuration_is_rejected(overrides, fragment):
    with pytest.raises(ImproperlyConfigured, match=fragment):
        _validate(**overrides)


def test_rejection_never_includes_the_key():
    with pytest.raises(ImproperlyConfigured) as exc:
        _validate(hmac_key=SECRET, secret_key=SECRET, environ={})
    assert SECRET not in str(exc.value)


def test_brevo_is_removed_only_from_the_given_environment():
    environ = {"BREVO_API_KEY": "valor-ficticio", "OTRA": "1"}
    disable_external_email(environ)
    assert environ == {"OTRA": "1"}
    disable_external_email(environ)  # idempotente
    assert environ == {"OTRA": "1"}
