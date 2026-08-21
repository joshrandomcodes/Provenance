"""In-memory image analysis, media-type gating, and single terminal classification.

Requirements: 9.4, 9.5, 17.6, 18.1
"""

from __future__ import annotations

import io
import tempfile
import zlib
from datetime import UTC, datetime

import numpy as np
import pytest
from PIL import Image

from provenance.application.image_analysis import (
    AnalyzedImage,
    ImageAnalyzer,
    is_image_media_type,
    parse_media_type,
    payload_crc32,
)
from provenance.domain.cancellation import CooperativeCancellationToken
from provenance.domain.errors import FailureCode
from provenance.domain.models import (
    AssetHash,
    CreatorId,
    ExtractionKind,
    MediaType,
    WatermarkPayload,
)
from provenance.domain.payload import serialize_payload
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.time import UtcTimestamp
from provenance.domain.watermark import (
    HEADER_SIZE,
    MAGIC,
    SCHEMA_VERSION,
    embed_frame,
    embed_payload,
)
from provenance.infrastructure.image_decoder import PillowImageDecoder

pytestmark = pytest.mark.unit

VALID_HASH = "b" * 64
VALID_CREATOR = "studio.creator_1-x"
VALID_TIMESTAMP = "2026-08-21T10:20:30Z"

PNG_TYPE = "image/png"


class FrozenClock:
    """Clock that never advances, isolating analysis from timing."""

    __slots__ = ()

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, tzinfo=UTC)

    def monotonic(self) -> float:
        return 500.0


def _budget(limits: ScanLimits | None = None) -> ScanBudget:
    return ScanBudget(limits or ScanLimits(), FrozenClock(), None)


def _analyzer() -> ImageAnalyzer:
    return ImageAnalyzer(PillowImageDecoder())


def _payload() -> WatermarkPayload:
    return WatermarkPayload(
        asset_hash=AssetHash(VALID_HASH),
        creator_id=CreatorId(VALID_CREATOR),
        created_at=UtcTimestamp(VALID_TIMESTAMP),
    )


def _rgb(width: int = 400, height: int = 4, seed: int = 7) -> np.ndarray:
    generator = np.random.default_rng(seed=seed)
    return generator.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def _encode_png(rgb: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _encode_jpeg(rgb: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _clean_png(width: int = 400, height: int = 4) -> bytes:
    return _encode_png(_rgb(width, height))


def _watermarked_png(width: int = 400, height: int = 4) -> bytes:
    embedded = embed_payload(_rgb(width, height), None, _payload()).unwrap()
    return _encode_png(embedded.rgb)


def _analyze(data: bytes, content_type: str | None = PNG_TYPE) -> AnalyzedImage:
    return _analyzer().analyze(data, content_type=content_type, budget=_budget()).unwrap()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("image/png", "image/png"),
        ("IMAGE/PNG", "image/png"),
        ("image/jpeg; charset=binary", "image/jpeg"),
        ("  image/webp  ", "image/webp"),
        ("text/html", "text/html"),
        ("", None),
        (";charset=utf-8", None),
        (None, None),
    ],
)
def test_media_type_parsing_drops_parameters_and_case(
    header: str | None, expected: str | None
) -> None:
    assert parse_media_type(header) == expected


@pytest.mark.parametrize(
    "header",
    ["image/png", "image/jpeg", "image/gif", "IMAGE/AVIF", "image/svg+xml; q=1"],
)
def test_image_media_types_are_accepted(header: str) -> None:
    assert is_image_media_type(header) is True


@pytest.mark.parametrize(
    "header",
    ["text/html", "application/octet-stream", "image/", "image", "", None, "imagex/png"],
)
def test_non_image_media_types_are_refused(header: str | None) -> None:
    assert is_image_media_type(header) is False


def test_a_missing_content_type_is_refused_before_decoding() -> None:
    failure = (
        _analyzer().analyze(_clean_png(), content_type=None, budget=_budget()).unwrap_failure()
    )

    assert failure.code is FailureCode.UNSUPPORTED_MEDIA_TYPE
    assert failure.safe_detail == "media_type_absent"


def test_a_non_image_content_type_is_refused_before_decoding() -> None:
    failure = (
        _analyzer()
        .analyze(_clean_png(), content_type="text/html", budget=_budget())
        .unwrap_failure()
    )

    assert failure.code is FailureCode.UNSUPPORTED_MEDIA_TYPE
    assert failure.safe_detail == "media_type_not_image"


def test_an_image_content_type_with_non_image_bytes_fails_to_decode() -> None:
    result = _analyzer().analyze(b"not an image", content_type=PNG_TYPE, budget=_budget())

    assert result.unwrap_failure().code is FailureCode.UNSUPPORTED_FORMAT


def test_a_truncated_image_body_fails_to_decode() -> None:
    truncated = _clean_png()[:40]

    result = _analyzer().analyze(truncated, content_type=PNG_TYPE, budget=_budget())

    assert result.failure is not None


def test_an_unwatermarked_png_is_classified_as_no_watermark() -> None:
    analyzed = _analyze(_clean_png())

    assert analyzed.evidence.kind is ExtractionKind.NO_WATERMARK
    assert analyzed.evidence.payload is None
    assert analyzed.evidence.crc32 is None


def test_header_facts_describe_the_decoded_image() -> None:
    analyzed = _analyze(_clean_png(width=320, height=5))

    assert analyzed.facts.media_type is MediaType.PNG
    assert (analyzed.facts.width, analyzed.facts.height) == (320, 5)
    assert analyzed.facts.pixel_count == 1_600
    assert analyzed.source.width == 320
    assert analyzed.source.rgb.shape == (5, 320, 3)


def test_a_jpeg_body_is_accepted_and_reports_its_media_type() -> None:
    analyzed = _analyze(_encode_jpeg(_rgb(64, 4)), content_type="image/jpeg")

    assert analyzed.facts.media_type is MediaType.JPEG


def test_a_watermarked_png_yields_the_payload_pending_registry_check() -> None:
    analyzed = _analyze(_watermarked_png())
    evidence = analyzed.evidence

    assert evidence.kind is ExtractionKind.UNREGISTERED
    assert evidence.payload is not None
    assert evidence.payload.asset_hash == AssetHash(VALID_HASH)
    assert evidence.payload.creator_id == CreatorId(VALID_CREATOR)


def test_analysis_alone_never_reports_a_verified_match() -> None:
    """Only a Registry cross-check may promote a payload to verified."""
    for data in (_clean_png(), _watermarked_png()):
        assert _analyze(data).evidence.kind is not ExtractionKind.VERIFIED


def test_the_reported_crc_matches_the_embedded_frame() -> None:
    analyzed = _analyze(_watermarked_png())
    canonical = serialize_payload(_payload()).unwrap()

    assert analyzed.evidence.crc32 == zlib.crc32(canonical) & 0xFFFFFFFF


def test_payload_crc32_reproduces_the_canonical_checksum() -> None:
    canonical = serialize_payload(_payload()).unwrap()

    assert payload_crc32(_payload()) == zlib.crc32(canonical) & 0xFFFFFFFF


def test_a_flipped_payload_bit_is_corrupt_not_a_match() -> None:
    embedded = embed_payload(_rgb(), None, _payload()).unwrap()
    damaged = embedded.rgb.copy()
    # Flip one body bit, well past the 13-byte header.
    body_channel = HEADER_SIZE * 8 + 5
    flat = damaged.reshape(-1)
    flat[body_channel] ^= 1

    analyzed = _analyze(_encode_png(damaged))

    assert analyzed.evidence.kind is ExtractionKind.CORRUPT_WATERMARK
    assert analyzed.evidence.payload is None


def test_a_recognized_marker_with_a_bad_version_is_corrupt() -> None:
    canonical = serialize_payload(_payload()).unwrap()
    frame = (
        MAGIC
        + bytes([SCHEMA_VERSION + 1])
        + len(canonical).to_bytes(4, "big")
        + (zlib.crc32(canonical) & 0xFFFFFFFF).to_bytes(4, "big")
        + canonical
    )
    embedded = embed_frame(_rgb(), None, frame).unwrap()

    analyzed = _analyze(_encode_png(embedded.rgb))

    assert analyzed.evidence.kind is ExtractionKind.CORRUPT_WATERMARK


def test_corrupt_extraction_reveals_no_identity() -> None:
    embedded = embed_payload(_rgb(), None, _payload()).unwrap()
    damaged = embedded.rgb.copy()
    flat = damaged.reshape(-1)
    flat[HEADER_SIZE * 8 + 5] ^= 1

    evidence = _analyze(_encode_png(damaged)).evidence

    assert evidence.payload is None
    assert VALID_HASH not in repr(evidence)
    assert VALID_CREATOR not in repr(evidence)


def test_an_image_over_the_pixel_budget_is_refused_before_decoding() -> None:
    data = _clean_png(width=400, height=4)
    budget = _budget(ScanLimits(decoded_pixels=100))

    result = _analyzer().analyze(data, content_type=PNG_TYPE, budget=budget)

    assert result.unwrap_failure().code is FailureCode.PIXEL_LIMIT


def test_an_image_at_exactly_the_pixel_budget_is_analyzed() -> None:
    data = _clean_png(width=400, height=4)
    budget = _budget(ScanLimits(decoded_pixels=1_600))

    result = _analyzer().analyze(data, content_type=PNG_TYPE, budget=budget)

    assert result.unwrap().facts.pixel_count == 1_600


def test_a_cancelled_scan_stops_analysis_before_decoding() -> None:
    token = CooperativeCancellationToken()
    token.cancel()
    budget = ScanBudget(ScanLimits(), FrozenClock(), token)

    result = _analyzer().analyze(_clean_png(), content_type=PNG_TYPE, budget=budget)

    assert result.unwrap_failure().code is FailureCode.CANCELLED


def test_the_pixel_budget_is_charged_before_the_cancellation_check() -> None:
    """An oversized image is refused on size, not masked by a cancellation."""
    token = CooperativeCancellationToken()
    token.cancel()
    budget = ScanBudget(ScanLimits(decoded_pixels=1), FrozenClock(), token)

    result = _analyzer().analyze(_clean_png(), content_type=PNG_TYPE, budget=budget)

    assert result.unwrap_failure().code is FailureCode.PIXEL_LIMIT


def test_one_image_failure_does_not_affect_the_next_image() -> None:
    analyzer = _analyzer()
    budget = _budget()

    first = analyzer.analyze(b"garbage", content_type=PNG_TYPE, budget=budget)
    second = analyzer.analyze(_watermarked_png(), content_type=PNG_TYPE, budget=budget)

    assert first.failure is not None
    assert second.unwrap().evidence.kind is ExtractionKind.UNREGISTERED


def test_the_analysis_representation_redacts_pixels() -> None:
    analyzed = _analyze(_watermarked_png())
    rendered = repr(analyzed)

    assert "AnalyzedImage(" in rendered
    assert "width=400" in rendered
    assert "array" not in rendered
    assert "rgb" not in rendered


def test_analysis_creates_no_temporary_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoding and extraction happen in memory, so any tempfile use is a defect."""

    def forbidden(*_args: object, **_kwargs: object) -> object:
        message = "image analysis created a temporary file"
        raise AssertionError(message)

    for name in ("NamedTemporaryFile", "TemporaryFile", "mkstemp", "mkdtemp"):
        monkeypatch.setattr(tempfile, name, forbidden)

    analyzed = _analyze(_watermarked_png())

    assert analyzed.evidence.kind is ExtractionKind.UNREGISTERED
