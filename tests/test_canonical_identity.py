"""Canonical source layout and Asset_Hash determinism.

Requirements: 3.1, 20.1, 20.2
"""

from __future__ import annotations

import hashlib
import io

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from provenance.domain.canonical_image import (
    CANONICAL_PREFIX,
    asset_hash_for_source,
    canonical_source_bytes,
    compute_asset_hash,
    is_valid_asset_hash,
)
from provenance.domain.models import RgbArray
from provenance.infrastructure.image_decoder import PillowImageDecoder

pytestmark = pytest.mark.unit


def _rgb(values: list[list[tuple[int, int, int]]]) -> RgbArray:
    return np.array(values, dtype=np.uint8)


def test_canonical_layout_is_exact() -> None:
    rgb = _rgb([[(1, 2, 3), (4, 5, 6)]])

    stream = canonical_source_bytes(2, 1, rgb)

    assert stream == (
        b"PRVN-SOURCE\x00"
        + (2).to_bytes(8, "big")
        + (1).to_bytes(8, "big")
        + bytes([1, 2, 3, 4, 5, 6])
    )
    assert stream.startswith(CANONICAL_PREFIX)
    assert len(stream) == 12 + 8 + 8 + 6


def test_asset_hash_matches_sha256_of_the_canonical_stream() -> None:
    rgb = _rgb([[(9, 8, 7)]])

    expected = hashlib.sha256(canonical_source_bytes(1, 1, rgb)).hexdigest()

    assert compute_asset_hash(1, 1, rgb) == expected
    assert is_valid_asset_hash(expected)


def test_channels_are_serialized_in_row_major_rgb_order() -> None:
    rgb = _rgb([[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]])

    stream = canonical_source_bytes(2, 2, rgb)

    assert stream[-12:] == bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])


def test_identical_pixels_produce_identical_hashes() -> None:
    first = _rgb([[(10, 20, 30), (40, 50, 60)]])
    second = _rgb([[(10, 20, 30), (40, 50, 60)]])

    assert compute_asset_hash(2, 1, first) == compute_asset_hash(2, 1, second)


def test_strided_views_hash_like_contiguous_copies() -> None:
    base = _rgb([[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]])
    flipped = base[::-1]

    assert not flipped.flags["C_CONTIGUOUS"]
    assert compute_asset_hash(2, 2, flipped) == compute_asset_hash(
        2, 2, np.ascontiguousarray(flipped)
    )


def test_dimensions_are_bound_into_the_hash() -> None:
    pixels = [(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4), (5, 5, 5), (6, 6, 6)]
    two_by_three = np.array(pixels, dtype=np.uint8).reshape(3, 2, 3)
    three_by_two = np.array(pixels, dtype=np.uint8).reshape(2, 3, 3)

    assert two_by_three.tobytes() == three_by_two.tobytes()
    assert compute_asset_hash(2, 3, two_by_three) != compute_asset_hash(3, 2, three_by_two)


def test_single_channel_change_changes_the_hash() -> None:
    original = _rgb([[(1, 2, 3)]])
    altered = _rgb([[(1, 2, 4)]])

    assert compute_asset_hash(1, 1, original) != compute_asset_hash(1, 1, altered)


def test_png_metadata_does_not_affect_the_hash() -> None:
    image = Image.new("RGB", (3, 2), color=(12, 34, 56))
    plain = io.BytesIO()
    annotated = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "provenance test metadata")
    image.save(plain, format="PNG")
    image.save(annotated, format="PNG", pnginfo=metadata)

    decoder = PillowImageDecoder()
    plain_source = decoder.decode(plain.getvalue()).unwrap()
    annotated_source = decoder.decode(annotated.getvalue()).unwrap()

    assert plain.getvalue() != annotated.getvalue()
    assert asset_hash_for_source(plain_source) == asset_hash_for_source(annotated_source)


def test_alpha_channel_does_not_affect_the_hash() -> None:
    opaque = Image.new("RGBA", (2, 2), color=(7, 7, 7, 255))
    translucent = Image.new("RGBA", (2, 2), color=(7, 7, 7, 32))
    decoder = PillowImageDecoder()
    buffers = []
    for image in (opaque, translucent):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffers.append(buffer.getvalue())

    first = decoder.decode(buffers[0]).unwrap()
    second = decoder.decode(buffers[1]).unwrap()

    assert first.alpha is not None
    assert second.alpha is not None
    assert asset_hash_for_source(first) == asset_hash_for_source(second)


def test_asset_hash_is_lowercase_hex_of_fixed_length() -> None:
    value = compute_asset_hash(1, 1, _rgb([[(0, 0, 0)]]))

    assert len(value) == 64
    assert value == value.lower()
    assert is_valid_asset_hash(value)


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "abc",
        "A" * 64,
        "g" * 64,
        "0" * 63,
        "0" * 65,
        " " + "0" * 63,
    ],
)
def test_invalid_asset_hash_strings_are_rejected(candidate: str) -> None:
    assert not is_valid_asset_hash(candidate)


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 1), (1, 0), (-1, 1), (2, 1)],
)
def test_dimension_mismatches_raise(width: int, height: int) -> None:
    rgb = _rgb([[(1, 2, 3)]])

    with pytest.raises(ValueError, match="width|height|shape"):
        compute_asset_hash(width, height, rgb)


def test_non_uint8_arrays_raise() -> None:
    rgb = np.zeros((1, 1, 3), dtype=np.uint16)

    with pytest.raises(ValueError, match="uint8"):
        compute_asset_hash(1, 1, rgb)  # type: ignore[arg-type]


def test_wrong_channel_count_raises() -> None:
    rgb = np.zeros((1, 1, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="shape"):
        compute_asset_hash(1, 1, rgb)
