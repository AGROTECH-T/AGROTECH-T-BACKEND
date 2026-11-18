# ruff: noqa: S101, S105, S106
"""Pruebas puras del contrato MQTT (sin base de datos)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.iot.mqtt import payload as contract
from apps.iot.mqtt.payload import (
    MAX_MESSAGE_BYTES,
    PayloadError,
    PayloadRejection,
    TopicError,
    parse_payload,
    parse_topic,
)
from apps.iot.tests import lora_vectors

GATEWAY_UID = "GW-7K3M9QXT2A"


def envelope(frame: bytes | str | int | None = None, **overrides) -> bytes:
    if isinstance(frame, bytes):
        encoded = base64.b64encode(frame).decode("ascii")
    elif frame is None:
        encoded = base64.b64encode(lora_vectors.REFERENCE_FRAME).decode("ascii")
    else:
        encoded = frame
    data = {
        "v": 1,
        "frame": encoded,
        "lora_rx_at": "2026-11-13T12:34:56.789+00:00",
        "clock_synced": True,
        "buffered": False,
        "rssi": -87,
        "snr": 7.5,
    }
    data.update(overrides)
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def assert_reason(raw: bytes, reason: PayloadRejection) -> None:
    with pytest.raises(PayloadError) as excinfo:
        parse_payload(raw)
    assert excinfo.value.reason is reason


def test_valid_payload_is_normalized() -> None:
    parsed = parse_payload(envelope())

    assert parsed.frame == lora_vectors.REFERENCE_FRAME
    assert parsed.lora_rx_at == datetime(2026, 11, 13, 12, 34, 56, 789000, tzinfo=UTC)
    assert parsed.clock_synced is True
    assert parsed.buffered is False
    assert parsed.rssi == -87
    assert parsed.snr == Decimal("7.50")


def test_offset_timestamp_is_normalized_to_utc() -> None:
    parsed = parse_payload(envelope(lora_rx_at="2026-11-13T14:34:56+02:00"))

    assert parsed.lora_rx_at == datetime(2026, 11, 13, 12, 34, 56, tzinfo=UTC)


def test_optional_gateway_fields_can_be_omitted() -> None:
    raw = envelope()
    document = json.loads(raw)
    del document["rssi"]
    del document["snr"]

    parsed = parse_payload(json.dumps(document).encode("utf-8"))

    assert parsed.rssi is None
    assert parsed.snr is None


def test_explicit_nulls_are_accepted() -> None:
    parsed = parse_payload(envelope(lora_rx_at=None, rssi=None, snr=None))

    assert parsed.lora_rx_at is None
    assert parsed.rssi is None
    assert parsed.snr is None


def test_bytearray_is_accepted() -> None:
    parsed = parse_payload(bytearray(envelope()))

    assert parsed.frame == lora_vectors.REFERENCE_FRAME


def test_snr_is_quantized_to_two_decimals() -> None:
    parsed = parse_payload(envelope(snr=7.555))

    assert parsed.snr == Decimal("7.56")


@pytest.mark.parametrize("raw", ["texto", None, 1, object()])
def test_rejects_non_bytes(raw) -> None:
    with pytest.raises(PayloadError) as excinfo:
        parse_payload(raw)
    assert excinfo.value.reason is PayloadRejection.INVALID_TYPE


def test_rejects_empty_payload() -> None:
    assert_reason(b"", PayloadRejection.TOO_LARGE)


def test_rejects_oversized_payload() -> None:
    assert_reason(b" " * (MAX_MESSAGE_BYTES + 1), PayloadRejection.TOO_LARGE)


def test_rejects_invalid_utf8() -> None:
    assert_reason(b"\xff", PayloadRejection.INVALID_UTF8)


@pytest.mark.parametrize("raw", [b"{", b"", b"not-json", b'{"v":1,}'])
def test_rejects_invalid_json(raw: bytes) -> None:
    if not raw:
        assert_reason(raw, PayloadRejection.TOO_LARGE)
        return
    assert_reason(raw, PayloadRejection.INVALID_JSON)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"1", b'"x"'])
def test_rejects_non_object(raw: bytes) -> None:
    assert_reason(raw, PayloadRejection.NOT_OBJECT)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_rejects_non_finite_json_numbers(constant: bytes) -> None:
    raw = envelope().replace(b'"snr":7.5', b'"snr":' + constant)

    assert_reason(raw, PayloadRejection.INVALID_JSON)


def test_rejects_duplicate_fields() -> None:
    raw = b'{"v":1,"v":1,"frame":"","lora_rx_at":null,"clock_synced":true,"buffered":false}'

    assert_reason(raw, PayloadRejection.DUPLICATE_FIELD)


@pytest.mark.parametrize("field", ["farm_id", "gateway_uid", "extra"])
def test_rejects_unknown_fields(field: str) -> None:
    document = json.loads(envelope())
    document[field] = 1

    assert_reason(json.dumps(document).encode("utf-8"), PayloadRejection.UNKNOWN_FIELD)


@pytest.mark.parametrize("field", ["v", "frame", "lora_rx_at", "clock_synced", "buffered"])
def test_rejects_missing_fields(field: str) -> None:
    document = json.loads(envelope())
    del document[field]

    assert_reason(json.dumps(document).encode("utf-8"), PayloadRejection.MISSING_FIELD)


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0, None])
def test_rejects_invalid_version(version) -> None:
    assert_reason(envelope(v=version), PayloadRejection.INVALID_VERSION)


@pytest.mark.parametrize(
    "frame_value",
    [
        "no-base64!",
        "",
        base64.b64encode(b"\x01" * 61).decode("ascii"),
        base64.b64encode(b"\x01" * 138).decode("ascii"),
        base64.b64encode(lora_vectors.REFERENCE_FRAME).decode("ascii").rstrip("="),
        123,
        "ñ",
    ],
)
def test_rejects_invalid_frame(frame_value) -> None:
    assert_reason(envelope(frame=frame_value), PayloadRejection.INVALID_FRAME)


@pytest.mark.parametrize(
    "value",
    ["2026-11-13", "2026-11-13T12:00:00", "no-es-fecha", 123, ""],
)
def test_rejects_invalid_timestamp(value) -> None:
    assert_reason(envelope(lora_rx_at=value), PayloadRejection.INVALID_TIMESTAMP)


@pytest.mark.parametrize("field", ["clock_synced", "buffered"])
@pytest.mark.parametrize("value", [1, 0, "true", None, []])
def test_rejects_non_boolean_flags(field: str, value) -> None:
    assert_reason(envelope(**{field: value}), PayloadRejection.INVALID_BOOLEAN)


@pytest.mark.parametrize("value", [-165, 1, True, 1.5, "-80", []])
def test_rejects_invalid_rssi(value) -> None:
    assert_reason(envelope(rssi=value), PayloadRejection.INVALID_RSSI)


@pytest.mark.parametrize("value", [20.01, -20.01, True, "7.5", []])
def test_rejects_invalid_snr(value) -> None:
    assert_reason(envelope(snr=value), PayloadRejection.INVALID_SNR)


def test_limits_are_derived_from_the_protocol() -> None:
    assert contract.MAX_MESSAGE_BYTES >= 4 * ((137 + 2) // 3) + contract.ENVELOPE_BUDGET_BYTES


def test_valid_topic_returns_gateway_uid() -> None:
    assert parse_topic(f"agrotech/v1/gw/{GATEWAY_UID}/up") == GATEWAY_UID


@pytest.mark.parametrize(
    "topic",
    [
        None,
        123,
        "",
        f"agrotech/v1/gw/{GATEWAY_UID}",
        f"agrotech/v1/gw/{GATEWAY_UID}/up/extra",
        f"agrotech/v2/gw/{GATEWAY_UID}/up",
        f"agrotech/v1/co/{GATEWAY_UID}/up",
        "agrotech/v1/gw/+/up",
        "agrotech/v1/gw/#/up",
        "agrotech/v1/gw/CO-7K3M9QXT2A/up",
        "AGROTECH/v1/gw/GW-7K3M9QXT2A/up",
        "agrotech/v1/gw/GW-ILO0AAAAAA/up",
        f"agrotech/v1/gw/{GATEWAY_UID}/UP",
        "agrotech/v1/gw/ñ/up",
    ],
)
def test_invalid_topics_are_rejected(topic) -> None:
    with pytest.raises(TopicError):
        parse_topic(topic)
