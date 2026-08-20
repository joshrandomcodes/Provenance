"""PNG encoding port.

Encoding is verified by decoding the produced bytes and comparing them to the input,
so a watermarked download can never be offered unless it is provably lossless.

Requirements: 4.6, 5.1, 5.5
"""

from __future__ import annotations

from typing import Protocol

from provenance.domain.errors import Result
from provenance.domain.models import AlphaArray, RgbArray


class PngEncoderPort(Protocol):
    """Encode RGB and optional alpha planes into verified PNG bytes."""

    def encode_verified(
        self, rgb: RgbArray, alpha: AlphaArray | None, *, operation: str = "encode_png"
    ) -> Result[bytes]:
        """Return PNG bytes that decode back to exactly these pixels."""
        ...
