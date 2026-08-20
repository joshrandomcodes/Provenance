"""Bounded Pillow image decoding.

Header facts are inspected before any full-frame allocation, the decoded format is
restricted to PNG and JPEG, and every library error becomes a safe typed failure.
Pixels are normalized to C-contiguous eight-bit RGB with alpha preserved separately.

Requirements: 2.1, 2.2, 4.5, 9.4, 17.4
"""

from __future__ import annotations

import io
from typing import Final

import numpy as np
from PIL import Image, UnidentifiedImageError

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    MAX_UPLOAD_BYTES,
    DecodedSource,
    MediaType,
)
from provenance.domain.validation import validate_dimensions

_FORMAT_MEDIA_TYPES: Final = {"PNG": MediaType.PNG, "JPEG": MediaType.JPEG}
_ALPHA_MODES: Final = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})


class PillowImageDecoder:
    """Decode PNG and JPEG bytes within the documented limits."""

    __slots__ = ()

    def decode(self, data: bytes, *, operation: str = "decode_image") -> Result[DecodedSource]:
        """Decode image bytes, returning a typed failure on any rejection."""
        if len(data) == 0:
            return failed(FailureCode.EMPTY_FILE, operation)
        if len(data) > MAX_UPLOAD_BYTES:
            return failed(FailureCode.BYTE_LIMIT, operation)

        header = self._inspect_header(data, operation)
        if not header.is_ok:
            return failed(
                header.unwrap_failure().code, operation, fields=header.unwrap_failure().fields
            )

        media_type, width, height = header.unwrap()
        dimension_report = validate_dimensions(width, height)
        if not dimension_report.is_valid:
            first = dimension_report.issues[0]
            return failed(first.code, operation, fields=dimension_report.issues)

        return self._decode_pixels(data, media_type, width, height, operation)

    def _inspect_header(self, data: bytes, operation: str) -> Result[tuple[MediaType, int, int]]:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
        except UnidentifiedImageError:
            return failed(FailureCode.UNSUPPORTED_FORMAT, operation)
        except Image.DecompressionBombError:
            return failed(FailureCode.PIXEL_LIMIT, operation)
        except (OSError, ValueError):
            return failed(FailureCode.DECODE_FAILURE, operation)

        media_type = _FORMAT_MEDIA_TYPES.get(image_format or "")
        if media_type is None:
            return failed(FailureCode.UNSUPPORTED_FORMAT, operation)
        return ok((media_type, int(width), int(height)))

    def _decode_pixels(
        self,
        data: bytes,
        media_type: MediaType,
        width: int,
        height: int,
        operation: str,
    ) -> Result[DecodedSource]:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                has_alpha = image.mode in _ALPHA_MODES or "transparency" in image.info
                rgb_array = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
                alpha_array = (
                    np.ascontiguousarray(
                        np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
                    )
                    if has_alpha
                    else None
                )
        except Image.DecompressionBombError:
            return failed(FailureCode.PIXEL_LIMIT, operation)
        except (OSError, ValueError, SyntaxError):
            return failed(FailureCode.DECODE_FAILURE, operation)

        if rgb_array.shape != (height, width, 3):
            return failed(FailureCode.DECODE_FAILURE, operation)
        if alpha_array is not None and alpha_array.shape != (height, width):
            return failed(FailureCode.DECODE_FAILURE, operation)

        return ok(
            DecodedSource(
                width=width,
                height=height,
                media_type=media_type,
                rgb=rgb_array,
                alpha=alpha_array,
            )
        )
