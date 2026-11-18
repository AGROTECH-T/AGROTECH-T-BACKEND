"""Vectores binarios de la trama LoRa v1 para pruebas (IoT-04.2).

Propósito: construir tramas con ``struct.pack`` y ``hmac`` de la biblioteca
estándar, sin usar ``apps.iot.lora_frame`` ni sus estructuras, para que las
pruebas no validen el parser con el mismo código que lo implementa.

El vector de referencia (67 bytes) se fija además en hexadecimal literal:
uid ``CO-7K3M9QXT2A``, ``key_version`` 1, ``boot_counter`` 7, ``seq`` 120,
canal 1 temperatura 2534 (25,34 °C) y canal 2 humedad 6325 (63,25 %). El tag se
calculó con ``HMAC-SHA256(bytes(range(32)), b"agrotech:lora-frame:v1" ||
signed_bytes)``.
"""
from __future__ import annotations

import hashlib
import hmac
import struct

UID = "CO-7K3M9QXT2A"
KEY = bytes(range(32))
CONTEXT = b"agrotech:lora-frame:v1"
READINGS = ((1, 1, 2534), (2, 2, 6325))

REFERENCE_SIGNED_HEX = (
    "01"  # frame_version
    "434f2d374b334d395158543241"  # "CO-7K3M9QXT2A"
    "0001"  # key_version
    "00000007"  # boot_counter
    "00000078"  # seq
    "02"  # reading_count
    "00010109e6"  # canal 1, temperatura, 2534
    "00020218b5"  # canal 2, humedad, 6325
)
REFERENCE_TAG_HEX = "ddbab481e3b4f71dcc93452b1d0943ad24163c59d6a3c2e6f27ad3eb6bab1af5"
REFERENCE_SIGNED = bytes.fromhex(REFERENCE_SIGNED_HEX)
REFERENCE_TAG = bytes.fromhex(REFERENCE_TAG_HEX)
REFERENCE_FRAME = REFERENCE_SIGNED + REFERENCE_TAG


def reading(channel: int, metric_code: int, value: int) -> bytes:
    """Bloque de 5 bytes; ``value`` con signo solo para la temperatura (1)."""
    value_format = ">h" if metric_code == 1 else ">H"
    return struct.pack(">HB", channel, metric_code) + struct.pack(value_format, value)


def signed_bytes(
    *,
    uid: str | bytes = UID,
    key_version: int = 1,
    boot_counter: int = 7,
    seq: int = 120,
    readings=READINGS,
    version: int = 1,
    reading_count: int | None = None,
) -> bytes:
    """Cabecera y lecturas; ``reading_count`` permite forzar incoherencias."""
    uid_bytes = uid if isinstance(uid, bytes) else uid.encode("ascii")
    blocks = b"".join(reading(*item) for item in readings)
    count = len(readings) if reading_count is None else reading_count
    return (
        struct.pack(">B", version)
        + uid_bytes
        + struct.pack(">HIIB", key_version, boot_counter, seq, count)
        + blocks
    )


def tag_for(signed: bytes, *, key: bytes = KEY, context: bytes = CONTEXT) -> bytes:
    """HMAC-SHA256 completo sobre ``context || signed``."""
    return hmac.new(key, context + signed, hashlib.sha256).digest()


def frame(*, key: bytes = KEY, context: bytes = CONTEXT, **fields) -> bytes:
    """Trama completa: ``signed_bytes`` seguidos de su tag."""
    signed = signed_bytes(**fields)
    return signed + tag_for(signed, key=key, context=context)
