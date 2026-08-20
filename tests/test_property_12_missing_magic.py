"""Property 12: Missing magic is No_Watermark.

Validates: Requirements 4.10
"""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import RgbArray
from provenance.domain.watermark import (
    CHANNELS_PER_PIXEL,
    MAGIC,
    MAGIC_BIT_COUNT,
    extract,
    is_no_watermark,
)
from tests.strategies import small_canvases


def _leading_marker(canvas: RgbArray) -> bytes | None:
    """Read the first four bytes from least significant bits, if available."""
    flat = np.ascontiguousarray(canvas).reshape(-1)
    if flat.size < MAGIC_BIT_COUNT:
        return None
    bits = (flat[:MAGIC_BIT_COUNT] & 1).astype(np.uint8)
    return bytes(np.packbits(bits, bitorder="big"))


@given(small_canvases())
def test_images_without_the_marker_report_no_watermark(canvas: RgbArray) -> None:
    # Feature: provenance, Property 12: Missing magic is No_Watermark
    marker = _leading_marker(canvas)
    result = extract(canvas)

    if marker is None or marker != MAGIC:
        assert is_no_watermark(result)
        assert result.value is None
    else:
        # A generated image that happens to carry the marker must not be classified as
        # absent; it is either a valid frame or corruption.
        assert not is_no_watermark(result)


@given(st.integers(min_value=1, max_value=10), st.integers(min_value=0, max_value=255))
def test_images_below_the_marker_length_always_report_no_watermark(pixels: int, fill: int) -> None:
    # Feature: provenance, Property 12: Missing magic is No_Watermark
    canvas = np.full((1, pixels, CHANNELS_PER_PIXEL), fill, dtype=np.uint8)

    if pixels * CHANNELS_PER_PIXEL < MAGIC_BIT_COUNT:
        assert is_no_watermark(extract(canvas))


@given(st.integers(min_value=11, max_value=200), st.integers(min_value=0, max_value=255))
def test_uniform_fills_never_produce_the_marker(pixels: int, fill: int) -> None:
    # Feature: provenance, Property 12: Missing magic is No_Watermark
    canvas = np.full((1, pixels, CHANNELS_PER_PIXEL), fill, dtype=np.uint8)

    # A uniform fill sets every low bit identically, so the marker cannot appear.
    assert is_no_watermark(extract(canvas))
