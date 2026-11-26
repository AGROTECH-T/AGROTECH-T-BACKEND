# ruff: noqa: S101
"""El bloqueo al crear cuenta, finca o invitación sigue la IP, no la cuenta.

@author Cristian Deysdayr Jimenez
"""
from types import SimpleNamespace

import pytest
from rest_framework.request import Request
from rest_framework.settings import api_settings

from apps.accounts.api.throttles import OriginIpThrottle
from apps.accounts.api.views import RegisterView
from apps.farms.tests.conftest import VALID_PASSWORD
from apps.farms.views import FarmInvitationListCreateView, FarmListCreateView

REGISTER = "/api/v1/auth/register/"
FARMS = "/api/v1/farms/"
INVITES = "/api/v1/farms/1/invitations/"


def _rates(settings, **extra):
    current = settings.REST_FRAMEWORK
    settings.REST_FRAMEWORK = {
        **current,
        "DEFAULT_THROTTLE_RATES": {**current["DEFAULT_THROTTLE_RATES"], **extra},
    }
    api_settings.reload()
    OriginIpThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES


def _token(client, account):
    response = client.post(
        "/api/v1/auth/login/",
        {"identification": account.identification, "password": VALID_PASSWORD},
        format="json",
        REMOTE_ADDR="10.0.0.8",
    )
    assert response.status_code == 200
    return response.data["token"]


def _auth(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_creation_views_count_by_origin_ip():
    """Las altas usan la cuota de IP y no la de cuenta."""
    assert RegisterView.throttle_classes == [OriginIpThrottle]
    assert FarmListCreateView.throttle_classes == [OriginIpThrottle]
    assert FarmInvitationListCreateView.throttle_classes == [OriginIpThrottle]


def test_same_ip_shares_the_bucket_across_accounts(rf, settings):
    """Otra cuenta o X-Forwarded-For no abre cupo; otra IP sí."""
    _rates(settings, register="1/hour")
    view = SimpleNamespace(throttle_scope="register")

    def ask(addr, account, forwarded="203.0.113.8"):
        django_request = rf.post(
            REGISTER,
            REMOTE_ADDR=addr,
            HTTP_X_FORWARDED_FOR=forwarded,
        )
        request = Request(django_request)
        request.user = SimpleNamespace(is_authenticated=True, pk=account)
        return OriginIpThrottle().allow_request(request, view)

    assert ask("10.8.0.1", 1) is True
    assert ask("10.8.0.1", 2, forwarded="198.51.100.7") is False
    assert ask("10.8.0.2", 2) is True


@pytest.mark.django_db
def test_register_lock_follows_the_ip(client, settings):
    """Otra identificación o una cabecera reenviada no abre una cuota nueva."""
    _rates(settings, register="2/hour")
    for _ in range(2):
        first = client.post(REGISTER, {}, format="json", REMOTE_ADDR="10.8.0.1")
        assert first.status_code == 400
    blocked = client.post(
        REGISTER,
        {"identification": "99999999"},
        format="json",
        REMOTE_ADDR="10.8.0.1",
        HTTP_X_FORWARDED_FOR="203.0.113.9",
    )
    other = client.post(REGISTER, {}, format="json", REMOTE_ADDR="10.8.0.2")
    assert blocked.status_code == 429
    assert other.status_code == 400


@pytest.mark.django_db
def test_farm_create_lock_is_shared_by_ip(client, make_user, settings):
    """Dos cuentas en la misma IP comparten el cupo de crear fincas."""
    _rates(settings, farm_create="1/hour")
    token_a = _token(client, make_user("11111111"))
    token_b = _token(client, make_user("22222222"))
    created = client.post(
        FARMS,
        {"name": "Finca Norte"},
        format="json",
        REMOTE_ADDR="10.4.4.4",
        **_auth(token_a),
    )
    shared = client.post(
        FARMS,
        {"name": "Finca Sur"},
        format="json",
        REMOTE_ADDR="10.4.4.4",
        **_auth(token_b),
    )
    other_ip = client.post(
        FARMS,
        {"name": "Finca Este"},
        format="json",
        REMOTE_ADDR="10.4.4.5",
        **_auth(token_b),
    )
    assert created.status_code == 201
    assert shared.status_code == 429
    assert other_ip.status_code == 201


@pytest.mark.django_db
def test_invitation_create_lock_is_shared_by_ip(client, make_user, settings):
    """Crear invitaciones se cuenta por IP aunque cambie la cuenta."""
    _rates(settings, invitation_create="2/hour")
    token_a = _token(client, make_user("33333333"))
    token_b = _token(client, make_user("44444444"))
    for _ in range(2):
        attempt = client.post(
            INVITES,
            {},
            format="json",
            REMOTE_ADDR="10.5.5.5",
            **_auth(token_a),
        )
        assert attempt.status_code != 429
    blocked = client.post(
        INVITES,
        {},
        format="json",
        REMOTE_ADDR="10.5.5.5",
        HTTP_X_FORWARDED_FOR="198.51.100.4",
        **_auth(token_b),
    )
    fresh = client.post(
        INVITES,
        {},
        format="json",
        REMOTE_ADDR="10.5.5.6",
        **_auth(token_b),
    )
    assert blocked.status_code == 429
    assert fresh.status_code != 429
