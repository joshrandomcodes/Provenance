"""Lossless PNG encoding with a mandatory decode-and-compare verification step.

Pillow writes PNG losslessly, but the watermark lives in least significant bits, so
a silent mode conversion or plugin change would destroy the payload without any
error. Every encode is therefore decoded again and compared byte for byte before the
result is returned.

Requirements: 4.5, 4.6, 5.1, 5.5
"""

from __future__ import annotations

import io
from typing import Final

import numpy as np
from PIL import Image

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import AlphaArray, RgbArray

_RGB_MODE: Final = "RGB"
_RGBA_MODE: Final = "RGBA"

DETAIL_SHAPE_MISMATCH: Final = "decoded_shape_mismatch"
DETAIL_RGB_MISMATCH: Final = "decoded_rgb_mismatch"
DETAIL_ALPHA_MISMATCH: Final = "decoded_alpha_mismatch"
DETAIL_ENCODE_ERROR: Final = "encode_error"
DETAIL_DECODE_ERROR: Final = "decode_error"


class PillowPngEncoder:
    """Encode verified lossless PNG bytes."""

    __slots__ = ()

    def encode_verified(
        self, rgb: RgbArray, alpha: AlphaArray | None, *, operation: str = "encode_png"
    ) -> Result[bytes]:
        """Encode to PNG and prove the decoded pixels match the input exactly."""
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_SHAPE_MISMATCH
            )
        if alpha is not None and alpha.shape != rgb.shape[:2]:
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_ALPHA_MISMATCH
            )

        try:
            encoded = self._encode(rgb, alpha)
        except (OSError, ValueError):
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_ENCODE_ERROR
            )

        verification = self._verify(encoded, rgb, alpha, operation)
        if verification is not None:
            return verification
        return ok(encoded)

    def _encode(self, rgb: RgbArray, alpha: AlphaArray | None) -> bytes:
        if alpha is None:
            image = Image.fromarray(np.ascontiguousarray(rgb), mode=_RGB_MODE)
        else:
            stacked = np.concatenate(
                [np.ascontiguousarray(rgb), alpha.reshape(*alpha.shape, 1)], axis=2
            )
            image = Image.fromarray(np.ascontiguousarray(stacked), mode=_RGBA_MODE)

        buffer = io.BytesIO()
        with image:
            image.save(buffer, format="PNG", optimize=False, compress_level=6)
        return buffer.getvalue()

    def _verify(
        self, encoded: bytes, rgb: RgbArray, alpha: AlphaArray | None, operation: str
    ) -> Result[bytes] | None:
        """Return a failure result, or None when the round trip is exact."""
        try:
            with Image.open(io.BytesIO(encoded)) as decoded:
                decoded.load()
                if decoded.format != "PNG":
                    return failed(
                        FailureCode.PNG_ROUNDTRIP_FAILED,
                        operation,
                        safe_detail=DETAIL_DECODE_ERROR,
                    )
                decoded_rgb = np.asarray(decoded.convert(_RGB_MODE), dtype=np.uint8)
                decoded_alpha = (
                    np.asarray(decoded.convert(_RGBA_MODE).getchannel("A"), dtype=np.uint8)
                    if alpha is not None
                    else None
                )
        except (OSError, ValueError):
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_DECODE_ERROR
            )

        if decoded_rgb.shape != rgb.shape:
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_SHAPE_MISMATCH
            )
        if not np.array_equal(decoded_rgb, rgb):
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_RGB_MISMATCH
            )
        if alpha is not None and (
            decoded_alpha is None or not np.array_equal(decoded_alpha, alpha)
        ):
            return failed(
                FailureCode.PNG_ROUNDTRIP_FAILED, operation, safe_detail=DETAIL_ALPHA_MISMATCH
            )
        return None
