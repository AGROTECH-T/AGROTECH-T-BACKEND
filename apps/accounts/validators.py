"""Normaliza y valida datos sensibles de cuentas.

@author Cristian Deysdayr Jimenez
"""
import re

from rest_framework import serializers

ID_PATTERN = re.compile(r"^\d{5,15}$")
PHONE_PATTERN = re.compile(r"^\d{7,15}$")
WEAK_CREDENTIAL_ERROR = (
    "la contraseña debe tener 8 caracteres, una mayúscula, "
    "una minúscula, un número y un signo"
)


def only_digits(value: str) -> str:
    """Retorna únicamente los dígitos de un texto."""
    return "".join(character for character in value if character.isdigit())


def normalize_name(value: str, label: str) -> str:
    """Normaliza espacios y valida un nombre humano."""
    normalized = " ".join(value.split())
    only_letters = all(part.isalpha() for part in normalized.split())
    if not 2 <= len(normalized) <= 40 or not only_letters:
        raise serializers.ValidationError(f"el {label} debe tener solo letras")
    return normalized


def normalize_identification(value: str) -> str:
    """Normaliza y valida una identificación numérica."""
    normalized = only_digits(value)
    if not ID_PATTERN.fullmatch(normalized):
        raise serializers.ValidationError("la identificacion debe tener entre 5 y 15 numeros")
    return normalized


def normalize_phone(value: str) -> str:
    """Normaliza un teléfono y su prefijo colombiano."""
    normalized = only_digits(value)
    if normalized.startswith("57") and len(normalized) > 10:
        normalized = normalized[-10:]
    if normalized and not PHONE_PATTERN.fullmatch(normalized):
        raise serializers.ValidationError("el celular debe tener solo numeros")
    return normalized


def validate_password_strength(value: str) -> str:
    """Exige longitud y cuatro categorías de caracteres."""
    categories = (
        any(character.isupper() for character in value),
        any(character.islower() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    )
    if not 8 <= len(value) <= 72 or not all(categories):
        raise serializers.ValidationError(WEAK_CREDENTIAL_ERROR)
    return value
