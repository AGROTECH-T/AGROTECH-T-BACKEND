# ruff: noqa: S101
"""Muchas peticiones bloquean la IP y, por separado, la cuenta.

@author Cristian Deysdayr Jimenez
"""
from types import SimpleNamespace

from rest_framework.settings import api_settings

from apps.accounts.api.throttles import LoginIdentifierThrottle, OriginIpThrottle
from apps.accounts.api.views import LoginView


def _rates(settings, **extra):
    current = settings.REST_FRAMEWORK
    settings.REST_FRAMEWORK = {
        **current,
        "DEFAULT_THROTTLE_RATES": {**current["DEFAULT_THROTTLE_RATES"], **extra},
    }
    api_settings.reload()
    OriginIpThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
    LoginIdentifierThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES


def _request(addr, identification, forwarded="203.0.113.9"):
    return SimpleNamespace(
        data={"identification": identification},
        META={"REMOTE_ADDR": addr, "HTTP_X_FORWARDED_FOR": forwarded},
        user=SimpleNamespace(is_authenticated=False, pk=None),
    )


def test_login_counts_the_ip_and_the_account():
    """El acceso tiene cupo por IP real y cupo por cuenta."""
    assert OriginIpThrottle in LoginView.throttle_classes
    assert LoginIdentifierThrottle in LoginView.throttle_classes


def test_one_ip_blocks_even_if_the_account_changes(settings):
    """Cambiar de cuenta en la misma IP no abre otro cupo."""
    _rates(settings, login="1/minute")
    view = SimpleNamespace(throttle_scope="login")
    first = OriginIpThrottle().allow_request(_request("10.8.0.1", "11111111"), view)
    second = OriginIpThrottle().allow_request(
        _request("10.8.0.1", "22222222", "198.51.100.4"),
        view,
    )
    third = OriginIpThrottle().allow_request(_request("10.8.0.2", "22222222"), view)
    assert first is True
    assert second is False
    assert third is True


def test_one_account_blocks_even_from_another_ip(settings):
    """La misma cuenta se agota aunque llegue desde otra IP."""
    _rates(settings, login_account="1/minute")
    view = SimpleNamespace(throttle_scope="login")
    same = LoginIdentifierThrottle()
    other_ip = LoginIdentifierThrottle()
    other_account = LoginIdentifierThrottle()
    assert same.allow_request(_request("10.1.0.1", "33333333"), view) is True
    assert other_ip.allow_request(_request("10.1.0.2", "33333333"), view) is False
    assert other_account.allow_request(_request("10.1.0.2", "44444444"), view) is True
