"""Property 11: Recognized watermark corruption is never a match.

Validates: Requirements 4.11, 20.9
"""

from __future__ import annotations

import zlib

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import RgbArray, WatermarkPayload
from provenance.domain.payload import serialize_payload
from provenance.domain.watermark import (
    DETAIL_CRC_MISMATCH,
    HEADER_BIT_COUNT,
    MAGIC,
    SCHEMA_VERSION,
    build_frame,
    embed,
    extract,
    is_corrupt_watermark,
    payload_capacity,
)
from tests.strategies import canvases, payloads


def _write_frame(canvas: RgbArray, frame: bytes) -> RgbArray:
    bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8), bitorder="big")
    flat = np.ascontiguousarray(canvas).reshape(-1).copy()
    flat[: bits.size] = (flat[: bits.size] & 0xFE) | bits
    return flat.reshape(canvas.shape)


@st.composite
def corrupted_images(draw: st.DrawFn) -> RgbArray:
    """Images whose magic marker is intact but whose frame is defective."""
    payload: WatermarkPayload = draw(payloads())
    serialized = serialize_payload(payload).unwrap()
    canvas = draw(canvases(minimum_payload_bytes=len(serialized)))
    height, width = canvas.shape[0], canvas.shape[1]
    frame = bytearray(build_frame(serialized))
    variant = draw(
        st.sampled_from(
            [
                "bad_version",
                "length_above_capacity",
                "crc_mismatch",
                "payload_bit_flip",
                "noncanonical_payload",
            ]
        )
    )

    if variant == "bad_version":
        frame[4] = draw(
            st.integers(min_value=0, max_value=255).filter(lambda v: v != SCHEMA_VERSION)
        )
        return _write_frame(canvas, bytes(frame))
    if variant == "length_above_capacity":
        capacity = payload_capacity(width, height)
        overflow = draw(st.integers(min_value=1, max_value=1000))
        frame[5:9] = (capacity + overflow).to_bytes(4, "big")
        return _write_frame(canvas, bytes(frame))
    if variant == "crc_mismatch":
        stored = int.from_bytes(frame[9:13], "big")
        mask = draw(st.integers(min_value=1, max_value=0xFFFFFFFF))
        frame[9:13] = ((stored ^ mask) & 0xFFFFFFFF).to_bytes(4, "big")
        return _write_frame(canvas, bytes(frame))
    if variant == "payload_bit_flip":
        written = _write_frame(canvas, bytes(frame))
        flat = np.ascontiguousarray(written).reshape(-1).copy()
        bit_index = draw(
            st.integers(min_value=0, max_value=len(serialized) * 8 - 1),
        )
        flat[HEADER_BIT_COUNT + bit_index] ^= 1
        return flat.reshape(written.shape)

    reordered = (
        f'{{"created_at":"{payload.created_at}","asset_hash":"{payload.asset_hash}",'
        f'"creator_id":"{payload.creator_id}"}}'.encode()
    )
    wider = draw(canvases(minimum_payload_bytes=len(reordered)))
    return _write_frame(wider, build_frame(reordered))


@given(corrupted_images())
def test_recognized_corruption_is_corrupt_and_never_a_match(image: RgbArray) -> None:
    # Feature: provenance, Property 11: Recognized watermark corruption is never a match
    result = extract(image)

    assert is_corrupt_watermark(result)
    assert result.value is None
    assert result.unwrap_failure().fields == ()


@st.composite
def single_bit_flipped_images(draw: st.DrawFn) -> tuple[RgbArray, int]:
    payload: WatermarkPayload = draw(payloads())
    serialized = serialize_payload(payload).unwrap()
    canvas = draw(canvases(minimum_payload_bytes=len(serialized)))
    embedded = embed(canvas, None, serialized).unwrap()
    bit_index = draw(st.integers(min_value=0, max_value=len(serialized) * 8 - 1))
    flat = np.ascontiguousarray(embedded.rgb).reshape(-1).copy()
    flat[HEADER_BIT_COUNT + bit_index] ^= 1
    return flat.reshape(embedded.rgb.shape), bit_index


@given(single_bit_flipped_images())
def test_any_single_payload_bit_flip_fails_the_stored_crc(
    case: tuple[RgbArray, int],
) -> None:
    # Feature: provenance, Property 11: Recognized watermark corruption is never a match
    image, _bit_index = case

    result = extract(image)

    assert is_corrupt_watermark(result)
    assert result.unwrap_failure().safe_detail == DETAIL_CRC_MISMATCH


@given(payloads())
def test_crc_is_recomputed_over_the_extracted_bytes(payload: WatermarkPayload) -> None:
    # Feature: provenance, Property 11: Recognized watermark corruption is never a match
    serialized = serialize_payload(payload).unwrap()
    frame = build_frame(serialized)

    assert frame[:4] == MAGIC
    assert int.from_bytes(frame[9:13], "big") == zlib.crc32(serialized) & 0xFFFFFFFF
