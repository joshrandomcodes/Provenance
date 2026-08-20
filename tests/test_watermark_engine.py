"""Watermark header, bit packing, capacity, and extraction classification.

Requirements: 4.1-4.11
"""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from provenance.domain.errors import FailureCode
from provenance.domain.models import AssetHash, CreatorId, RgbArray, WatermarkPayload
from provenance.domain.payload import serialize_payload
from provenance.domain.time import UtcTimestamp
from provenance.domain.watermark import (
    DETAIL_CRC_MISMATCH,
    DETAIL_HEADER_TRUNCATED,
    DETAIL_LENGTH_ABOVE_CAPACITY,
    DETAIL_NO_MAGIC,
    DETAIL_PAYLOAD_INVALID,
    DETAIL_TOO_FEW_CHANNELS,
    DETAIL_UNSUPPORTED_VERSION,
    HEADER_BIT_COUNT,
    HEADER_SIZE,
    MAGIC,
    SCHEMA_VERSION,
    build_frame,
    build_header,
    capacity_report,
    embed,
    embed_frame,
    embed_payload,
    extract,
    is_corrupt_watermark,
    is_no_watermark,
    payload_capacity,
)

pytestmark = pytest.mark.unit

VALID_HASH = "b" * 64
VALID_CREATOR = "studio_1"
VALID_TIMESTAMP = "2026-04-05T06:07:08Z"
PAYLOAD = WatermarkPayload(
    asset_hash=AssetHash(VALID_HASH),
    creator_id=CreatorId(VALID_CREATOR),
    created_at=UtcTimestamp(VALID_TIMESTAMP),
)
SERIALIZED = serialize_payload(PAYLOAD).unwrap()


def _canvas(width: int, height: int, fill: int = 0) -> RgbArray:
    return np.full((height, width, 3), fill, dtype=np.uint8)


def _canvas_for(payload_bytes: int, fill: int = 0) -> RgbArray:
    """Smallest square-ish canvas that can hold this payload."""
    channels_needed = (HEADER_SIZE + payload_bytes) * 8
    pixels = -(-channels_needed // 3) + 1
    return _canvas(pixels, 1, fill)


def _write_frame(rgb: RgbArray, frame: bytes) -> RgbArray:
    """Write frame bits into least significant bits without the production guard."""
    bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8), bitorder="big")
    flat = np.ascontiguousarray(rgb).reshape(-1).copy()
    flat[: bits.size] = (flat[: bits.size] & 0xFE) | bits
    return flat.reshape(rgb.shape)


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1, 1, 0),
        (2, 2, 0),
        (8, 8, 11),
        (10, 10, 24),
        (0, 5, 0),
        (5, 0, 0),
        (100, 100, 3737),
    ],
)
def test_capacity_formula(width: int, height: int, expected: int) -> None:
    assert payload_capacity(width, height) == expected


def test_header_layout_is_exact() -> None:
    header = build_header(SERIALIZED)

    assert len(header) == HEADER_SIZE
    assert header[:4] == MAGIC
    assert header[4] == SCHEMA_VERSION
    assert int.from_bytes(header[5:9], "big") == len(SERIALIZED)
    assert int.from_bytes(header[9:13], "big") == zlib.crc32(SERIALIZED) & 0xFFFFFFFF


def test_header_for_empty_payload_uses_zero_length_and_crc_of_empty() -> None:
    header = build_header(b"")

    assert int.from_bytes(header[5:9], "big") == 0
    assert int.from_bytes(header[9:13], "big") == zlib.crc32(b"") & 0xFFFFFFFF


def test_frame_is_header_then_payload() -> None:
    frame = build_frame(SERIALIZED)

    assert frame == build_header(SERIALIZED) + SERIALIZED
    assert len(frame) == HEADER_SIZE + len(SERIALIZED)


def test_round_trip_returns_the_embedded_payload() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=200)

    embedded = embed(canvas, None, SERIALIZED).unwrap()

    assert extract(embedded.rgb).unwrap() == PAYLOAD
    assert embedded.payload_bytes == len(SERIALIZED)
    assert embedded.capacity_bytes == payload_capacity(embedded.width, embedded.height)


def test_embed_payload_accepts_a_typed_payload() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=17)

    embedded = embed_payload(canvas, None, PAYLOAD).unwrap()

    assert extract(embedded.rgb).unwrap() == PAYLOAD


def test_only_least_significant_bits_change() -> None:
    # 1500 channels cycling through every byte value, with room for the whole frame.
    channel_values = (np.arange(1500, dtype=np.uint16) % 256).astype(np.uint8)
    canvas = channel_values.reshape(1, 500, 3)

    embedded = embed(canvas, None, SERIALIZED).unwrap()

    assert np.array_equal(embedded.rgb & 0xFE, canvas & 0xFE)
    assert embedded.rgb.shape == canvas.shape


def test_channels_after_the_frame_are_untouched() -> None:
    canvas = _canvas(500, 1, fill=0xFF)

    embedded = embed(canvas, None, SERIALIZED).unwrap()

    consumed_channels = (HEADER_SIZE + len(SERIALIZED)) * 8
    original_tail = np.ascontiguousarray(canvas).reshape(-1)[consumed_channels:]
    embedded_tail = np.ascontiguousarray(embedded.rgb).reshape(-1)[consumed_channels:]
    assert np.array_equal(embedded_tail, original_tail)


def test_bits_are_written_most_significant_first() -> None:
    canvas = _canvas_for(len(SERIALIZED))

    embedded = embed(canvas, None, SERIALIZED).unwrap()

    first_byte_bits = np.ascontiguousarray(embedded.rgb).reshape(-1)[:8] & 1
    assert list(first_byte_bits) == [0, 1, 0, 1, 0, 0, 0, 0]  # 'P' is 0x50


def test_alpha_is_preserved_exactly() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=9)
    alpha = np.arange(canvas.shape[0] * canvas.shape[1], dtype=np.uint8).reshape(
        canvas.shape[0], canvas.shape[1]
    )

    embedded = embed(canvas, alpha, SERIALIZED).unwrap()

    assert embedded.alpha is not None
    assert np.array_equal(embedded.alpha, alpha)


def test_alpha_copy_is_independent_of_the_caller_array() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    alpha = np.full(canvas.shape[:2], 5, dtype=np.uint8)

    embedded = embed(canvas, alpha, SERIALIZED).unwrap()
    alpha[0][0] = 250

    assert embedded.alpha is not None
    assert embedded.alpha[0][0] == 5


def test_source_array_is_not_modified() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=0xFF)
    before = canvas.copy()

    embed(canvas, None, SERIALIZED).unwrap()

    assert np.array_equal(canvas, before)


def test_payload_of_exactly_capacity_is_accepted() -> None:
    canvas = _canvas(8, 8)
    capacity = payload_capacity(8, 8)
    frame = build_frame(b"x" * capacity)

    embedded = embed_frame(canvas, None, frame).unwrap()

    assert embedded.payload_bytes == capacity


def test_payload_one_byte_over_capacity_is_rejected_with_counts() -> None:
    canvas = _canvas(8, 8)
    capacity = payload_capacity(8, 8)

    failure = embed(canvas, None, b"x" * (capacity + 1)).unwrap_failure()

    assert failure.code is FailureCode.CAPACITY_EXCEEDED
    assert failure.safe_detail is not None
    assert f"required_bytes={capacity + 1}" in failure.safe_detail
    assert f"available_bytes={capacity}" in failure.safe_detail


def test_capacity_report_reflects_the_boundary() -> None:
    capacity = payload_capacity(8, 8)

    assert capacity_report(8, 8, b"x" * capacity).fits
    assert not capacity_report(8, 8, b"x" * (capacity + 1)).fits


def test_zero_capacity_image_cannot_hold_a_header() -> None:
    failure = embed(_canvas(2, 2), None, b"").unwrap_failure()

    assert failure.code is FailureCode.CAPACITY_EXCEEDED


def test_too_few_channels_is_no_watermark() -> None:
    result = extract(_canvas(3, 3))

    assert is_no_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_TOO_FEW_CHANNELS


def test_absent_magic_is_no_watermark() -> None:
    result = extract(_canvas(40, 1, fill=0))

    assert is_no_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_NO_MAGIC


def test_unwatermarked_photo_like_data_is_no_watermark() -> None:
    generator = np.random.default_rng(seed=1234)
    canvas = generator.integers(0, 256, size=(20, 20, 3), dtype=np.uint8)

    assert is_no_watermark(extract(canvas))


def test_magic_with_truncated_header_is_corrupt() -> None:
    canvas = _canvas(12, 1)  # 36 channels: enough for magic, short of the header

    tampered = _write_frame(canvas, MAGIC)
    result = extract(tampered)

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_HEADER_TRUNCATED


def test_unsupported_schema_version_is_corrupt() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    frame = bytearray(build_frame(SERIALIZED))
    frame[4] = SCHEMA_VERSION + 1

    result = extract(_write_frame(canvas, bytes(frame)))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_UNSUPPORTED_VERSION


def test_declared_length_above_capacity_is_corrupt() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    frame = bytearray(build_frame(SERIALIZED))
    frame[5:9] = (10**6).to_bytes(4, "big")

    result = extract(_write_frame(canvas, bytes(frame)))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_LENGTH_ABOVE_CAPACITY


def test_declared_length_one_over_capacity_is_corrupt() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    capacity = payload_capacity(canvas.shape[1], canvas.shape[0])
    frame = bytearray(build_frame(SERIALIZED))
    frame[5:9] = (capacity + 1).to_bytes(4, "big")

    result = extract(_write_frame(canvas, bytes(frame)))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_LENGTH_ABOVE_CAPACITY


def test_crc_mismatch_is_corrupt() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    frame = bytearray(build_frame(SERIALIZED))
    frame[9:13] = ((zlib.crc32(SERIALIZED) ^ 0xFF) & 0xFFFFFFFF).to_bytes(4, "big")

    result = extract(_write_frame(canvas, bytes(frame)))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_CRC_MISMATCH


def test_single_payload_bit_flip_is_corrupt() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=128)
    embedded = embed(canvas, None, SERIALIZED).unwrap()
    flat = np.ascontiguousarray(embedded.rgb).reshape(-1).copy()
    flat[HEADER_BIT_COUNT] ^= 1

    result = extract(flat.reshape(embedded.rgb.shape))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_CRC_MISMATCH


def test_valid_crc_over_noncanonical_payload_is_corrupt() -> None:
    noncanonical = b'{"created_at":"2026-04-05T06:07:08Z","asset_hash":"' + VALID_HASH.encode()
    noncanonical += b'","creator_id":"' + VALID_CREATOR.encode() + b'"}'
    canvas = _canvas_for(len(noncanonical))

    result = extract(_write_frame(canvas, build_frame(noncanonical)))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_PAYLOAD_INVALID


def test_zero_length_payload_with_valid_crc_is_corrupt() -> None:
    canvas = _canvas_for(0)

    result = extract(_write_frame(canvas, build_frame(b"")))

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_PAYLOAD_INVALID


def test_corrupt_extraction_never_exposes_identity() -> None:
    canvas = _canvas_for(len(SERIALIZED))
    frame = bytearray(build_frame(SERIALIZED))
    frame[4] = 99

    result = extract(_write_frame(canvas, bytes(frame)))

    assert result.value is None
    assert result.unwrap_failure().fields == ()


def test_extraction_works_on_a_strided_view() -> None:
    canvas = _canvas_for(len(SERIALIZED), fill=64)
    embedded = embed(canvas, None, SERIALIZED).unwrap()
    padded = np.zeros((embedded.height, embedded.width * 2, 3), dtype=np.uint8)
    padded[:, ::2, :] = embedded.rgb

    assert extract(padded[:, ::2, :]).unwrap() == PAYLOAD


def test_extraction_rejects_malformed_arrays() -> None:
    with pytest.raises(ValueError, match="shape"):
        extract(np.zeros((4, 4), dtype=np.uint8))
    with pytest.raises(ValueError, match="uint8"):
        extract(np.zeros((4, 4, 3), dtype=np.uint16))


def test_declared_length_within_capacity_always_has_enough_channels() -> None:
    # Documents the invariant that makes a separate truncation branch unreachable.
    # Images too small to hold the header never reach the payload read: extraction
    # stops at DETAIL_TOO_FEW_CHANNELS or DETAIL_HEADER_TRUNCATED first.
    checked = 0
    for width in range(1, 400):
        total_channels = width * 3
        if total_channels < HEADER_BIT_COUNT:
            assert payload_capacity(width, 1) == 0
            continue
        assert HEADER_BIT_COUNT + payload_capacity(width, 1) * 8 <= total_channels
        checked += 1

    assert checked > 300
