"""In-memory analysis of one retrieved image.

One retrieved image produces exactly one terminal classification, and it does so without
touching the Registry, the filesystem, or any cache. The order of checks matters:

1. the declared media type must be an image type, so non-image bodies are refused before
   any decoder sees them;
2. header facts are read next, which costs no full-frame allocation;
3. the pixel count is charged to the scan budget *before* the decode that would allocate
   those pixels;
4. only then is the image fully decoded and the watermark frame read.

A valid extracted payload is reported as ``UNREGISTERED``, not ``VERIFIED``. Promotion to
``VERIFIED`` requires a Registry cross-check on both Asset_Hash and Creator_ID, which is
deliberately not this module's job: nothing here can manufacture a registered match.

Failure of one image is local to that image. Every rejection returns a typed failure
carrying a safe category and no identity fields.

Requirements: 9.4, 9.5, 17.6, 18.1
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    DecodedSource,
    ExtractionEvidence,
    ExtractionKind,
    ImageFacts,
    WatermarkPayload,
)
from provenance.domain.payload import serialize_payload
from provenance.domain.scan_budget import ScanBudget
from provenance.domain.watermark import extract, is_corrupt_watermark, is_no_watermark
from provenance.ports.images import ImageDecoderPort

ANALYZE_OPERATION: Final = "analyze_image"

IMAGE_TYPE_PREFIX: Final = "image/"
CRC_MASK: Final = 0xFFFFFFFF

DETAIL_MISSING_MEDIA_TYPE: Final = "media_type_absent"
DETAIL_NOT_AN_IMAGE: Final = "media_type_not_image"
DETAIL_UNSERIALIZABLE_PAYLOAD: Final = "payload_not_reserializable"


def parse_media_type(content_type: str | None) -> str | None:
    """The lowercase media type from a Content-Type header, without parameters."""
    if content_type is None:
        return None
    primary = content_type.split(";", 1)[0].strip().lower()
    return primary if primary != "" else None


def is_image_media_type(content_type: str | None) -> bool:
    """True only for a well-formed ``image/<subtype>`` media type."""
    primary = parse_media_type(content_type)
    if primary is None:
        return False
    return primary.startswith(IMAGE_TYPE_PREFIX) and len(primary) > len(IMAGE_TYPE_PREFIX)


@dataclass(frozen=True, slots=True, repr=False)
class AnalyzedImage:
    """One image's header facts, decoded pixels, and single extraction outcome.

    The decoded pixels are volatile. The caller owns their lifetime and must drop this
    value unless the corresponding incident is the one current triage selection.
    """

    facts: ImageFacts
    source: DecodedSource
    evidence: ExtractionEvidence

    def __repr__(self) -> str:
        """Redacted representation. Pixel data must never reach a diagnostic record."""
        return (
            f"AnalyzedImage(kind={self.evidence.kind.value}, "
            f"media_type={self.facts.media_type.value}, "
            f"width={self.facts.width}, height={self.facts.height})"
        )


def payload_crc32(payload: WatermarkPayload) -> int | None:
    """CRC-32 over the payload's canonical bytes, or None if it cannot be serialized.

    Extraction already validated the embedded CRC against these exact bytes, and the
    codec round trips byte for byte, so this reproduces the embedded value rather than
    computing a new one.
    """
    serialized = serialize_payload(payload)
    if serialized.failure is not None:
        return None
    return zlib.crc32(serialized.unwrap()) & CRC_MASK


class ImageAnalyzer:
    """Turns one retrieved image body into exactly one extraction outcome."""

    __slots__ = ("_decoder",)

    def __init__(self, decoder: ImageDecoderPort) -> None:
        self._decoder = decoder

    def analyze(
        self, data: bytes, *, content_type: str | None, budget: ScanBudget
    ) -> Result[AnalyzedImage]:
        """Validate, decode, and extract, charging the pixel budget before decoding."""
        if parse_media_type(content_type) is None:
            return failed(
                FailureCode.UNSUPPORTED_MEDIA_TYPE,
                ANALYZE_OPERATION,
                safe_detail=DETAIL_MISSING_MEDIA_TYPE,
            )
        if not is_image_media_type(content_type):
            return failed(
                FailureCode.UNSUPPORTED_MEDIA_TYPE,
                ANALYZE_OPERATION,
                safe_detail=DETAIL_NOT_AN_IMAGE,
            )

        inspected = self._decoder.inspect(data, operation=ANALYZE_OPERATION)
        if inspected.failure is not None:
            return Result(failure=inspected.failure)
        facts = inspected.unwrap()

        # Charged before the allocation, not after, so an oversized image is refused
        # without ever holding its pixels.
        permitted = budget.accept_decoded_pixels(facts.pixel_count)
        if permitted.failure is not None:
            return Result(failure=permitted.failure)

        guard = budget.check_continue()
        if guard.failure is not None:
            return Result(failure=guard.failure)

        decoded = self._decoder.decode(data, operation=ANALYZE_OPERATION)
        if decoded.failure is not None:
            return Result(failure=decoded.failure)
        source = decoded.unwrap()

        return ok(
            AnalyzedImage(facts=facts, source=source, evidence=self._classify(source)),
        )

    def _classify(self, source: DecodedSource) -> ExtractionEvidence:
        """Reduce extraction to exactly one terminal category."""
        result = extract(source.rgb)

        if is_no_watermark(result):
            return ExtractionEvidence(
                kind=ExtractionKind.NO_WATERMARK, detail=result.unwrap_failure().safe_detail
            )
        if is_corrupt_watermark(result):
            return ExtractionEvidence(
                kind=ExtractionKind.CORRUPT_WATERMARK, detail=result.unwrap_failure().safe_detail
            )
        if result.failure is not None:
            return ExtractionEvidence(
                kind=ExtractionKind.FAILED, detail=result.unwrap_failure().code.value
            )

        payload = result.unwrap()
        crc32 = payload_crc32(payload)
        if crc32 is None:
            # A parsed payload that cannot be reserialized is not trustworthy evidence.
            return ExtractionEvidence(
                kind=ExtractionKind.CORRUPT_WATERMARK, detail=DETAIL_UNSERIALIZABLE_PAYLOAD
            )

        # Valid frame, identity not yet checked against the Registry.
        return ExtractionEvidence(kind=ExtractionKind.UNREGISTERED, payload=payload, crc32=crc32)
