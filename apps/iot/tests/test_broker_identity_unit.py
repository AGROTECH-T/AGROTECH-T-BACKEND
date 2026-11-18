# ruff: noqa: S101, S105, S106, S603
"""BUILD-FLEPSI-02: pruebas sin base de datos de la identidad del broker."""

from __future__ import annotations

import base64
import logging
import os
import secrets
import subprocess
import sys
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from apps.iot.broker_identity_config import (
    BrokerIdentityConfig,
    BrokerIdentityMode,
    UnavailableKeyVersion,
    build_keyring,
    load_admin_keyring,
    load_broker_identity_config,
)
from apps.iot.mqtt.broker_identity import (
    MAX_USER_PROPERTIES,
    BrokerIdentityObservation,
    class_digest,
    classify_user_properties,
    token_fingerprint,
)
from apps.iot.mqtt.broker_identity import (
    BrokerIdentityPresence as P,
)
from apps.iot.mqtt.consumer import MqttConfigurationError, MqttConsumer, MqttSettings
from apps.iot.services.mqtt_delivery_receipts import (
    MqttReceiptResult,
    MqttReceiptStatus,
    build_transport_key,
)
from apps.iot.tests.test_mqtt_consumer import FakeClient, FakeMessage

SENTINEL = "SENTINEL-TOKEN-7f3a9c"


def b64key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def keyring(version=1, previous=None, next_=None):
    return build_keyring(
        raw_key=b64key(), raw_version=str(version), raw_previous=previous, raw_next=next_
    )


@pytest.fixture(autouse=True)
def advisory_candidate_without_database(monkeypatch):
    monkeypatch.setattr("apps.iot.mqtt.consumer._coordinator_candidate", lambda t, p: None)


# -- Clasificación (casos 1-5) ------------------------------------------------


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        (None, P.ABSENT),
        ([], P.ABSENT),
        ([("otro", "x")], P.ABSENT),
        ([("token_id", "123")], P.SINGLE),
        ([("token_id", "ñandú-ü")], P.SINGLE),
        ([("token_id", "a"), ("token_id", "a")], P.MULTIPLE),
        ([("token_id", "a"), ("token_id", "b")], P.MULTIPLE),
        ([("token_id", "a"), ("Token_ID", "b")], P.MULTIPLE),
        ([("token_id", "")], P.INVALID),
        ([("token_id", " 1")], P.INVALID),
        ([("token_id", "1 ")], P.INVALID),
        ([("token_id", "1\x07")], P.INVALID),
        ([("token_id", "\ud800")], P.INVALID),
        ([("token_id", 123)], P.INVALID),
        ([("token_id", "x" * 257)], P.OVERSIZED),
        ([("token_id", "é" * 129)], P.OVERSIZED),
        ([("p", "v")] * (MAX_USER_PROPERTIES + 1), P.OVERSIZED),
    ],
)
def test_classification(props, expected):
    assert classify_user_properties(props)[0] is expected


def test_limits_are_inclusive():
    assert classify_user_properties([("token_id", "x" * 256)])[0] is P.SINGLE
    props = [("p", "v")] * (MAX_USER_PROPERTIES - 1) + [("token_id", "1")]
    assert classify_user_properties(props)[0] is P.SINGLE


def test_duplicates_survive_real_mqtt5_property_roundtrip():
    """Paho conserva duplicados como lista; dict() los perdería."""
    props = Properties(PacketTypes.PUBLISH)
    props.UserProperty = ("token_id", "111")
    props.UserProperty = ("token_id", "222")
    decoded = Properties(PacketTypes.PUBLISH)
    decoded.unpack(props.pack())
    assert classify_user_properties(decoded.UserProperty)[0] is P.MULTIPLE
    assert len(dict(decoded.UserProperty)) == 1  # por eso nunca se usa dict()


# -- Huellas ----------------------------------------------------------------


def test_fingerprint_is_keyed_versioned_and_domain_separated():
    k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
    fp = token_fingerprint(k1, b"123")
    assert len(fp) == 32
    assert fp == token_fingerprint(k1, b"123")
    assert fp != token_fingerprint(k2, b"123")
    assert fp != token_fingerprint(k1, b"124")
    assert fp != token_fingerprint(k1, b"123", provider="flespix")
    assert class_digest(k1, P.ABSENT) != class_digest(k1, P.MULTIPLE)


def test_observation_never_exposes_the_value():
    ring = keyring()
    obs = BrokerIdentityObservation.from_user_properties([("token_id", SENTINEL)], ring)
    assert SENTINEL not in repr(obs)
    presence, version, fp = obs.evidence_for_new_delivery()
    assert (presence, version) == ("single", 1)
    assert SENTINEL.encode() not in fp
    with pytest.raises(UnavailableKeyVersion):
        obs.digest(9)


# -- Claves y modos (case 23) ---------------------------------------------------


SETTINGS = SimpleNamespace(
    SECRET_KEY="x" * 50,
    GATEWAY_CREDENTIAL_HMAC_KEY="g" * 48,
    INVITATION_CODE_HMAC_KEY="i" * 48,
    COORDINATOR_KEY_ENCRYPTION_KEY=b64key(),
    COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS="",
)


def test_mode_off_by_default_needs_no_key():
    config = load_broker_identity_config({}, django_settings=SETTINGS)
    assert config.mode is BrokerIdentityMode.OFF and config.keyring is None


def test_enforce_is_refused():
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        load_broker_identity_config(
            {"IOT_BROKER_IDENTITY_MODE": "enforce"}, django_settings=SETTINGS
        )
    with pytest.raises(ImproperlyConfigured, match="enforce"):
        load_admin_keyring({"IOT_BROKER_IDENTITY_MODE": "ENFORCE"}, django_settings=SETTINGS)


def test_record_loads_a_versioned_ring_without_exposing_keys():
    current, previous, nxt = b64key(), b64key(), b64key()
    config = load_broker_identity_config(
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": current,
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "2",
            "IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS": f"1:{previous}",
            "IOT_BROKER_IDENTITY_HMAC_KEY_NEXT": f"3:{nxt}",
        },
        django_settings=SETTINGS,
    )
    ring = config.keyring
    assert (ring.current_version, ring.next_version, ring.previous_versions) == (2, 3, {1})
    text = ring.describe() + repr(ring) + repr(config)
    assert all(value not in text for value in (current, previous, nxt))


@pytest.mark.parametrize(
    "env",
    [
        {"IOT_BROKER_IDENTITY_MODE": "record"},
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": "no-base64!",
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": base64.b64encode(b"short").decode(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": " " + b64key(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": b64key(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "0",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": b64key(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "65536",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": b64key(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
            "IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS": f"1:{b64key()}",
        },
        {
            "IOT_BROKER_IDENTITY_MODE": "record",
            "IOT_BROKER_IDENTITY_HMAC_KEY": b64key(),
            "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
            "IOT_BROKER_IDENTITY_HMAC_KEY_NEXT": f"2:{b64key()},3:{b64key()}",
        },
        {"IOT_BROKER_IDENTITY_MODE": "maybe"},
    ],
)
def test_invalid_configurations_fail_closed(env):
    with pytest.raises(ImproperlyConfigured) as info:
        load_broker_identity_config(env, django_settings=SETTINGS)
    for value in env.values():
        if len(value) > 20:
            assert value not in str(info.value)


def test_same_key_reused_across_versions_is_refused():
    key = b64key()
    with pytest.raises(ImproperlyConfigured):
        build_keyring(raw_key=key, raw_version="2", raw_previous=f"1:{key}", raw_next=None)


@pytest.mark.parametrize("other", ["COORDINATOR_KEY_ENCRYPTION_KEY", "GATEWAY_CREDENTIAL_HMAC_KEY"])
def test_identity_key_must_be_independent(other):
    key = b64key()
    settings = SimpleNamespace(**{**vars(SETTINGS), other: key})
    with pytest.raises(ImproperlyConfigured, match="independiente"):
        load_broker_identity_config(
            {
                "IOT_BROKER_IDENTITY_MODE": "record",
                "IOT_BROKER_IDENTITY_HMAC_KEY": key,
                "IOT_BROKER_IDENTITY_HMAC_KEY_VERSION": "1",
            },
            django_settings=settings,
        )


@pytest.mark.parametrize("command", ["mqtt_consumer", "mqtt_delivery_worker"])
def test_enforce_makes_process_exit_with_status_one(command):
    env = dict(os.environ)
    env.update(
        {
            "IOT_BROKER_IDENTITY_MODE": "enforce",
            "MQTT_HOST": "broker.test",
            "MQTT_CLIENT_ID": "c",
            "MQTT_TLS": "true",
            "DJANGO_SETTINGS_MODULE": os.environ.get(
                "DJANGO_SETTINGS_MODULE", "config.settings_test"
            ),
        }
    )
    args = [sys.executable, "-B", "manage.py", command]
    if command == "mqtt_consumer":
        args.append("--check")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    result = subprocess.run(
        args,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    output = (result.stdout or b"") + (result.stderr or b"")
    detail = (
        f"rc={result.returncode} stdout={result.stdout is not None} "
        f"stderr={result.stderr is not None}"
    )
    assert result.returncode == 1, detail
    assert b"ImproperlyConfigured" in output, detail
    assert b"enforce" in output, detail


# -- MQTT 5 en el consumer ----------------------------------------------------


def test_mqtt5_connect_uses_clean_start_false_and_session_expiry():
    client = FakeClient()
    consumer = MqttConsumer(
        MqttSettings(host="h", port=8883, client_id="c", session_expiry_seconds=7200),
        client=client,
        receipt_func=lambda **_k: MqttReceiptResult(MqttReceiptStatus.PERSISTED),
    )
    client.loop_forever = lambda **_k: consumer.stop()
    consumer.run()
    kwargs = client.connect_async_kwargs[0]
    assert kwargs["clean_start"] is False
    assert kwargs["properties"].SessionExpiryInterval == 7200


@pytest.mark.parametrize("value", ["0", "59", "4294967296", "x"])
def test_session_expiry_must_be_positive_and_bounded(value):
    with pytest.raises(MqttConfigurationError):
        MqttSettings.from_env(
            {"MQTT_HOST": "h", "MQTT_CLIENT_ID": "c", "MQTT_SESSION_EXPIRY_SECONDS": value},
            production=False,
        )


def test_real_mqtt5_client_builds_without_clean_session():
    from apps.iot.mqtt.consumer import _build_client

    client = _build_client(MqttSettings(host="h", port=8883, client_id="c", tls=True))
    assert client._protocol == 5 and client._manual_ack is True


def _message_with(props):
    message = FakeMessage()
    message.properties = SimpleNamespace(UserProperty=props)
    return message


def test_record_mode_passes_evidence_and_off_mode_ignores_properties():
    seen = []

    def receipt(**kwargs):
        seen.append(kwargs.get("broker_identity"))
        return MqttReceiptResult(MqttReceiptStatus.PERSISTED)

    record = BrokerIdentityConfig(mode=BrokerIdentityMode.RECORD, keyring=keyring())
    client = FakeClient()
    MqttConsumer(
        MqttSettings(host="h", port=1, client_id="c"),
        client=client,
        receipt_func=receipt,
        broker_identity=record,
    )._on_message(client, None, _message_with([("token_id", "42")]))
    off_client = FakeClient()
    MqttConsumer(
        MqttSettings(host="h", port=1, client_id="c"), client=off_client, receipt_func=receipt
    )._on_message(off_client, None, _message_with([("token_id", "42")]))

    assert seen[0].presence is P.SINGLE
    assert seen[1] is None
    assert client.ack_calls == [(42, 1)] and off_client.ack_calls == [(42, 1)]


def test_token_value_never_reaches_logs():
    records = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("apps.iot")
    handler = Collector(level=logging.DEBUG)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:

        def failing(**_kwargs):
            raise RuntimeError(f"fallo {SENTINEL}")

        record = BrokerIdentityConfig(mode=BrokerIdentityMode.RECORD, keyring=keyring())
        client = FakeClient()
        MqttConsumer(
            MqttSettings(host="h", port=1, client_id="c"),
            client=client,
            receipt_func=failing,
            broker_identity=record,
        )._on_message(client, None, _message_with([("token_id", SENTINEL)]))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    assert records and all(SENTINEL not in text for text in records)


# -- Contrato de transporte (caso 30) ----------------------------------------------


def test_transport_key_known_answer_is_unchanged():
    key = build_transport_key(topic="agrotech/v1/gw/GW-7K3M9QXT2A/up", payload=b'{"v":1}')
    assert key.hex() == KNOWN_TRANSPORT_KEY


KNOWN_TRANSPORT_KEY = "99bd9642ed92c8a618ce829ecb04aeaa289c9f9cbecb3bf358f899b132686bac"
