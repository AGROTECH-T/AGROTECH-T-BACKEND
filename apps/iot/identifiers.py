"""Identificadores públicos de dispositivos IoT (IoT-02.1, DEC-13).

Propósito: generar, normalizar y validar los identificadores ``GW-``, ``CO-`` y
``SN-`` de gateways, coordinadores y sensores.

Contexto: la clave primaria interna será ``BigAutoField`` (convención del
proyecto). El identificador público es el que se muestra, se usa como usuario
MQTT del gateway y viaja en las tramas del coordinador; por eso es aleatorio y
no revela cuántos registros existen.

Formato canónico: prefijo de 3 caracteres (``GW-``, ``CO-``, ``SN-``) seguido de
10 caracteres de ``IDENTIFIER_ALPHABET``, en mayúsculas. Ejemplo: ``GW-7K3M9QXT2A``.

Alfabeto: es el mismo conjunto de 31 caracteres de
``apps.farms.invitation_codes.CODE_ALPHABET`` (sin ``0 O 1 I L``, ambiguos al
leerlos o copiarlos). Se declara aquí como constante propia en lugar de
importarla porque este formato se guardará de forma permanente: un cambio
futuro en los códigos de invitación no debe invalidar identificadores ya
emitidos.

Unicidad: este módulo no consulta la base. La unicidad definitiva la
garantizará una restricción ``UNIQUE`` en MariaDB; ante una colisión (muy
improbable: 31**10 ≈ 8.2e14 combinaciones por tipo), la capa de persistencia
generará un identificador nuevo y reintentará como máximo
``MAX_GENERATION_ATTEMPTS`` veces. Un identificador nunca se reasigna: los
dispositivos se desactivan, no se borran.
"""
from __future__ import annotations

import secrets
from enum import StrEnum

IDENTIFIER_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RANDOM_PART_LENGTH = 10
SEPARATOR = "-"
PREFIX_LENGTH = 3
IDENTIFIER_LENGTH = PREFIX_LENGTH + RANDOM_PART_LENGTH
MAX_GENERATION_ATTEMPTS = 5

_ALLOWED = frozenset(IDENTIFIER_ALPHABET)
INVALID_IDENTIFIER_MESSAGE = "identificador de dispositivo no válido"


class IdentifierKind(StrEnum):
    """Tipo de entidad y su prefijo (sin separador)."""

    GATEWAY = "GW"
    COORDINATOR = "CO"
    SENSOR = "SN"

    @property
    def prefix(self) -> str:
        """Prefijo completo, con separador: ``GW-``, ``CO-`` o ``SN-``."""
        return f"{self.value}{SEPARATOR}"


class InvalidIdentifierError(ValueError):
    """El identificador no cumple el formato. El mensaje no repite el valor."""


def generate_identifier(kind: IdentifierKind) -> str:
    """Genera un identificador canónico aleatorio con ``secrets``."""
    kind = IdentifierKind(kind)
    random_part = "".join(
        secrets.choice(IDENTIFIER_ALPHABET) for _ in range(RANDOM_PART_LENGTH)
    )
    return kind.prefix + random_part


def is_valid_identifier(value, kind: IdentifierKind) -> bool:
    """Valida de forma estricta la forma canónica, sin normalizar."""
    kind = IdentifierKind(kind)
    if not isinstance(value, str) or len(value) != IDENTIFIER_LENGTH:
        return False
    if not value.startswith(kind.prefix):
        return False
    return all(char in _ALLOWED for char in value[PREFIX_LENGTH:])


def normalize_identifier(value, kind: IdentifierKind) -> str:
    """Convierte una entrada de usuario a la forma canónica o la rechaza.

    Solo elimina espacios en los extremos y pasa a mayúsculas; no corrige
    caracteres ambiguos ni agrega el prefijo. Lanza ``InvalidIdentifierError``.
    """
    if not isinstance(value, str):
        raise InvalidIdentifierError(INVALID_IDENTIFIER_MESSAGE)
    candidate = value.strip().upper()
    if not is_valid_identifier(candidate, kind):
        raise InvalidIdentifierError(INVALID_IDENTIFIER_MESSAGE)
    return candidate
