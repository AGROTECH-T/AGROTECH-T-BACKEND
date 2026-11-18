# ruff: noqa: S101
"""One-time device provisioning HTTP contract (MariaDB disposable only)."""
import base64
import secrets

import pytest
from rest_framework.test import APIClient

from apps.iot.models import CoordinatorCredential, GatewayCredential

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def credential_keys(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize(
    "kind,secret_field",
    [("coordinators", "provisioning_key"), ("gateways", "password")],
)
def test_device_registration_returns_individual_secret_once(admin, farm_id, kind, secret_field):
    url = f"/api/v1/iot/farms/{farm_id}/{kind}/"
    response = _client(admin).post(url, {"name": "Dispositivo"}, format="json")

    assert response.status_code == 201
    assert response["Cache-Control"].find("no-store") >= 0
    assert response.data["uid"].startswith("CO-" if kind == "coordinators" else "GW-")
    assert response.data[secret_field]
    credential_count = (
        CoordinatorCredential.objects.count()
        if kind == "coordinators"
        else GatewayCredential.objects.count()
    )
    assert credential_count == 1
    assert not response.data.get("fingerprint")


def test_coordinator_credential_rotation_and_revocation(admin, operator, farm_id):
    client = _client(admin)
    registration = client.post(
        f"/api/v1/iot/farms/{farm_id}/coordinators/", {"name": "C"}, format="json"
    )
    uid = registration.data["uid"]
    issue_url = f"/api/v1/iot/farms/{farm_id}/coordinators/{uid}/credentials/"
    first = client.post(issue_url, {"action": "rotate"}, format="json")
    assert first.status_code == 200
    assert first.data["key_version"] == 2
    assert first.data["provisioning_key"]
    assert client.get(issue_url).status_code == 200
    assert [item["key_version"] for item in client.get(issue_url).data] == [2, 1]

    denied = _client(operator).post(issue_url, {"action": "revoke"}, format="json")
    assert denied.status_code == 403
    revoked = client.post(issue_url, {"action": "revoke", "reason": "retiro"}, format="json")
    assert revoked.status_code == 200
    assert revoked.data["active"] is False
    assert "provisioning_key" not in revoked.data


def test_gateway_credential_rotation_does_not_expose_fingerprint(admin, farm_id):
    client = _client(admin)
    registration = client.post(
        f"/api/v1/iot/farms/{farm_id}/gateways/", {"name": "GW"}, format="json"
    )
    gateway_uid = registration.data["uid"]
    url = f"/api/v1/iot/farms/{farm_id}/gateways/{gateway_uid}/credentials/"
    issued = client.post(url, {"action": "rotate"}, format="json")
    assert issued.status_code == 200
    assert issued.data["username"] == gateway_uid
    assert issued.data["password"]
    history = client.get(url)
    assert history.status_code == 200
    assert "fingerprint" not in history.data[0]


def test_device_registration_rejects_supplied_identity_and_non_admin(admin, operator, farm_id):
    url = f"/api/v1/iot/farms/{farm_id}/coordinators/"
    bad = _client(admin).post(url, {"name": "C", "uid": "CO-ABCDEFGHJK"}, format="json")
    assert bad.status_code == 400
    denied = _client(operator).post(url, {"name": "C"}, format="json")
    assert denied.status_code == 403
