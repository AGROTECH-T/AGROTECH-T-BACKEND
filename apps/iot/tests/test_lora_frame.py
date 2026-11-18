# ruff: noqa: S101, S311
"""Parser binario de la trama LoRa v1 (IoT-04.2, sin BD).

Propósito: vector de referencia independiente (``lora_vectors``), longitud,
versión, cabecera, uid, lecturas, canales, métricas, centinelas, inmutabilidad,
ausencia de datos sensibles en ``repr`` y errores, contexto HMAC exacto y
protección M-02 (cada campo sale de los mismos bytes que firma el HMAC).
"""
import dataclasses
import random
import struct

import pytest

from apps.iot import coordinator_credentials as crypto
from apps.iot import lora_frame as lf
from apps.iot.catalog import MetricCode
from apps.iot.lora_frame import LoraFrameError, LoraFrameRejection, parse_lora_frame
from apps.iot.tests import lora_vectors as vec

R = LoraFrameRejection
OTHER_UID = "CO-ZZZZZZZZZZ"


def _rejects(raw, reason):
    with pytest.raises(LoraFrameError) as excinfo:
        parse_lora_frame(raw)
    assert excinfo.value.reason is reason
    return excinfo.value


def _with_readings(readings, **fields):
    return vec.frame(readings=readings, **fields)


# --- Vector de referencia ---


def test_reference_vector_matches_its_literal_hex():
    assert vec.signed_bytes() == vec.REFERENCE_SIGNED
    assert vec.tag_for(vec.REFERENCE_SIGNED) == vec.REFERENCE_TAG
    assert len(vec.REFERENCE_FRAME) == 67


def test_reference_vector_is_parsed_field_by_field():
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    assert frame.frame_version == 1
    assert frame.coordinator_uid == "CO-7K3M9QXT2A"
    assert frame.key_version == 1
    assert frame.boot_counter == 7
    assert frame.seq == 120
    assert frame.readings == (
        lf.LoraReading(channel=1, metric=MetricCode.TEMPERATURE, raw_value=2534),
        lf.LoraReading(channel=2, metric=MetricCode.RELATIVE_HUMIDITY, raw_value=6325),
    )
    assert frame.signed_bytes == vec.REFERENCE_SIGNED
    assert frame.tag == vec.REFERENCE_TAG
    assert frame.signed_bytes + frame.tag == vec.REFERENCE_FRAME


def test_reference_tag_uses_the_exact_v1_context():
    assert lf.LORA_FRAME_HMAC_CONTEXT == b"agrotech:lora-frame:v1"
    assert len(lf.LORA_FRAME_HMAC_CONTEXT) == 22
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    assert (
        crypto.compute_lora_hmac(
            vec.KEY, frame.signed_bytes, context=lf.LORA_FRAME_HMAC_CONTEXT
        )
        == vec.REFERENCE_TAG
    )
    assert crypto.verify_lora_hmac(
        vec.KEY, frame.signed_bytes, frame.tag, context=lf.LORA_FRAME_HMAC_CONTEXT
    )


@pytest.mark.parametrize(
    "context",
    [
        b"agrotech:co-cred:v1",
        b"agrotech:gw-cred:v1",
        b"agrotech:lora-frame:v2",
        b"agrotech:lora-frame",
        b"agrotechlora-frame:v1",
        b"AGROTECH:LORA-FRAME:V1",
    ],
)
def test_reference_tag_does_not_verify_with_another_context(context):
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    assert not crypto.verify_lora_hmac(vec.KEY, frame.signed_bytes, frame.tag, context=context)


def test_tag_keeps_all_32_bytes():
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    assert len(frame.tag) == lf.TAG_BYTES == crypto.LORA_TAG_BYTES == 32
    assert frame.tag == vec.REFERENCE_FRAME[-32:]
    assert frame.signed_bytes == vec.REFERENCE_FRAME[:-32]


def test_wire_metrics_match_the_catalog():
    assert lf.METRIC_TEMPERATURE == MetricCode.TEMPERATURE
    assert lf.METRIC_RELATIVE_HUMIDITY == MetricCode.RELATIVE_HUMIDITY
    assert {r.metric for r in parse_lora_frame(vec.REFERENCE_FRAME).readings} <= set(
        MetricCode.values
    )


# --- Tipo de entrada ---


def test_bytearray_is_copied_to_immutable_bytes():
    buffer = bytearray(vec.REFERENCE_FRAME)
    frame = parse_lora_frame(buffer)
    buffer[1:4] = b"XX-"
    buffer[-1] ^= 0xFF
    assert type(frame.signed_bytes) is bytes and type(frame.tag) is bytes
    assert frame.signed_bytes + frame.tag == vec.REFERENCE_FRAME
    assert frame.coordinator_uid == vec.UID


@pytest.mark.parametrize(
    "raw",
    [
        vec.REFERENCE_FRAME.hex(),
        vec.REFERENCE_FRAME.decode("latin-1"),
        None,
        memoryview(vec.REFERENCE_FRAME),
        list(vec.REFERENCE_FRAME),
        67,
    ],
)
def test_non_bytes_input_is_rejected(raw):
    _rejects(raw, R.INVALID_TYPE)


def test_empty_input_is_rejected():
    _rejects(b"", R.EMPTY)
    _rejects(bytearray(), R.EMPTY)


# --- Versión ---


@pytest.mark.parametrize("version", [0, 2, 3, 255])
def test_unknown_version_is_rejected(version):
    _rejects(vec.frame(version=version), R.UNSUPPORTED_VERSION)


def test_version_is_checked_on_a_single_byte():
    _rejects(b"\x02", R.UNSUPPORTED_VERSION)


# --- Longitud ---


@pytest.mark.parametrize("length", [1, 2, 13, 24])
def test_truncated_header_is_rejected(length):
    _rejects(vec.REFERENCE_FRAME[:length], R.TRUNCATED)


@pytest.mark.parametrize("length", [25, 30, 35, 61, 62, 66])
def test_truncated_body_or_tag_is_rejected(length):
    _rejects(vec.REFERENCE_FRAME[:length], R.LENGTH_MISMATCH)


@pytest.mark.parametrize("extra", [b"\x00", b"\x00" * 31, b"\x00" * 32, b"\xff" * 70])
def test_trailing_bytes_are_rejected(extra):
    _rejects(vec.REFERENCE_FRAME + extra, R.LENGTH_MISMATCH)


@pytest.mark.parametrize("tag_length", [0, 1, 16, 31, 33, 64])
def test_tag_of_any_other_length_is_rejected(tag_length):
    _rejects(vec.REFERENCE_SIGNED + b"t" * tag_length, R.LENGTH_MISMATCH)


def test_minimum_and_maximum_frames_are_accepted():
    minimum = _with_readings(((1, 1, 0),))
    maximum = _with_readings(tuple((c, 2, c) for c in range(1, 17)))
    assert len(minimum) == lf.MIN_FRAME_BYTES_V1 == 62
    assert len(maximum) == lf.MAX_FRAME_BYTES_V1 == 137
    assert len(parse_lora_frame(minimum).readings) == 1
    assert len(parse_lora_frame(maximum).readings) == 16
    _rejects(minimum[:-1], R.LENGTH_MISMATCH)
    _rejects(maximum + b"\x00", R.LENGTH_MISMATCH)


@pytest.mark.parametrize("count", [1, 2, 4, 16])
def test_length_follows_the_formula(count):
    raw = _with_readings(tuple((c, 1, c) for c in range(1, count + 1)))
    assert len(raw) == 25 + 5 * count + 32
    assert len(parse_lora_frame(raw).readings) == count


def test_reading_count_disagreeing_with_length_is_rejected():
    _rejects(vec.frame(reading_count=1), R.LENGTH_MISMATCH)
    _rejects(vec.frame(reading_count=3), R.LENGTH_MISMATCH)


# --- reading_count ---


@pytest.mark.parametrize("count", [0, 17, 18, 51, 255])
def test_reading_count_out_of_range_is_rejected_even_with_matching_length(count):
    readings = tuple((c, 1, 0) for c in range(1, count + 1))
    _rejects(_with_readings(readings, reading_count=count), R.INVALID_READING_COUNT)


# --- uid ---


@pytest.mark.parametrize(
    "uid",
    [
        b"co-7K3M9QXT2A",
        b"CO-7k3M9QXT2A",
        b"CO-7K3M9QXT2O",
        b"CO-7K3M9QXT20",
        b"CO-7K3M9QXT2I",
        b"CO-7K3M9QXT2L",
        b"CO-7K3M9QXT21",
        b"CO-7K3M9QXT2\x00",
        b"CO-7K3M9QXT2\x80",
        b"CO-7K3M9QXT2\xff",
        b"GW-7K3M9QXT2A",
        b"SN-7K3M9QXT2A",
        b"CO_7K3M9QXT2A",
        b"CO-7K3M9QXT2 ",
        b" CO-7K3M9QXT2",
        b"\x00" * 13,
    ],
)
def test_non_canonical_uid_is_rejected(uid):
    assert len(uid) == 13
    _rejects(vec.frame(uid=uid), R.INVALID_COORDINATOR_UID)


def test_valid_uid_is_returned_verbatim():
    assert parse_lora_frame(vec.frame(uid=OTHER_UID)).coordinator_uid == OTHER_UID


# --- key_version y contadores ---


def test_key_version_zero_is_rejected():
    _rejects(vec.frame(key_version=0), R.INVALID_KEY_VERSION)


@pytest.mark.parametrize("key_version", [1, 2, 256, 65535])
def test_key_version_range_is_accepted(key_version):
    assert parse_lora_frame(vec.frame(key_version=key_version)).key_version == key_version


@pytest.mark.parametrize("value", [0, 1, 255, 256, 65536, 2**31, 0xFFFFFFFF])
def test_counters_use_the_full_uint32_range(value):
    frame = parse_lora_frame(vec.frame(boot_counter=value, seq=value))
    assert frame.boot_counter == value and frame.seq == value


def test_counters_are_big_endian():
    frame = parse_lora_frame(vec.frame(boot_counter=0x01020304, seq=0xA0B0C0D0))
    assert frame.signed_bytes[16:20] == b"\x01\x02\x03\x04"
    assert frame.signed_bytes[20:24] == b"\xa0\xb0\xc0\xd0"
    assert (frame.boot_counter, frame.seq) == (0x01020304, 0xA0B0C0D0)


# --- Lecturas ---


@pytest.mark.parametrize(
    "raw_value", [-32768, -32767, -2534, -1, 0, 1, 2534, 32766, 32767]
)
def test_temperature_is_signed_big_endian(raw_value):
    (reading,) = parse_lora_frame(_with_readings(((1, 1, raw_value),))).readings
    assert reading.metric == MetricCode.TEMPERATURE
    assert reading.raw_value == raw_value


@pytest.mark.parametrize("raw_value", [0, 1, 6325, 10000, 10001, 32768, 65534, 65535])
def test_humidity_is_unsigned_big_endian_without_ranges(raw_value):
    (reading,) = parse_lora_frame(_with_readings(((1, 2, raw_value),))).readings
    assert reading.metric == MetricCode.RELATIVE_HUMIDITY
    assert reading.raw_value == raw_value


def test_same_two_bytes_differ_by_metric_signedness():
    temperature, humidity = parse_lora_frame(
        _with_readings(((1, 1, -1), (2, 2, 0xFFFF)))
    ).readings
    assert temperature.raw_value == -1
    assert humidity.raw_value == 65535


def test_value_bytes_are_big_endian():
    raw = _with_readings(((0x0102, 1, 0x09E6),))
    assert raw[25:30] == b"\x01\x02\x01\x09\xe6"
    (reading,) = parse_lora_frame(raw).readings
    assert (reading.channel, reading.raw_value) == (0x0102, 2534)


@pytest.mark.parametrize(
    ("metric_code", "raw_value", "expected"),
    [
        (1, -32768, True),
        (1, -32767, False),
        (1, 0, False),
        (2, 65535, True),
        (2, 65534, False),
        (2, 10000, False),
    ],
)
def test_invalid_markers_are_identified_but_not_rejected(metric_code, raw_value, expected):
    (reading,) = parse_lora_frame(_with_readings(((1, metric_code, raw_value),))).readings
    assert reading.raw_value == raw_value
    assert reading.is_invalid_marker is expected


@pytest.mark.parametrize("metric_code", [0, 3, 4, 127, 255])
def test_unknown_metric_code_is_rejected(metric_code):
    _rejects(_with_readings(((1, metric_code, 0),)), R.UNKNOWN_METRIC_CODE)


def test_unknown_metric_code_in_a_later_block_is_rejected():
    _rejects(_with_readings(((1, 1, 0), (2, 2, 0), (3, 9, 0))), R.UNKNOWN_METRIC_CODE)


@pytest.mark.parametrize("channel", [1, 2, 255, 256, 65535])
def test_channel_range_is_accepted(channel):
    (reading,) = parse_lora_frame(_with_readings(((channel, 1, 0),))).readings
    assert reading.channel == channel


def test_channel_zero_is_rejected():
    _rejects(_with_readings(((0, 1, 0),)), R.INVALID_CHANNEL)
    _rejects(_with_readings(((1, 1, 0), (0, 2, 0))), R.INVALID_CHANNEL)


@pytest.mark.parametrize(
    "readings",
    [
        ((1, 1, 0), (1, 1, 0)),
        ((1, 1, 0), (1, 1, 5)),
        ((1, 1, 0), (1, 2, 0)),
        ((7, 2, 0), (8, 1, 0), (7, 1, 0)),
    ],
)
def test_duplicate_channel_is_rejected_even_with_another_metric(readings):
    _rejects(_with_readings(readings), R.DUPLICATE_CHANNEL)


def test_readings_keep_the_transmitted_order():
    readings = ((9, 2, 100), (3, 1, -5), (5, 2, 0))
    parsed = parse_lora_frame(_with_readings(readings)).readings
    assert [(r.channel, r.raw_value) for r in parsed] == [(9, 100), (3, -5), (5, 0)]


# --- Inmutabilidad y repr ---


def test_frame_and_readings_are_immutable():
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    assert isinstance(frame.readings, tuple)
    for name, value in (("seq", 1), ("coordinator_uid", OTHER_UID), ("tag", b"x" * 32)):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frame, name, value)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.readings[0].raw_value = 0
    assert not hasattr(frame, "__dict__") and not hasattr(frame.readings[0], "__dict__")


def test_frame_repr_hides_uid_signed_bytes_and_tag():
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    text = repr(frame)
    assert vec.UID not in text
    assert "signed_bytes" not in text and "tag" not in text
    assert vec.REFERENCE_TAG_HEX not in text and vec.REFERENCE_SIGNED_HEX not in text
    assert repr(vec.REFERENCE_TAG) not in text
    assert "seq=120" in text


def _rejection_cases():
    return [
        (vec.REFERENCE_FRAME.hex(), R.INVALID_TYPE),
        (b"", R.EMPTY),
        (vec.frame(version=2), R.UNSUPPORTED_VERSION),
        (vec.REFERENCE_FRAME[:10], R.TRUNCATED),
        (_with_readings((), reading_count=0), R.INVALID_READING_COUNT),
        (vec.REFERENCE_FRAME + b"\x00", R.LENGTH_MISMATCH),
        (vec.frame(uid=b"CO-7K3M9QXT2O"), R.INVALID_COORDINATOR_UID),
        (vec.frame(key_version=0), R.INVALID_KEY_VERSION),
        (_with_readings(((0, 1, 0),)), R.INVALID_CHANNEL),
        (_with_readings(((1, 7, 0),)), R.UNKNOWN_METRIC_CODE),
        (_with_readings(((1, 1, 0), (1, 2, 0))), R.DUPLICATE_CHANNEL),
    ]


def test_every_rejection_reason_is_covered():
    assert {reason for _, reason in _rejection_cases()} == set(LoraFrameRejection)


@pytest.mark.parametrize(("raw", "reason"), _rejection_cases())
def test_errors_do_not_reveal_frame_content(raw, reason):
    error = _rejects(raw, reason)
    for text in (str(error), repr(error), str(error.args)):
        assert "CO-7K3M9QXT2" not in text
        assert vec.REFERENCE_TAG_HEX[:16] not in text
        assert "\\x" not in text
    assert str(error) == lf.INVALID_FRAME_MESSAGE
    assert error.args == (lf.INVALID_FRAME_MESSAGE,)
    assert isinstance(error, ValueError)


# --- Adaptador y M-02 ---


def test_adapter_builds_a_coherent_coordinator_frame():
    frame = parse_lora_frame(vec.REFERENCE_FRAME)
    coordinator_frame = frame.to_coordinator_frame()
    assert isinstance(coordinator_frame, crypto.ParsedCoordinatorFrame)
    assert crypto.validate_coordinator_frame(coordinator_frame) is coordinator_frame
    assert (
        coordinator_frame.coordinator_uid,
        coordinator_frame.key_version,
        coordinator_frame.boot_counter,
        coordinator_frame.seq,
        coordinator_frame.signed_bytes,
        coordinator_frame.tag,
    ) == (vec.UID, 1, 7, 120, vec.REFERENCE_SIGNED, vec.REFERENCE_TAG)
    assert crypto.verify_lora_hmac(
        vec.KEY,
        coordinator_frame.signed_bytes,
        coordinator_frame.tag,
        context=lf.LORA_FRAME_HMAC_CONTEXT,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("coordinator_uid", OTHER_UID),
        ("key_version", 2),
        ("key_version", 65535),
        ("boot_counter", 8),
        ("boot_counter", 6),
        ("seq", 121),
        ("seq", 119),
        ("signed_bytes", vec.signed_bytes(seq=121)),
        ("signed_bytes", vec.signed_bytes(uid=OTHER_UID)),
        ("signed_bytes", vec.signed_bytes(key_version=2)),
        ("signed_bytes", vec.signed_bytes(boot_counter=8)),
        ("signed_bytes", vec.REFERENCE_SIGNED + b"\x00"),
        ("signed_bytes", vec.REFERENCE_SIGNED[:-1]),
        ("tag", vec.REFERENCE_TAG[:31]),
        ("tag", vec.REFERENCE_TAG + b"\x00"),
    ],
)
def test_manually_built_contradictory_frames_are_rejected(field_name, value):
    """D11 / M-02: ningún campo declarado puede contradecir los bytes firmados."""
    genuine = parse_lora_frame(vec.REFERENCE_FRAME).to_coordinator_frame()
    forged = dataclasses.replace(genuine, **{field_name: value})
    with pytest.raises(crypto.InvalidLoraTag):
        crypto.validate_coordinator_frame(forged)


@pytest.mark.parametrize(
    ("changes", "signed_fields"),
    [
        ({"seq": 121}, {"seq": 121}),
        # Las lecturas no son campos de ParsedCoordinatorFrame: alterar un valor
        # deja los campos declarados coherentes y debe rechazarlo el HMAC.
        ({}, {"readings": ((1, 1, 2535), (2, 2, 6325))}),
        ({}, {"readings": ((1, 1, 2534), (3, 2, 6325))}),
    ],
)
def test_coherent_but_altered_frames_still_fail_the_hmac(changes, signed_fields):
    """Coherente con los bytes no es auténtico: el tag original no los cubre."""
    genuine = parse_lora_frame(vec.REFERENCE_FRAME).to_coordinator_frame()
    altered = dataclasses.replace(
        genuine, signed_bytes=vec.signed_bytes(**signed_fields), **changes
    )
    assert crypto.validate_coordinator_frame(altered) is altered
    assert not crypto.verify_lora_hmac(
        vec.KEY, altered.signed_bytes, altered.tag, context=lf.LORA_FRAME_HMAC_CONTEXT
    )


@pytest.mark.parametrize("offset", range(67))
def test_flipping_any_bit_never_yields_an_authentic_frame(offset):
    """M-02: cada byte está cubierto por la estructura o por el HMAC."""
    for bit in range(8):
        tampered = bytearray(vec.REFERENCE_FRAME)
        tampered[offset] ^= 1 << bit
        try:
            frame = parse_lora_frame(bytes(tampered))
        except LoraFrameError:
            continue
        assert not crypto.verify_lora_hmac(
            vec.KEY, frame.signed_bytes, frame.tag, context=lf.LORA_FRAME_HMAC_CONTEXT
        )


def test_reordering_readings_changes_the_signature():
    swapped = vec.signed_bytes(readings=tuple(reversed(vec.READINGS)))
    frame = parse_lora_frame(swapped + vec.REFERENCE_TAG)
    assert not crypto.verify_lora_hmac(
        vec.KEY, frame.signed_bytes, frame.tag, context=lf.LORA_FRAME_HMAC_CONTEXT
    )


# --- Robustez ---


def test_random_inputs_only_parse_or_raise_lora_frame_error():
    rng = random.Random(4042)
    for _ in range(2000):
        length = rng.randrange(0, 300)
        raw = bytes(rng.getrandbits(8) for _ in range(length))
        if raw and rng.random() < 0.5:
            raw = b"\x01" + raw[1:]
        try:
            parse_lora_frame(raw)
        except LoraFrameError:
            pass


def test_random_mutations_of_valid_frames_only_parse_or_raise():
    rng = random.Random(1907)
    for _ in range(2000):
        raw = bytearray(vec.REFERENCE_FRAME)
        for _ in range(rng.randrange(1, 4)):
            raw[rng.randrange(len(raw))] = rng.getrandbits(8)
        try:
            frame = parse_lora_frame(bytes(raw))
        except LoraFrameError:
            continue
        assert len(frame.tag) == 32
        assert struct.unpack(">B", frame.signed_bytes[:1]) == (1,)
