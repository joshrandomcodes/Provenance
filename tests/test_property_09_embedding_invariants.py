"""Property 9: Embedding preserves the lossless image invariants.

Validates: Requirements 4.3, 4.4, 4.5, 4.6, 20.6
"""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import AlphaArray, RgbArray, WatermarkPayload
from provenance.domain.payload import serialize_payload
from provenance.domain.watermark import build_frame, embed, extract
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.png_codec import PillowPngEncoder
from tests.strategies import canvases, payloads


@st.composite
def payload_canvas_and_alpha(
    draw: st.DrawFn,
) -> tuple[WatermarkPayload, RgbArray, AlphaArray | None]:
    payload = draw(payloads())
    serialized = serialize_payload(payload).unwrap()
    canvas = draw(canvases(minimum_payload_bytes=len(serialized)))
    include_alpha = draw(st.booleans())
    alpha: AlphaArray | None = None
    if include_alpha:
        pattern = draw(st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=16))
        alpha = np.resize(
            np.array(pattern, dtype=np.uint8), canvas.shape[0] * canvas.shape[1]
        ).reshape(canvas.shape[0], canvas.shape[1])
    return payload, canvas, alpha


@given(payload_canvas_and_alpha())
def test_embedding_changes_only_consumed_low_bits(
    case: tuple[WatermarkPayload, RgbArray, AlphaArray | None],
) -> None:
    # Feature: provenance, Property 9: Embedding preserves the lossless image invariants
    payload, canvas, alpha = case
    serialized = serialize_payload(payload).unwrap()
    frame = build_frame(serialized)
    frame_bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8), bitorder="big")

    embedded = embed(canvas, alpha, serialized).unwrap()

    before = np.ascontiguousarray(canvas).reshape(-1)
    after = np.ascontiguousarray(embedded.rgb).reshape(-1)

    # Dimensions unchanged.
    assert embedded.rgb.shape == canvas.shape
    assert (embedded.width, embedded.height) == (canvas.shape[1], canvas.shape[0])
    # Every bit above the least significant bit is preserved everywhere.
    assert np.array_equal(after & 0xFE, before & 0xFE)
    # Consumed low bits carry the frame, most significant bit first.
    assert np.array_equal(after[: frame_bits.size] & 1, frame_bits)
    # Channels after the frame are untouched, including their low bits.
    assert np.array_equal(after[frame_bits.size :], before[frame_bits.size :])


@given(payload_canvas_and_alpha())
def test_alpha_is_preserved_and_copied(
    case: tuple[WatermarkPayload, RgbArray, AlphaArray | None],
) -> None:
    # Feature: provenance, Property 9: Embedding preserves the lossless image invariants
    payload, canvas, alpha = case
    serialized = serialize_payload(payload).unwrap()

    embedded = embed(canvas, alpha, serialized).unwrap()

    if alpha is None:
        assert embedded.alpha is None
        return

    assert embedded.alpha is not None
    assert np.array_equal(embedded.alpha, alpha)
    # The stored plane is a copy, so later caller mutation cannot change it.
    original = alpha.copy()
    alpha[...] = (alpha.astype(np.uint16) + 1).astype(np.uint8)
    assert np.array_equal(embedded.alpha, original)


@given(payload_canvas_and_alpha())
def test_source_arrays_are_never_mutated(
    case: tuple[WatermarkPayload, RgbArray, AlphaArray | None],
) -> None:
    # Feature: provenance, Property 9: Embedding preserves the lossless image invariants
    payload, canvas, alpha = case
    serialized = serialize_payload(payload).unwrap()
    canvas_before = canvas.copy()

    embed(canvas, alpha, serialized).unwrap()

    assert np.array_equal(canvas, canvas_before)


@given(payload_canvas_and_alpha())
def test_png_encoding_preserves_pixels_and_payload(
    case: tuple[WatermarkPayload, RgbArray, AlphaArray | None],
) -> None:
    # Feature: provenance, Property 9: Embedding preserves the lossless image invariants
    payload, canvas, alpha = case
    serialized = serialize_payload(payload).unwrap()
    embedded = embed(canvas, alpha, serialized).unwrap()

    encoded = PillowPngEncoder().encode_verified(embedded.rgb, embedded.alpha).unwrap()
    decoded = PillowImageDecoder().decode(encoded).unwrap()

    assert decoded.width == embedded.width
    assert decoded.height == embedded.height
    assert np.array_equal(decoded.rgb, embedded.rgb)
    assert extract(decoded.rgb).unwrap() == payload
    if embedded.alpha is not None:
        assert decoded.alpha is not None
        assert np.array_equal(decoded.alpha, embedded.alpha)
