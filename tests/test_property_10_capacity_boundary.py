"""Property 10: Capacity boundary is exact.

Validates: Requirements 4.7, 4.8, 20.7, 20.8
"""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.errors import FailureCode
from provenance.domain.models import RgbArray
from provenance.domain.watermark import (
    BITS_PER_BYTE,
    CHANNELS_PER_PIXEL,
    HEADER_SIZE,
    capacity_report,
    embed,
    embed_frame,
    payload_capacity,
)


@st.composite
def dimensioned_canvases(draw: st.DrawFn) -> RgbArray:
    """Canvases spanning zero-capacity through comfortably sized images."""
    height = draw(st.integers(min_value=1, max_value=4))
    width = draw(st.integers(min_value=1, max_value=120))
    fill = draw(st.integers(min_value=0, max_value=255))
    return np.full((height, width, CHANNELS_PER_PIXEL), fill, dtype=np.uint8)


@given(st.integers(min_value=1, max_value=400), st.integers(min_value=1, max_value=8))
def test_capacity_matches_the_specified_formula(width: int, height: int) -> None:
    # Feature: provenance, Property 10: Capacity boundary is exact
    total_channels = width * height * CHANNELS_PER_PIXEL
    expected = max(0, total_channels // BITS_PER_BYTE - HEADER_SIZE)

    assert payload_capacity(width, height) == expected


@given(dimensioned_canvases())
def test_payload_of_exactly_capacity_is_accepted(canvas: RgbArray) -> None:
    # Feature: provenance, Property 10: Capacity boundary is exact
    height, width = canvas.shape[0], canvas.shape[1]
    capacity = payload_capacity(width, height)
    if capacity == 0:
        return

    embedded = embed(canvas, None, b"x" * capacity).unwrap()

    assert embedded.payload_bytes == capacity
    assert embedded.capacity_bytes == capacity
    assert capacity_report(width, height, b"x" * capacity).fits


@given(dimensioned_canvases(), st.integers(min_value=1, max_value=64))
def test_payload_over_capacity_is_rejected_with_exact_counts(
    canvas: RgbArray, overflow: int
) -> None:
    # Feature: provenance, Property 10: Capacity boundary is exact
    height, width = canvas.shape[0], canvas.shape[1]
    capacity = payload_capacity(width, height)
    required = capacity + overflow

    result = embed(canvas, None, b"x" * required)
    failure = result.unwrap_failure()

    assert result.value is None
    assert failure.code is FailureCode.CAPACITY_EXCEEDED
    assert failure.safe_detail is not None
    assert f"required_bytes={required}" in failure.safe_detail
    assert f"available_bytes={capacity}" in failure.safe_detail
    assert not capacity_report(width, height, b"x" * required).fits


@given(dimensioned_canvases(), st.integers(min_value=1, max_value=64))
def test_rejected_embedding_produces_no_image_and_no_mutation(
    canvas: RgbArray, overflow: int
) -> None:
    # Feature: provenance, Property 10: Capacity boundary is exact
    height, width = canvas.shape[0], canvas.shape[1]
    capacity = payload_capacity(width, height)
    before = canvas.copy()

    result = embed(canvas, None, b"x" * (capacity + overflow))

    assert result.value is None
    assert np.array_equal(canvas, before)


@given(dimensioned_canvases())
def test_zero_capacity_images_cannot_carry_even_an_empty_payload(canvas: RgbArray) -> None:
    # Feature: provenance, Property 10: Capacity boundary is exact
    height, width = canvas.shape[0], canvas.shape[1]
    total_channels = width * height * CHANNELS_PER_PIXEL

    result = embed_frame(canvas, None, b"\x00" * HEADER_SIZE)

    if total_channels < HEADER_SIZE * BITS_PER_BYTE:
        assert result.value is None
        assert result.unwrap_failure().code is FailureCode.CAPACITY_EXCEEDED
    else:
        assert result.failure is None
