"""Shared Hypothesis generators for Provenance property tests.

Development-only module. Production code never imports it.
"""

from __future__ import annotations

import string
from datetime import UTC, datetime
from typing import Final, NamedTuple

import numpy as np
from hypothesis import strategies as st
from hypothesis.extra import numpy as npst

from provenance.domain.models import (
    AlphaArray,
    AssetHash,
    CreatorId,
    RgbArray,
    WatermarkPayload,
)
from provenance.domain.time import UtcTimestamp, format_utc_timestamp
from provenance.domain.watermark import BITS_PER_BYTE, CHANNELS_PER_PIXEL, HEADER_SIZE

HEX_DIGITS: Final = "0123456789abcdef"
CREATOR_ID_CHARACTERS: Final = string.ascii_letters + string.digits + "._-"

INVALID_TIMESTAMPS: Final = (
    "",
    "2026-02-30T00:00:00Z",
    "2023-02-29T00:00:00Z",
    "2026-13-01T00:00:00Z",
    "2026-00-01T00:00:00Z",
    "2026-01-32T00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T00:60:00Z",
    "2026-01-01T00:00:60Z",
    "2026-01-01T00:00:00",
    "2026-01-01T00:00:00.000Z",
    "2026-01-01 00:00:00Z",
    "2026-1-01T00:00:00Z",
    "not-a-timestamp",
)


class SourceImage(NamedTuple):
    """A generated decoded source image."""

    width: int
    height: int
    rgb: RgbArray


def asset_hashes() -> st.SearchStrategy[AssetHash]:
    """Valid 64-character lowercase hexadecimal Asset_Hash values."""
    return st.text(alphabet=HEX_DIGITS, min_size=64, max_size=64).map(AssetHash)


def invalid_asset_hashes() -> st.SearchStrategy[str]:
    """Strings that can never be a valid Asset_Hash."""
    return st.one_of(
        st.text(alphabet=HEX_DIGITS, min_size=0, max_size=63),
        st.text(alphabet=HEX_DIGITS, min_size=65, max_size=70),
        st.text(alphabet="ABCDEF", min_size=64, max_size=64),
        st.text(alphabet="ghijklmnop", min_size=64, max_size=64),
    )


def creator_ids() -> st.SearchStrategy[CreatorId]:
    """Valid Creator_ID values."""
    return st.text(alphabet=CREATOR_ID_CHARACTERS, min_size=1, max_size=64).map(CreatorId)


def invalid_creator_ids() -> st.SearchStrategy[str]:
    """Strings that can never be a valid Creator_ID, and are JSON-safe."""
    return st.one_of(
        st.just(""),
        st.text(alphabet="ab", min_size=65, max_size=70),
        st.sampled_from(["has space", "créator", "a/b", "a@b", "a:b", "a,b"]),
    )


def aware_utc_datetimes() -> st.SearchStrategy[datetime]:
    """Aware UTC datetimes across the full supported calendar range."""
    return st.datetimes(
        min_value=datetime(1, 1, 1, 0, 0, 0),  # noqa: DTZ001
        max_value=datetime(9999, 12, 31, 23, 59, 59),  # noqa: DTZ001
    ).map(lambda value: value.replace(tzinfo=UTC))


def utc_timestamps() -> st.SearchStrategy[UtcTimestamp]:
    """Valid formatted UTC timestamps."""
    return aware_utc_datetimes().map(format_utc_timestamp)


def payloads() -> st.SearchStrategy[WatermarkPayload]:
    """Valid Watermark_Payload values."""
    return st.builds(
        WatermarkPayload,
        asset_hash=asset_hashes(),
        creator_id=creator_ids(),
        created_at=utc_timestamps(),
    )


@st.composite
def source_images(draw: st.DrawFn, max_side: int = 6) -> SourceImage:
    """Small decoded RGB images with arbitrary channel values."""
    width = draw(st.integers(min_value=1, max_value=max_side))
    height = draw(st.integers(min_value=1, max_value=max_side))
    rgb = draw(npst.arrays(dtype=np.uint8, shape=(height, width, 3)))
    return SourceImage(width=width, height=height, rgb=rgb)


@st.composite
def canvases(draw: st.DrawFn, minimum_payload_bytes: int, max_extra_bytes: int = 12) -> RgbArray:
    """An RGB canvas guaranteed to hold a frame for ``minimum_payload_bytes``.

    Pixel values come from a small drawn pattern tiled to size, which keeps generation
    cheap while still varying every channel value.
    """
    extra = draw(st.integers(min_value=0, max_value=max_extra_bytes))
    height = draw(st.integers(min_value=1, max_value=3))
    channels_needed = (HEADER_SIZE + minimum_payload_bytes + extra) * BITS_PER_BYTE
    pixels_needed = -(-channels_needed // CHANNELS_PER_PIXEL)
    width = -(-pixels_needed // height)

    pattern = draw(st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=32))
    flat = np.resize(np.array(pattern, dtype=np.uint8), height * width * CHANNELS_PER_PIXEL)
    return flat.reshape(height, width, CHANNELS_PER_PIXEL)


@st.composite
def small_canvases(draw: st.DrawFn, max_side: int = 12) -> RgbArray:
    """An arbitrary small RGB canvas, usually too small to carry a frame."""
    width = draw(st.integers(min_value=1, max_value=max_side))
    height = draw(st.integers(min_value=1, max_value=max_side))
    return draw(npst.arrays(dtype=np.uint8, shape=(height, width, CHANNELS_PER_PIXEL)))


def alpha_planes(shape: tuple[int, int]) -> st.SearchStrategy[AlphaArray]:
    """An alpha plane matching the given (height, width)."""
    return npst.arrays(dtype=np.uint8, shape=shape)
