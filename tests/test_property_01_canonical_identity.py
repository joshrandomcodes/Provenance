"""Property 1: Canonical image identity equivalence.

Validates: Requirements 3.1, 20.1
"""

from __future__ import annotations

import io

import numpy as np
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image, PngImagePlugin

from provenance.domain.canonical_image import asset_hash_for_source, compute_asset_hash
from provenance.infrastructure.image_decoder import PillowImageDecoder
from tests.strategies import SourceImage, source_images


def _encode_png(array: np.ndarray, mode: str, metadata: PngImagePlugin.PngInfo | None) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array, mode=mode).save(buffer, format="PNG", pnginfo=metadata)
    return buffer.getvalue()


@given(source_images(), st.integers(min_value=0, max_value=255), st.text(max_size=20))
def test_equal_decoded_pixels_always_hash_equally(
    source: SourceImage, alpha_value: int, comment: str
) -> None:
    # Feature: provenance, Property 1: Canonical image identity equivalence
    baseline = compute_asset_hash(source.width, source.height, source.rgb)

    # A distinct object with equal values.
    assert compute_asset_hash(source.width, source.height, source.rgb.copy()) == baseline

    # A non-contiguous view whose values are equal.
    padded = np.zeros((source.height, source.width * 2, 3), dtype=np.uint8)
    padded[:, ::2, :] = source.rgb
    assert compute_asset_hash(source.width, source.height, padded[:, ::2, :]) == baseline

    # Different container encodings, PNG text metadata, and alpha are all excluded.
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", comment)
    alpha = np.full((source.height, source.width, 1), alpha_value, dtype=np.uint8)
    rgba = np.concatenate([source.rgb, alpha], axis=2)

    decoder = PillowImageDecoder()
    variants = (
        _encode_png(source.rgb, "RGB", None),
        _encode_png(source.rgb, "RGB", metadata),
        _encode_png(rgba, "RGBA", None),
    )
    for encoded in variants:
        decoded = decoder.decode(encoded).unwrap()
        assert decoded.width == source.width
        assert decoded.height == source.height
        assert asset_hash_for_source(decoded) == baseline
