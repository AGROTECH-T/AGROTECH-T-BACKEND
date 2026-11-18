# ruff: noqa: S101, S105, S106
"""Secretos y huellas de credenciales de gateway (IoT-03.1, sin base de datos).

Propósito: formato y entropía del secreto, huella HMAC determinista y ligada al
gateway, verificación en tiempo constante, versiones de clave y fallo cerrado
sin clave. Ningún mensaje ni ``repr`` revela el secreto, la huella o la clave.
"""
import hashlib
import hmac
import re
import secrets
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.iot import gateway_credentials as crypto
from apps.iot.services.credentials import IssuedGatewayCredential

UID_A = "GW-ABCDEFGHJK"
UID_B = "GW-MNPQRSTUVW"
SECRET_RE = re.compile(r"^gws_[A-Za-z0-9_-]{43}$")


@pytest.fixture
def key(settings):
    value = secrets.token_urlsafe(48)
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = value
    settings.SECRET_KEY = "otra-" + secrets.token_urlsafe(48)
    return value


# --- Secreto ---


def test_secret_has_prefix_and_format():
    secret = crypto.generate_secret()
    assert secret.startswith("gws_")
    assert len(secret) == crypto.SECRET_LENGTH == 47
    assert SECRET_RE.match(secret)
    assert crypto.is_well_formed_secret(secret)


def test_secret_uses_token_urlsafe_with_32_bytes(monkeypatch):
    calls = []

    def fake(nbytes):
        calls.append(nbytes)
        return "A" * 43

    monkeypatch.setattr(crypto.secrets, "token_urlsafe", fake)
    assert crypto.generate_secret() == "gws_" + "A" * 43
    assert calls == [32]


def test_secrets_are_distinct_and_high_entropy():
    values = {crypto.generate_secret() for _ in range(1000)}
    assert len(values) == 1000
    alphabet = set("".join(v[4:] for v in values))
    assert len(alphabet) >= 60  # el alfabeto URL-safe tiene 64 símbolos


@pytest.mark.parametrize(
    "value",
    [None, 1, "", "gws_", "gws_" + "A" * 42, "gws_" + "A" * 44, "gwx_" + "A" * 43,
     "GWS_" + "A" * 43, "gws_" + "A" * 42 + "=", "gws_" + "A" * 42 + "ñ", " gws_" + "A" * 42],
)
def test_malformed_secrets_are_rejected(value):
    assert crypto.is_well_formed_secret(value) is False


# --- Huella ---


def test_fingerprint_is_hmac_sha256_with_versioned_context(key):
    secret = crypto.generate_secret()
    expected = hmac.new(
        key.encode(), f"agrotech:gw-cred:v1:{UID_A}:{secret}".encode(), hashlib.sha256
    ).hexdigest()
    assert crypto.fingerprint(UID_A, secret) == expected
    assert crypto.is_well_formed_fingerprint(expected)
    assert len(expected) == 64


def test_fingerprint_is_deterministic(key):
    secret = crypto.generate_secret()
    assert crypto.fingerprint(UID_A, secret) == crypto.fingerprint(UID_A, secret)


def test_fingerprint_differs_per_gateway_and_secret(key):
    secret = crypto.generate_secret()
    assert crypto.fingerprint(UID_A, secret) != crypto.fingerprint(UID_B, secret)
    assert crypto.fingerprint(UID_A, secret) != crypto.fingerprint(
        UID_A, crypto.generate_secret()
    )


def test_fingerprint_depends_on_server_key(key, settings):
    secret = crypto.generate_secret()
    first = crypto.fingerprint(UID_A, secret)
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    assert crypto.fingerprint(UID_A, secret) != first


@pytest.mark.parametrize("uid", ["gw-abcdefghjk", "CO-ABCDEFGHJK", "GW-ABC", None])
def test_fingerprint_rejects_malformed_uid_without_echo(key, uid):
    secret = crypto.generate_secret()
    with pytest.raises(crypto.InvalidGatewayCredential) as excinfo:
        crypto.fingerprint(uid, secret)
    assert secret not in str(excinfo.value) and secret not in repr(excinfo.value)


def test_fingerprint_rejects_malformed_secret_without_echo(key):
    bad = "gws_" + "A" * 10
    with pytest.raises(crypto.InvalidGatewayCredential) as excinfo:
        crypto.fingerprint(UID_A, bad)
    assert bad not in str(excinfo.value)


def test_new_credential_for_returns_secret_fingerprint_and_version(key):
    secret, fingerprint, version = crypto.new_credential_for(UID_A)
    assert crypto.is_well_formed_secret(secret)
    assert fingerprint == crypto.fingerprint(UID_A, secret)
    assert version == crypto.CURRENT_KEY_VERSION == 1


# --- Verificación ---


def test_verify_correct_and_incorrect(key):
    secret, fingerprint, version = crypto.new_credential_for(UID_A)
    kwargs = {"expected_fingerprint": fingerprint, "key_version": version}
    assert crypto.verify_secret(UID_A, secret, **kwargs) is True
    assert crypto.verify_secret(UID_A, crypto.generate_secret(), **kwargs) is False
    assert crypto.verify_secret(UID_B, secret, **kwargs) is False
    assert crypto.verify_secret(UID_A, "basura", **kwargs) is False
    assert crypto.verify_secret(UID_A, None, **kwargs) is False


def test_verify_uses_compare_digest(key, monkeypatch):
    secret, fingerprint, version = crypto.new_credential_for(UID_A)
    calls = []
    original = crypto.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(crypto.hmac, "compare_digest", spy)
    assert crypto.verify_secret(
        UID_A, secret, expected_fingerprint=fingerprint, key_version=version
    )
    # La validación de la clave también usa compare_digest (clave ≠ SECRET_KEY).
    assert (fingerprint, fingerprint) in calls


def test_unknown_key_version_fails_closed(key):
    secret, fingerprint, _version = crypto.new_credential_for(UID_A)
    assert (
        crypto.verify_secret(UID_A, secret, expected_fingerprint=fingerprint, key_version=2)
        is False
    )
    with pytest.raises(crypto.UnsupportedKeyVersion):
        crypto.fingerprint(UID_A, secret, key_version=2)


def test_malformed_expected_fingerprint_never_matches(key):
    secret, _fingerprint, version = crypto.new_credential_for(UID_A)
    for expected in (None, "", "A" * 64, "0" * 63):
        assert (
            crypto.verify_secret(UID_A, secret, expected_fingerprint=expected, key_version=version)
            is False
        )


# --- Clave del servidor ---


@pytest.mark.parametrize("value", ["", "corta"])
def test_missing_or_short_key_fails_closed(settings, value):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = value
    with pytest.raises(ImproperlyConfigured):
        crypto.new_credential_for(UID_A)


def test_key_equal_to_secret_key_fails_closed(settings):
    shared = secrets.token_urlsafe(48)
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = shared
    settings.SECRET_KEY = shared
    with pytest.raises(ImproperlyConfigured) as excinfo:
        crypto.new_credential_for(UID_A)
    assert shared not in str(excinfo.value)


def test_missing_key_propagates_in_verification(settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        crypto.verify_secret(
            UID_A, crypto.generate_secret(), expected_fingerprint="0" * 64, key_version=1
        )


# --- Sin fugas por repr ni variables sensibles ---


def test_issued_credential_repr_hides_secret():
    secret = crypto.generate_secret()
    issued = IssuedGatewayCredential(credential=SimpleNamespace(pk=1), secret=secret)
    assert secret not in repr(issued)
    assert secret not in str(issued)
    assert issued.secret == secret


MODULE_FUNCTIONS = {
    "fingerprint",
    "verify_secret",
    "new_credential_for",
    "_key",
    "sensitive_variables_wrapper",
}


@pytest.mark.parametrize("call", ["fingerprint", "verify_secret"])
def test_error_reports_hide_secret_in_module_frames(settings, call):
    """El informe de errores de Django no muestra el secreto en los frames del módulo."""
    import sys

    from django.views.debug import ExceptionReporter

    settings.GATEWAY_CREDENTIAL_HMAC_KEY = ""  # provoca ImproperlyConfigured dentro
    secret = crypto.generate_secret()
    try:
        if call == "fingerprint":
            crypto.fingerprint(UID_A, secret)
        else:
            crypto.verify_secret(UID_A, secret, expected_fingerprint="0" * 64, key_version=1)
    except ImproperlyConfigured:
        exc_type, exc, tb = sys.exc_info()
    data = ExceptionReporter(None, exc_type, exc, tb).get_traceback_data()
    module_frames = [f for f in data["frames"] if f["function"] in MODULE_FUNCTIONS]
    assert module_frames
    for frame in module_frames:
        assert secret not in str(frame["vars"]), frame["function"]
    assert secret not in str(exc)
