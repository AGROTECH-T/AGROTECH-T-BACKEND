# ruff: noqa: S101, S105, S106
"""BUILD-FLEPSI-02H: ENFORCE sin degradación a OFF y entrada segura del token.

Sin base de datos.
"""

from __future__ import annotations

import base64
import io
import secrets

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.iot.broker_identity_config import (
    BrokerIdentityConfig,
    BrokerIdentityMode,
    build_keyring,
)
from apps.iot.management.commands import mqtt_broker_identity as identity_command
from apps.iot.mqtt.consumer import MqttConsumer, MqttSettings
from apps.iot.services import mqtt_delivery_processing as processing_module
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.tests.test_mqtt_consumer import FakeClient

SENTINEL = "SENTINEL-TOKEN-7f3a9c"


def ring():
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return build_keyring(raw_key=key, raw_version="1", raw_previous=None, raw_next=None)


def forged_enforce():
    """Bypasses __post_init__ the way only deliberate code could."""
    config = object.__new__(BrokerIdentityConfig)
    object.__setattr__(config, "mode", BrokerIdentityMode.ENFORCE)
    object.__setattr__(config, "keyring", ring())
    return config


def test_off_and_record_configs_are_valid():
    assert BrokerIdentityConfig(mode=BrokerIdentityMode.OFF).records is False
    assert BrokerIdentityConfig(mode=BrokerIdentityMode.RECORD, keyring=ring()).records is True


def test_direct_enforce_construction_fails_explicitly():
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        BrokerIdentityConfig(mode=BrokerIdentityMode.ENFORCE, keyring=ring())


def test_record_without_keyring_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="anillo"):
        BrokerIdentityConfig(mode=BrokerIdentityMode.RECORD)


def test_consumer_rejects_hand_built_enforce_instead_of_degrading():
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        MqttConsumer(
            MqttSettings(host="h", port=1, client_id="c"),
            client=FakeClient(),
            broker_identity=forged_enforce(),
        )


def test_worker_rejects_hand_built_enforce_before_any_claim():
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        run_mqtt_delivery_worker_once(worker_id="w", broker_identity=forged_enforce())


def test_worker_rejects_enforce_from_environment_each_batch(monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "enforce")
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        run_mqtt_delivery_worker_once(worker_id="w")


def test_processing_never_degrades_enforce_to_off(monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "enforce")
    with pytest.raises(ImproperlyConfigured):
        processing_module._broker_identity_records()
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    assert processing_module._broker_identity_records() is True
    monkeypatch.delenv("IOT_BROKER_IDENTITY_MODE")
    assert processing_module._broker_identity_records() is False


def test_worker_command_does_not_swallow_configuration_errors(monkeypatch):
    calls = []

    def batch(**_kwargs):
        calls.append(1)
        raise ImproperlyConfigured("IOT_BROKER_IDENTITY_MODE=enforce no está habilitado")

    monkeypatch.setattr(
        "apps.iot.management.commands.mqtt_delivery_worker.run_mqtt_delivery_worker_once", batch
    )
    with pytest.raises(ImproperlyConfigured):
        call_command("mqtt_delivery_worker", "--poll-seconds", "0.1", stdout=io.StringIO())
    assert calls == [1]  # no retry loop


def test_identity_command_rejects_enforce(monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "enforce")
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        call_command("mqtt_broker_identity", "versions", stdout=io.StringIO())


# -- H-05 ---------------------------------------------------------------------


class TtyStream(io.StringIO):
    def isatty(self):
        return True

    def readline(self, *_args):  # pragma: no cover - must never be used
        raise AssertionError("una terminal nunca se lee con readline (eco)")


def test_interactive_input_uses_getpass(monkeypatch):
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return SENTINEL

    monkeypatch.setattr(identity_command.getpass, "getpass", fake_getpass)
    assert identity_command._read_token({"stdin": TtyStream()}) == SENTINEL
    assert prompts and SENTINEL not in prompts[0]


def test_piped_input_is_read_without_prompt(monkeypatch):
    monkeypatch.setattr(
        identity_command.getpass, "getpass", lambda _p: pytest.fail("no debe pedir por terminal")
    )
    assert identity_command._read_token({"stdin": io.StringIO(SENTINEL + "\r\n")}) == SENTINEL


@pytest.mark.parametrize("stream", [io.StringIO(""), io.StringIO("\n")])
def test_empty_input_is_rejected(stream):
    with pytest.raises(CommandError, match="No se recibió"):
        identity_command._read_token({"stdin": stream})
