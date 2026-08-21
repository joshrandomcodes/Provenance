"""Image decoding port.

Decoding is bounded and returns typed failures instead of raising library errors.

Requirements: 2.1, 2.2, 4.5, 9.4
"""

from __future__ import annotations

from typing import Protocol

from provenance.domain.errors import Result
from provenance.domain.models import DecodedSource, ImageFacts


class ImageDecoderPort(Protocol):
    """Decode image bytes into eight-bit RGB with optional alpha."""

    def inspect(self, data: bytes, *, operation: str = "inspect_image") -> Result[ImageFacts]:
        """Read container facts from the header without allocating a full frame.

        Separate from ``decode`` so a caller can enforce a pixel budget before the
        memory for those pixels is committed.
        """
        ...

    def decode(self, data: bytes, *, operation: str = "decode_image") -> Result[DecodedSource]:
        """Decode fully, or return a bounded, safe failure."""
        ...
