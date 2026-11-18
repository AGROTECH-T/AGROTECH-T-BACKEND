# ruff: noqa: S101, S105, S106
"""Primitivas criptográficas de credenciales de coordinador (IoT-03.2, sin BD).

Propósito: formato de clave y cifrado AES-256-GCM con AAD ligado al coordinador
y a la versión maestra, HMAC-SHA256 de trama con tag truncable y validación de
la trama interpretada. Ningún ``repr`` o mensaje expone material secreto.
"""
import secrets

import pytest

from apps.iot import coordinator_credentials as crypto
from apps.iot.identifiers import IdentifierKind, generate_identifier
from apps.iot.tests import lora_vectors
from config.coordinator_key_encryption_key import MasterKeyRing

UID = "CO-7K3M9QXT2A"
OTHER_UID = generate_identifier(IdentifierKind.COORDINATOR)
CONTEXT = b"contexto-de-prueba"


def _ring(*versions: int, current: int | None = None) -> MasterKeyRing:
    keys = {version: secrets.token_bytes(32) for version in versions}
    return MasterKeyRing(current_version=current or max(keys), keys=keys)


@pytest.fixture
def ring():
    return _ring(1)


@pytest.fixture
def key():
    return crypto.generate_coordinator_key()


# --- Clave del coordinador ---


def test_generated_key_is_32_random_bytes():
    first, second = crypto.generate_coordinator_key(), crypto.generate_coordinator_key()
    assert len(first) == crypto.COORDINATOR_KEY_BYTES == 32
    assert first != second


@pytest.mark.parametrize("raw", [None, "", "x" * 32, b"", b"x" * 31, b"x" * 33, 42])
def test_validate_key_rejects_wrong_shape(raw):
    with pytest.raises(crypto.InvalidCoordinatorKey):
        crypto.validate_coordinator_key(raw)


def test_validate_key_accepts_bytes_and_bytearray():
    key = crypto.generate_coordinator_key()
    assert crypto.validate_coordinator_key(key) == key
    assert crypto.validate_coordinator_key(bytearray(key)) == key


# --- AAD ---


def test_aad_binds_context_uid_and_version():
    assert crypto.build_coordinator_aad(UID, 3) == b"agrotech:co-cred:v1:CO-7K3M9QXT2A:3"


@pytest.mark.parametrize("uid", ["", "GW-7K3M9QXT2A", "CO-123", None])
def test_aad_rejects_invalid_uid(uid):
    with pytest.raises(crypto.InvalidCoordinatorIdentifier):
        crypto.build_coordinator_aad(uid, 1)


@pytest.mark.parametrize("version", [0, -1, True, "1", None])
def test_aad_rejects_invalid_version(version):
    with pytest.raises(crypto.InvalidEncryptedCredential):
        crypto.build_coordinator_aad(UID, version)


# --- Cifrado ---


def test_issue_encryption_produces_48_byte_ciphertext_and_12_byte_nonce(ring, key):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    assert len(encrypted.ciphertext) == crypto.CIPHERTEXT_BYTES == 48
    assert len(encrypted.nonce) == crypto.NONCE_BYTES == 12
    assert encrypted.master_key_version == 1
    assert repr(encrypted) == "EncryptedCoordinatorKey(master_key_version=1)"


def test_roundtrip_recovers_the_key(ring, key):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    recovered = crypto.decrypt_coordinator_key(
        encrypted.ciphertext,
        encrypted.nonce,
        coordinator_uid=UID,
        key_ring=ring,
        master_key_version=1,
    )
    assert recovered == key


def test_each_encryption_uses_a_fresh_nonce(ring, key):
    first = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    second = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


def test_ciphertext_is_bound_to_the_coordinator(ring, key):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    with pytest.raises(crypto.CoordinatorCredentialUnreadable):
        crypto.decrypt_coordinator_key(
            encrypted.ciphertext,
            encrypted.nonce,
            coordinator_uid=OTHER_UID,
            key_ring=ring,
            master_key_version=1,
        )


def test_ciphertext_is_bound_to_the_master_version():
    ring = _ring(1, 2, current=2)
    encrypted = crypto.encrypt_coordinator_key(
        crypto.generate_coordinator_key(),
        coordinator_uid=UID,
        key_ring=ring,
        master_key_version=1,
    )
    with pytest.raises(crypto.CoordinatorCredentialUnreadable):
        crypto.decrypt_coordinator_key(
            encrypted.ciphertext,
            encrypted.nonce,
            coordinator_uid=UID,
            key_ring=ring,
            master_key_version=2,
        )


def test_encrypt_with_unsupported_version_fails_closed(ring, key):
    with pytest.raises(crypto.UnsupportedMasterKeyVersion):
        crypto.encrypt_coordinator_key(
            key, coordinator_uid=UID, key_ring=ring, master_key_version=9
        )


def test_decrypt_with_unsupported_version_fails_closed(ring, key):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    with pytest.raises(crypto.UnsupportedMasterKeyVersion):
        crypto.decrypt_coordinator_key(
            encrypted.ciphertext,
            encrypted.nonce,
            coordinator_uid=UID,
            key_ring=ring,
            master_key_version=9,
        )


@pytest.mark.parametrize("field_name", ["ciphertext", "nonce", "both"])
def test_tampering_is_rejected(ring, key, field_name):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    ciphertext, nonce = bytearray(encrypted.ciphertext), bytearray(encrypted.nonce)
    if field_name in ("ciphertext", "both"):
        ciphertext[0] ^= 0x01
    if field_name in ("nonce", "both"):
        nonce[0] ^= 0x01
    with pytest.raises(crypto.CoordinatorCredentialUnreadable):
        crypto.decrypt_coordinator_key(
            bytes(ciphertext),
            bytes(nonce),
            coordinator_uid=UID,
            key_ring=ring,
            master_key_version=1,
        )


@pytest.mark.parametrize(
    ("ciphertext", "nonce"),
    [(b"", b"x" * 12), (b"x" * 48, b""), (b"x" * 47, b"x" * 12), (b"x" * 48, b"x" * 11)],
)
def test_decrypt_rejects_invalid_lengths(ring, ciphertext, nonce):
    with pytest.raises(crypto.InvalidEncryptedCredential):
        crypto.decrypt_coordinator_key(
            ciphertext,
            nonce,
            coordinator_uid=UID,
            key_ring=ring,
            master_key_version=1,
        )


def test_encrypted_credential_repr_hides_material(ring, key):
    encrypted = crypto.encrypt_coordinator_key(key, coordinator_uid=UID, key_ring=ring)
    assert repr(encrypted) == "EncryptedCoordinatorKey(master_key_version=1)"


# --- HMAC de trama ---


def test_hmac_is_sha256_and_verifies(key):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert len(tag) == crypto.LORA_TAG_BYTES == 32
    assert crypto.verify_lora_hmac(key, b"trama", tag, context=CONTEXT)


# --- H-01 (IoT-03.2-R3): tag de longitud fija, HMAC completo ---


@pytest.mark.parametrize("length", [1, 8, 16, 31])
def test_verify_rejects_truncated_prefix_of_the_valid_tag(key, length):
    """Un prefijo auténtico del HMAC no autentica: la longitud no la elige el emisor."""
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", tag[:length], context=CONTEXT)


def test_verify_rejects_33_byte_tag(key):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", tag + tag[:1], context=CONTEXT)


def test_verify_accepts_exactly_the_full_32_byte_tag(key):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert len(tag) == 32
    assert crypto.verify_lora_hmac(key, b"trama", tag, context=CONTEXT)
    assert crypto.verify_lora_hmac(key, b"trama", bytearray(tag), context=CONTEXT)


@pytest.mark.parametrize("position", [0, 15, 31])
def test_verify_rejects_wrong_32_byte_tag(key, position):
    tag = bytearray(crypto.compute_lora_hmac(key, b"trama", context=CONTEXT))
    tag[position] ^= 0x01
    assert not crypto.verify_lora_hmac(key, b"trama", bytes(tag), context=CONTEXT)


def test_verify_compares_the_full_hmac_with_compare_digest(key, monkeypatch):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    calls = []
    original = crypto.hmac.compare_digest

    def spy(a, b):
        calls.append((bytes(a), bytes(b)))
        return original(a, b)

    monkeypatch.setattr(crypto.hmac, "compare_digest", spy)
    assert crypto.verify_lora_hmac(key, b"trama", tag, context=CONTEXT)
    assert calls == [(tag, tag)]
    calls.clear()
    assert not crypto.verify_lora_hmac(key, b"trama", tag[:16], context=CONTEXT)
    assert calls == []  # la longitud incorrecta se rechaza sin recortar el esperado


def test_tag_length_is_a_backend_constant():
    import inspect

    source = inspect.getsource(crypto.verify_lora_hmac)
    assert "LORA_TAG_BYTES" in source
    assert "expected[:" not in source
    assert not hasattr(crypto, "MAX_TAG_BYTES")


def test_verify_rejects_empty_or_oversized_tag(key):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", b"", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", tag + b"\x00", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", "no-bytes", context=CONTEXT)


def test_verify_rejects_tampered_payload_or_context(key):
    tag = crypto.compute_lora_hmac(key, b"trama", context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"otra", tag, context=CONTEXT)
    assert not crypto.verify_lora_hmac(key, b"trama", tag, context=b"otro")


def test_hmac_requires_an_explicit_context(key):
    with pytest.raises(crypto.MissingHmacContext):
        crypto.compute_lora_hmac(key, b"trama", context=None)
    with pytest.raises(crypto.MissingHmacContext):
        crypto.compute_lora_hmac(key, b"trama", context=b"")


def test_hmac_rejects_an_invalid_key():
    with pytest.raises(crypto.InvalidCoordinatorKey):
        crypto.compute_lora_hmac(b"corta", b"trama", context=CONTEXT)


# --- Trama interpretada ---


def _frame(**overrides) -> crypto.ParsedCoordinatorFrame:
    # signed_bytes v1 reales, coherentes con los campos (M-02, IoT-04.2).
    values = {
        "coordinator_uid": UID,
        "key_version": 1,
        "boot_counter": 2,
        "seq": 7,
        "signed_bytes": lora_vectors.signed_bytes(uid=UID, boot_counter=2, seq=7),
        "tag": b"t" * 32,
    }
    values.update(overrides)
    return crypto.ParsedCoordinatorFrame(**values)


def test_valid_frame_is_returned_as_is():
    frame = _frame()
    assert crypto.validate_coordinator_frame(frame) is frame


@pytest.mark.parametrize(
    "overrides",
    [
        {"coordinator_uid": "nope"},
        {"key_version": 0},
        {"key_version": True},
        {"boot_counter": -1},
        {"seq": "7"},
        {"signed_bytes": b""},
        {"signed_bytes": "texto"},
        {"tag": b""},
        {"tag": b"t"},
        {"tag": b"t" * 8},
        {"tag": b"t" * 16},
        {"tag": b"t" * 31},
        {"tag": b"t" * 33},
        {"tag": None},
        {"tag": "t" * 32},
    ],
)
def test_invalid_frame_shape_is_rejected(overrides):
    with pytest.raises(crypto.InvalidLoraTag):
        crypto.validate_coordinator_frame(_frame(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"coordinator_uid": OTHER_UID},
        {"key_version": 2},
        {"boot_counter": 3},
        {"seq": 8},
        {"signed_bytes": lora_vectors.signed_bytes(uid=UID, boot_counter=2, seq=8)},
        {"signed_bytes": lora_vectors.signed_bytes(uid=OTHER_UID, boot_counter=2, seq=7)},
        {"signed_bytes": b"trama"},
        {"signed_bytes": lora_vectors.signed_bytes(uid=UID, boot_counter=2, seq=7) + b"x"},
    ],
)
def test_frame_fields_contradicting_signed_bytes_are_rejected(overrides):
    """M-02: los campos declarados deben salir de los mismos bytes firmados."""
    with pytest.raises(crypto.InvalidLoraTag):
        crypto.validate_coordinator_frame(_frame(**overrides))


def test_non_frame_object_is_rejected():
    with pytest.raises(crypto.InvalidLoraTag):
        crypto.validate_coordinator_frame({"coordinator_uid": UID})


def test_frame_repr_hides_signed_material():
    frame = _frame(signed_bytes=b"secreto-firmado")
    text = repr(frame)
    assert "secreto-firmado" not in text and "tag" not in text


def test_master_key_version_supported(ring):
    assert crypto.master_key_version_supported(ring, 1)
    assert not crypto.master_key_version_supported(ring, 2)