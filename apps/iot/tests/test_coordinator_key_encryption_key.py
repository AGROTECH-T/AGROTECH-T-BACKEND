# ruff: noqa: S101, S105, S106
"""Validación de COORDINATOR_KEY_ENCRYPTION_KEY (IoT-03.2, sin base de datos).

Propósito: clave maestra Base64 de exactamente 32 bytes, obligatoria y validada
al arrancar solo en producción; versiones vigentes e históricas; sin alternativa
con ``SECRET_KEY``; los mensajes nunca revelan el valor.
"""
import base64
import secrets

import pytest
from django.core.exceptions import ImproperlyConfigured

import config.platform as platform
from config.coordinator_key_encryption_key import (
    KEY_BYTES,
    KEY_NAME,
    PREVIOUS_NAME,
    VERSION_NAME,
    MasterKeyRing,
    UnsupportedMasterKeyVersion,
    build_master_key_ring,
    decode_master_key,
    load_master_key_ring,
    parse_master_key_version,
    parse_previous_master_keys,
    validate_coordinator_master_key,
)

SECRET_KEY_OTHER = "s" * 50


def _encoded(size: int = KEY_BYTES) -> str:
    return base64.b64encode(secrets.token_bytes(size)).decode("ascii")


VALID_KEY = _encoded()
OTHER_KEY = _encoded()


# --- Decodificación ---


@pytest.mark.parametrize("raw", [None, "", "   ", 123, b"bytes", object()])
def test_decode_rejects_missing_or_non_text(raw):
    with pytest.raises(ImproperlyConfigured, match="no está configurada"):
        decode_master_key(raw)


@pytest.mark.parametrize("raw", ["no-es-base64!", "frase de paso", "-" * 44])
def test_decode_rejects_invalid_base64(raw):
    with pytest.raises(ImproperlyConfigured, match="Base64 válido"):
        decode_master_key(raw)


@pytest.mark.parametrize("size", [16, 31, 33, 64])
def test_decode_rejects_wrong_length(size):
    with pytest.raises(ImproperlyConfigured, match=str(KEY_BYTES)):
        decode_master_key(_encoded(size))


def test_decode_rejects_external_spaces():
    with pytest.raises(ImproperlyConfigured, match="espacios externos"):
        decode_master_key(f" {VALID_KEY}")


def test_decode_returns_exactly_32_bytes():
    key = decode_master_key(VALID_KEY)
    assert isinstance(key, bytes) and len(key) == KEY_BYTES


def test_decode_never_echoes_the_value():
    bad = base64.b64encode(b"x" * 16).decode("ascii")
    with pytest.raises(ImproperlyConfigured) as excinfo:
        decode_master_key(bad)
    assert bad not in str(excinfo.value)


# --- Versiones ---


@pytest.mark.parametrize("raw", [1, "1", " 2 ", 10])
def test_version_accepts_positive_integers(raw):
    assert parse_master_key_version(raw) >= 1


@pytest.mark.parametrize("raw", [0, -1, "0", "abc", "", "  ", None, True, False, 1.5])
def test_version_rejects_invalid_values(raw):
    with pytest.raises(ImproperlyConfigured, match=VERSION_NAME):
        parse_master_key_version(raw)


def test_previous_accepts_empty():
    assert parse_previous_master_keys(None) == {}
    assert parse_previous_master_keys("") == {}
    assert parse_previous_master_keys("   ") == {}


def test_previous_parses_version_key_pairs():
    previous = parse_previous_master_keys(f"2:{OTHER_KEY}, 3:{VALID_KEY}")
    assert set(previous) == {2, 3}
    assert all(len(key) == KEY_BYTES for key in previous.values())


@pytest.mark.parametrize(
    "raw",
    ["sin-dos-puntos", "2:", ":abc", f"2:{OTHER_KEY},", f"2:{OTHER_KEY},2:{VALID_KEY}"],
)
def test_previous_rejects_malformed_entries(raw):
    with pytest.raises(ImproperlyConfigured):
        parse_previous_master_keys(raw)


def test_previous_error_names_the_version_without_the_key():
    with pytest.raises(ImproperlyConfigured) as excinfo:
        parse_previous_master_keys(f"7:{OTHER_KEY},7:{VALID_KEY}")
    message = str(excinfo.value)
    assert "7" in message and OTHER_KEY not in message


def test_previous_rejects_non_text():
    with pytest.raises(ImproperlyConfigured, match=PREVIOUS_NAME):
        parse_previous_master_keys(2)


# --- Anillo ---


def test_ring_hides_keys_in_repr():
    ring = build_master_key_ring(raw_key=VALID_KEY, raw_previous=f"2:{OTHER_KEY}")
    assert VALID_KEY not in repr(ring) and OTHER_KEY not in repr(ring)


def test_ring_publishes_current_and_previous_versions():
    ring = build_master_key_ring(
        raw_key=VALID_KEY,
        raw_version=3,
        raw_previous=f"2:{OTHER_KEY}",
    )
    assert ring.current_version == 3
    assert ring.supported_versions == frozenset({2, 3})
    assert ring.key_for(3) == decode_master_key(VALID_KEY)
    assert ring.key_for(2) == decode_master_key(OTHER_KEY)


def test_ring_rejects_unknown_version_after_old_key_retired():
    ring = build_master_key_ring(raw_key=VALID_KEY, raw_version=3)
    with pytest.raises(UnsupportedMasterKeyVersion):
        ring.key_for(2)


def test_ring_requires_explicit_current_key():
    with pytest.raises(ImproperlyConfigured, match="no está configurada"):
        build_master_key_ring(raw_key="")


def test_ring_rejects_current_version_repeated_in_previous():
    with pytest.raises(ImproperlyConfigured, match=PREVIOUS_NAME):
        build_master_key_ring(
            raw_key=VALID_KEY, raw_version=2, raw_previous=f"2:{OTHER_KEY}"
        )


def test_ring_rejects_secret_key_reuse_without_echo():
    with pytest.raises(ImproperlyConfigured) as excinfo:
        build_master_key_ring(raw_key=VALID_KEY, secret_key=VALID_KEY)
    message = str(excinfo.value)
    assert "SECRET_KEY" in message and VALID_KEY not in message


# --- Arranque ---


def test_development_does_not_block_startup():
    validate_coordinator_master_key(
        raw_key="",
        raw_version="1",
        raw_previous="",
        debug=True,
        secret_key=SECRET_KEY_OTHER,
    )


@pytest.mark.parametrize("raw", ["", "no-es-base64", _encoded(16)])
def test_production_requires_valid_key_at_startup(raw):
    with pytest.raises(ImproperlyConfigured, match="obligatoria en producción"):
        validate_coordinator_master_key(
            raw_key=raw,
            raw_version="1",
            raw_previous="",
            debug=False,
            secret_key=SECRET_KEY_OTHER,
        )


def test_production_accepts_valid_key():
    validate_coordinator_master_key(
        raw_key=VALID_KEY,
        raw_version="1",
        raw_previous=f"2:{OTHER_KEY}",
        debug=False,
        secret_key=SECRET_KEY_OTHER,
    )


def test_load_reads_settings_and_fails_closed(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = ""
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    with pytest.raises(ImproperlyConfigured, match="no está configurada"):
        load_master_key_ring()


def test_load_builds_ring_from_settings(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = VALID_KEY
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = f"2:{OTHER_KEY}"
    ring = load_master_key_ring()
    assert ring == MasterKeyRing(
        current_version=1,
        keys={1: decode_master_key(VALID_KEY), 2: decode_master_key(OTHER_KEY)},
    )


# --- Configuración ---


def test_platform_reads_its_own_environment_variables():
    for name in (KEY_NAME, VERSION_NAME, PREVIOUS_NAME):
        assert isinstance(getattr(platform, name), str)
    assert platform.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION.isdigit()
