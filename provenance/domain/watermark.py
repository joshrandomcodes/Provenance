"""Exact LSB watermark embedding and extraction.

The embedded frame is a 13-byte header followed immediately by the serialized
Watermark_Payload::

    MAGIC ("PRVN") | schema version (1 byte) | payload length (u32 big-endian)
    | CRC-32 of the payload bytes (u32 big-endian)

Bits are consumed most-significant-bit first per byte and written into the least
significant bit of each RGB channel, traversing channels row-major as R, G, B per
pixel. Every higher-order bit, every channel after the frame, the alpha channel, and
the image dimensions are preserved exactly.

Extraction distinguishes three outcomes:

* absent magic marker -> ``NO_WATERMARK``
* recognized magic with any structural or content defect -> ``CORRUPT_WATERMARK``
* fully valid frame -> the parsed payload

Requirements: 4.1-4.11, 20.5-20.9
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Final

import numpy as np

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import AlphaArray, DecodedSource, RgbArray, WatermarkPayload
from provenance.domain.payload import parse_payload, serialize_payload

MAGIC: Final = b"PRVN"
SCHEMA_VERSION: Final = 1
HEADER_SIZE: Final = 13
BITS_PER_BYTE: Final = 8
CHANNELS_PER_PIXEL: Final = 3

MAGIC_BIT_COUNT: Final = len(MAGIC) * BITS_PER_BYTE
HEADER_BIT_COUNT: Final = HEADER_SIZE * BITS_PER_BYTE

_VERSION_OFFSET: Final = 4
_LENGTH_SLICE: Final = slice(5, 9)
_CRC_SLICE: Final = slice(9, 13)
_CRC_MASK: Final = 0xFFFFFFFF
_LSB_CLEAR_MASK: Final = 0xFE

EMBED_OPERATION: Final = "embed_watermark"
EXTRACT_OPERATION: Final = "extract_watermark"

DETAIL_NO_MAGIC: Final = "magic_absent"
DETAIL_TOO_FEW_CHANNELS: Final = "too_few_channels"
DETAIL_HEADER_TRUNCATED: Final = "header_truncated"
DETAIL_UNSUPPORTED_VERSION: Final = "unsupported_schema_version"
DETAIL_LENGTH_ABOVE_CAPACITY: Final = "declared_length_above_capacity"
DETAIL_CRC_MISMATCH: Final = "crc_mismatch"
DETAIL_PAYLOAD_INVALID: Final = "payload_invalid"


@dataclass(frozen=True, slots=True)
class EmbeddedImage:
    """A watermarked image held in memory, with its capacity accounting."""

    width: int
    height: int
    rgb: RgbArray
    alpha: AlphaArray | None
    payload_bytes: int
    capacity_bytes: int


@dataclass(frozen=True, slots=True)
class CapacityReport:
    """Required and available payload byte counts for one embedding attempt."""

    required_bytes: int
    available_bytes: int

    @property
    def fits(self) -> bool:
        """True when the payload fits within the available capacity."""
        return self.required_bytes <= self.available_bytes


def payload_capacity(width: int, height: int) -> int:
    """Return the payload bytes an image of these dimensions can carry."""
    if width < 1 or height < 1:
        return 0
    total_channels = width * height * CHANNELS_PER_PIXEL
    return max(0, total_channels // BITS_PER_BYTE - HEADER_SIZE)


def capacity_for_source(source: DecodedSource) -> int:
    """Return the payload capacity of a decoded source image."""
    return payload_capacity(source.width, source.height)


def build_header(payload: bytes) -> bytes:
    """Build the exact 13-byte frame header for these payload bytes."""
    return (
        MAGIC
        + bytes([SCHEMA_VERSION])
        + len(payload).to_bytes(4, "big")
        + (zlib.crc32(payload) & _CRC_MASK).to_bytes(4, "big")
    )


def build_frame(payload: bytes) -> bytes:
    """Build the complete header-plus-payload frame."""
    return build_header(payload) + payload


def _validate_rgb(rgb: RgbArray) -> None:
    if rgb.dtype != np.uint8:
        message = f"rgb array must be uint8, got {rgb.dtype}"
        raise ValueError(message)
    if rgb.ndim != 3 or rgb.shape[2] != CHANNELS_PER_PIXEL:
        message = f"rgb array must have shape (height, width, 3), got {rgb.shape}"
        raise ValueError(message)


def _frame_bits(frame: bytes) -> np.ndarray:
    """Expand frame bytes to one bit per element, most-significant bit first."""
    return np.unpackbits(np.frombuffer(frame, dtype=np.uint8), bitorder="big")


def embed_frame(rgb: RgbArray, alpha: AlphaArray | None, frame: bytes) -> Result[EmbeddedImage]:
    """Embed an already-built frame without re-validating its payload.

    ``embed`` is the normal entry point. This function exists so callers that must
    construct a specific frame, such as tamper-detection tests, use the same exact
    bit-writing path as production embedding.
    """
    _validate_rgb(rgb)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    capacity = payload_capacity(width, height)

    bits = _frame_bits(frame)
    flat_source = np.ascontiguousarray(rgb).reshape(-1)
    if bits.size > flat_source.size:
        return failed(
            FailureCode.CAPACITY_EXCEEDED,
            EMBED_OPERATION,
            safe_detail=f"required_bits={bits.size} available_bits={flat_source.size}",
        )

    flat_output = flat_source.copy()
    consumed = flat_output[: bits.size]
    flat_output[: bits.size] = (consumed & _LSB_CLEAR_MASK) | bits.astype(np.uint8)

    return ok(
        EmbeddedImage(
            width=width,
            height=height,
            rgb=flat_output.reshape(rgb.shape),
            alpha=None if alpha is None else np.array(alpha, dtype=np.uint8, copy=True),
            payload_bytes=max(0, len(frame) - HEADER_SIZE),
            capacity_bytes=capacity,
        )
    )


def embed(rgb: RgbArray, alpha: AlphaArray | None, payload: bytes) -> Result[EmbeddedImage]:
    """Embed payload bytes, refusing anything larger than the exact capacity."""
    _validate_rgb(rgb)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    report = capacity_report(width, height, payload)
    if not report.fits:
        return failed(
            FailureCode.CAPACITY_EXCEEDED,
            EMBED_OPERATION,
            safe_detail=(
                f"required_bytes={report.required_bytes} available_bytes={report.available_bytes}"
            ),
        )
    return embed_frame(rgb, alpha, build_frame(payload))


def embed_payload(
    rgb: RgbArray, alpha: AlphaArray | None, payload: WatermarkPayload
) -> Result[EmbeddedImage]:
    """Serialize a typed payload and embed it."""
    serialized = serialize_payload(payload)
    if serialized.failure is not None:
        return Result(failure=serialized.failure)
    return embed(rgb, alpha, serialized.unwrap())


def capacity_report(width: int, height: int, payload: bytes) -> CapacityReport:
    """Report the required and available payload byte counts."""
    return CapacityReport(
        required_bytes=len(payload), available_bytes=payload_capacity(width, height)
    )


def _corrupt(detail: str) -> Result[WatermarkPayload]:
    return failed(FailureCode.CORRUPT_WATERMARK, EXTRACT_OPERATION, safe_detail=detail)


def _no_watermark(detail: str) -> Result[WatermarkPayload]:
    return failed(FailureCode.NO_WATERMARK, EXTRACT_OPERATION, safe_detail=detail)


def _read_bytes(channels: np.ndarray, start_bit: int, end_bit: int) -> bytes:
    bits = (channels[start_bit:end_bit] & 1).astype(np.uint8)
    return bytes(np.packbits(bits, bitorder="big"))


def extract(rgb: RgbArray) -> Result[WatermarkPayload]:
    """Extract a payload, or report No_Watermark or Corrupt_Watermark.

    A failure never carries an identity or timestamp field.
    """
    _validate_rgb(rgb)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    channels = np.ascontiguousarray(rgb).reshape(-1)
    available = int(channels.size)

    if available < MAGIC_BIT_COUNT:
        return _no_watermark(DETAIL_TOO_FEW_CHANNELS)
    if _read_bytes(channels, 0, MAGIC_BIT_COUNT) != MAGIC:
        return _no_watermark(DETAIL_NO_MAGIC)

    # From here the marker is recognized, so every defect is corruption.
    if available < HEADER_BIT_COUNT:
        return _corrupt(DETAIL_HEADER_TRUNCATED)

    header = _read_bytes(channels, 0, HEADER_BIT_COUNT)
    if header[_VERSION_OFFSET] != SCHEMA_VERSION:
        return _corrupt(DETAIL_UNSUPPORTED_VERSION)

    declared_length = int.from_bytes(header[_LENGTH_SLICE], "big")
    stored_crc = int.from_bytes(header[_CRC_SLICE], "big")
    if declared_length > payload_capacity(width, height):
        return _corrupt(DETAIL_LENGTH_ABOVE_CAPACITY)

    # Capacity is derived from the same array, and capacity is defined as
    # total_channels // 8 - 13, so a declared length within capacity always has enough
    # remaining channels: 104 + declared * 8 <= (total_channels // 8) * 8 <= available.
    # A separate truncation check here would be unreachable.
    end_bit = HEADER_BIT_COUNT + declared_length * BITS_PER_BYTE
    payload = _read_bytes(channels, HEADER_BIT_COUNT, end_bit)
    if (zlib.crc32(payload) & _CRC_MASK) != stored_crc:
        return _corrupt(DETAIL_CRC_MISMATCH)

    parsed = parse_payload(payload)
    if parsed.failure is not None:
        return _corrupt(DETAIL_PAYLOAD_INVALID)
    return ok(parsed.unwrap())


def is_no_watermark(result: Result[WatermarkPayload]) -> bool:
    """True when extraction found no magic marker."""
    return result.failure is not None and result.failure.code is FailureCode.NO_WATERMARK


def is_corrupt_watermark(result: Result[WatermarkPayload]) -> bool:
    """True when extraction recognized a marker but rejected the frame."""
    return result.failure is not None and result.failure.code is FailureCode.CORRUPT_WATERMARK
