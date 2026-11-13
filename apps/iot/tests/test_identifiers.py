# ruff: noqa: S101
"""Pruebas de los identificadores públicos IoT (IoT-02.1).

Propósito: verificar generación con ``secrets``, formato canónico, alfabeto,
validación estricta y normalización. No usan base de datos.
"""
import pytest

from apps.iot import identifiers
from apps.iot.identifiers import (
    IDENTIFIER_ALPHABET,
    IDENTIFIER_LENGTH,
    INVALID_IDENTIFIER_MESSAGE,
    IdentifierKind,
    InvalidIdentifierError,
    generate_identifier,
    is_valid_identifier,
    normalize_identifier,
)

KINDS = [
    (IdentifierKind.GATEWAY, "GW-"),
    (IdentifierKind.COORDINATOR, "CO-"),
    (IdentifierKind.SENSOR, "SN-"),
]
VALID_RANDOM_PART = "7K3M9QXT2A"


@pytest.mark.parametrize(("kind", "prefix"), KINDS)
def test_generation_per_kind(kind, prefix):
    value = generate_identifier(kind)
    assert value.startswith(prefix)
    assert len(value) == IDENTIFIER_LENGTH == 13
    assert all(char in IDENTIFIER_ALPHABET for char in value[3:])
    assert is_valid_identifier(value, kind)


@pytest.mark.parametrize(("kind", "prefix"), KINDS)
def test_prefix_property(kind, prefix):
    assert kind.prefix == prefix


def test_alphabet_has_no_ambiguous_or_lowercase_characters():
    assert len(IDENTIFIER_ALPHABET) == len(set(IDENTIFIER_ALPHABET)) == 31
    assert not set("0O1IL") & set(IDENTIFIER_ALPHABET)
    assert IDENTIFIER_ALPHABET == IDENTIFIER_ALPHABET.upper()


def test_generation_uses_secrets(monkeypatch):
    calls = []

    def fake_choice(sequence):
        calls.append(sequence)
        return "Z"

    monkeypatch.setattr(identifiers.secrets, "choice", fake_choice)
    assert generate_identifier(IdentifierKind.SENSOR) == "SN-ZZZZZZZZZZ"
    assert calls == [IDENTIFIER_ALPHABET] * 10


def test_module_does_not_use_random():
    assert "random" not in vars(identifiers)


def test_generated_identifiers_differ():
    values = {generate_identifier(IdentifierKind.GATEWAY) for _ in range(500)}
    assert len(values) == 500


def test_generation_rejects_unknown_kind():
    with pytest.raises(ValueError):
        generate_identifier("XX")


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        "GW-",
        "GW-7K3M9QXT2",  # 9 caracteres
        "GW-7K3M9QXT2AB",  # 11 caracteres
        "CO-7K3M9QXT2A",  # prefijo de otro tipo
        "GW_7K3M9QXT2A",  # separador incorrecto
        "GWX7K3M9QXT2A",
        "gw-7k3m9qxt2a",  # la validación estricta no normaliza
        "GW-7K3M9QXT0A",  # 0 ambiguo
        "GW-7K3M9QXTOA",  # O ambiguo
        "GW-7K3M9QXT1A",  # 1 ambiguo
        "GW-7K3M9QXTIA",  # I ambiguo
        "GW-7K3M9QXTLA",  # L ambiguo
        "GW-7K3M 9QXTA",  # espacio interno
        "GW-7K3M9QXT2Á",  # fuera del alfabeto
        " GW-7K3M9QXT2A",
    ],
)
def test_strict_validation_rejects_malformed(value):
    assert is_valid_identifier(value, IdentifierKind.GATEWAY) is False


@pytest.mark.parametrize(
    "value",
    [None, 123, "", "   ", "GW-7K3M9QXT2", "CO-7K3M9QXT2A", "GW-7K3M9QXT0A", "GW-7K3M 9QXTA"],
)
def test_normalization_rejects_malformed_without_echo(value):
    with pytest.raises(InvalidIdentifierError) as excinfo:
        normalize_identifier(value, IdentifierKind.GATEWAY)
    assert str(excinfo.value) == INVALID_IDENTIFIER_MESSAGE


@pytest.mark.parametrize(("kind", "prefix"), KINDS)
def test_normalization_to_canonical_form(kind, prefix):
    raw = f"  {prefix.lower()}{VALID_RANDOM_PART.lower()}\t"
    assert normalize_identifier(raw, kind) == prefix + VALID_RANDOM_PART


def test_normalization_is_idempotent():
    canonical = generate_identifier(IdentifierKind.COORDINATOR)
    assert normalize_identifier(canonical, IdentifierKind.COORDINATOR) == canonical


def test_invalid_identifier_error_is_value_error():
    assert issubclass(InvalidIdentifierError, ValueError)
