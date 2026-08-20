"""Canonical source serialization and deterministic Asset_Hash computation.

The canonical byte stream is exactly::

    b"PRVN-SOURCE\\x00" || width_u64_be || height_u64_be || rgb_bytes

where ``rgb_bytes`` are eight-bit RGB channels in row-major pixel order, R then G
then B. Source encoding, container metadata, EXIF, ICC data, filename, alpha, and
array stride padding are never included, so two images with identical decoded
dimensions and RGB values always produce the same Asset_Hash.

Requirements: 3.1, 20.1, 20.2
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Final

import numpy as np

from provenance.domain.models import AssetHash, DecodedSource, RgbArray

CANONICAL_PREFIX: Final = b"PRVN-SOURCE\x00"
DIMENSION_BYTE_WIDTH: Final = 8
MAX_DIMENSION: Final = 2**64 - 1
ASSET_HASH_LENGTH: Final = 64
ASSET_HASH_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z", re.ASCII)


def _validate_source(width: int, height: int, rgb: RgbArray) -> None:
    """Reject any array that cannot produce a canonical stream.

    A mismatch here is a programming error rather than a user-facing condition,
    because bounded decoding already rejects invalid uploads.
    """
    if width < 1 or height < 1:
        message = "width and height must each be at least one pixel"
        raise ValueError(message)
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        message = "width and height must fit in unsigned 64 bits"
        raise ValueError(message)
    if rgb.dtype != np.uint8:
        message = f"rgb array must be uint8, got {rgb.dtype}"
        raise ValueError(message)
    if rgb.ndim != 3 or rgb.shape != (height, width, 3):
        message = f"rgb array must have shape {(height, width, 3)}, got {rgb.shape}"
        raise ValueError(message)


def canonical_source_chunks(width: int, height: int, rgb: RgbArray) -> Iterator[bytes]:
    """Yield the canonical byte stream in order, one header chunk then one row each.

    Rows are copied into contiguous buffers when needed so a strided view produces
    the same bytes as a contiguous array.
    """
    _validate_source(width, height, rgb)

    yield (
        CANONICAL_PREFIX
        + width.to_bytes(DIMENSION_BYTE_WIDTH, "big")
        + height.to_bytes(DIMENSION_BYTE_WIDTH, "big")
    )
    for row_index in range(height):
        yield np.ascontiguousarray(rgb[row_index]).tobytes()


def canonical_source_bytes(width: int, height: int, rgb: RgbArray) -> bytes:
    """Return the complete canonical stream. Intended for small images and tests."""
    return b"".join(canonical_source_chunks(width, height, rgb))


def compute_asset_hash(width: int, height: int, rgb: RgbArray) -> AssetHash:
    """Compute the SHA-256 Asset_Hash over the canonical source stream."""
    digest = hashlib.sha256()
    for chunk in canonical_source_chunks(width, height, rgb):
        digest.update(chunk)
    return AssetHash(digest.hexdigest())


def asset_hash_for_source(source: DecodedSource) -> AssetHash:
    """Compute the Asset_Hash for a decoded source image."""
    return compute_asset_hash(source.width, source.height, source.rgb)


def is_valid_asset_hash(value: str) -> bool:
    """True when the value is exactly 64 lowercase hexadecimal characters."""
    return ASSET_HASH_PATTERN.match(value) is not None
