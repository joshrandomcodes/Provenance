"""Verified lossless PNG encoding.

Requirements: 4.5, 4.6, 5.1, 5.5
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from provenance.domain.errors import FailureCode
from provenance.domain.models import AssetHash, CreatorId, WatermarkPayload
from provenance.domain.payload import serialize_payload
from provenance.domain.time import UtcTimestamp
from provenance.domain.watermark import embed, extract
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.png_codec import (
    DETAIL_ALPHA_MISMATCH,
    DETAIL_RGB_MISMATCH,
    DETAIL_SHAPE_MISMATCH,
    PillowPngEncoder,
)

pytestmark = pytest.mark.unit

PAYLOAD = WatermarkPayload(
    asset_hash=AssetHash("c" * 64),
    creator_id=CreatorId("codec.test"),
    created_at=UtcTimestamp("2026-07-08T09:10:11Z"),
)


@pytest.fixture
def encoder() -> PillowPngEncoder:
    return PillowPngEncoder()


def test_rgb_round_trip_is_exact(encoder: PillowPngEncoder) -> None:
    generator = np.random.default_rng(seed=7)
    rgb = generator.integers(0, 256, size=(6, 5, 3), dtype=np.uint8)

    encoded = encoder.encode_verified(rgb, None).unwrap()

    decoded = PillowImageDecoder().decode(encoded).unwrap()
    assert np.array_equal(decoded.rgb, rgb)
    assert decoded.alpha is None


def test_alpha_round_trip_is_exact(encoder: PillowPngEncoder) -> None:
    rgb = np.full((4, 4, 3), 33, dtype=np.uint8)
    alpha = np.arange(16, dtype=np.uint8).reshape(4, 4)

    encoded = encoder.encode_verified(rgb, alpha).unwrap()

    decoded = PillowImageDecoder().decode(encoded).unwrap()
    assert np.array_equal(decoded.rgb, rgb)
    assert decoded.alpha is not None
    assert np.array_equal(decoded.alpha, alpha)


def test_fully_transparent_alpha_preserves_rgb(encoder: PillowPngEncoder) -> None:
    rgb = np.full((3, 3, 3), 200, dtype=np.uint8)
    alpha = np.zeros((3, 3), dtype=np.uint8)

    encoded = encoder.encode_verified(rgb, alpha).unwrap()

    decoded = PillowImageDecoder().decode(encoded).unwrap()
    assert np.array_equal(decoded.rgb, rgb)
    assert decoded.alpha is not None
    assert int(decoded.alpha.max()) == 0


def test_output_is_a_png_container(encoder: PillowPngEncoder) -> None:
    encoded = encoder.encode_verified(np.zeros((2, 2, 3), dtype=np.uint8), None).unwrap()

    assert encoded.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.format == "PNG"


def test_watermark_survives_the_png_round_trip(encoder: PillowPngEncoder) -> None:
    serialized = serialize_payload(PAYLOAD).unwrap()
    canvas = np.full((1, ((13 + len(serialized)) * 8) // 3 + 1, 3), 120, dtype=np.uint8)
    embedded = embed(canvas, None, serialized).unwrap()

    encoded = encoder.encode_verified(embedded.rgb, embedded.alpha).unwrap()

    decoded = PillowImageDecoder().decode(encoded).unwrap()
    assert extract(decoded.rgb).unwrap() == PAYLOAD


def test_watermark_survives_the_round_trip_with_alpha(encoder: PillowPngEncoder) -> None:
    serialized = serialize_payload(PAYLOAD).unwrap()
    width = ((13 + len(serialized)) * 8) // 3 + 1
    canvas = np.full((1, width, 3), 77, dtype=np.uint8)
    alpha = np.full((1, width), 128, dtype=np.uint8)
    embedded = embed(canvas, alpha, serialized).unwrap()

    encoded = encoder.encode_verified(embedded.rgb, embedded.alpha).unwrap()

    decoded = PillowImageDecoder().decode(encoded).unwrap()
    assert extract(decoded.rgb).unwrap() == PAYLOAD
    assert decoded.alpha is not None
    assert np.array_equal(decoded.alpha, alpha)


@pytest.mark.parametrize(
    "shape",
    [(4, 4), (4, 4, 1), (4, 4, 4)],
)
def test_invalid_rgb_shapes_are_rejected(encoder: PillowPngEncoder, shape: tuple[int, ...]) -> None:
    failure = encoder.encode_verified(np.zeros(shape, dtype=np.uint8), None).unwrap_failure()

    assert failure.code is FailureCode.PNG_ROUNDTRIP_FAILED
    assert failure.safe_detail == DETAIL_SHAPE_MISMATCH


def test_wrong_dtype_is_rejected(encoder: PillowPngEncoder) -> None:
    failure = encoder.encode_verified(np.zeros((2, 2, 3), dtype=np.uint16), None).unwrap_failure()

    assert failure.safe_detail == DETAIL_SHAPE_MISMATCH


def test_mismatched_alpha_shape_is_rejected(encoder: PillowPngEncoder) -> None:
    failure = encoder.encode_verified(
        np.zeros((2, 2, 3), dtype=np.uint8), np.zeros((3, 3), dtype=np.uint8)
    ).unwrap_failure()

    assert failure.safe_detail == DETAIL_ALPHA_MISMATCH


def test_verification_catches_a_lossy_encoder(
    encoder: PillowPngEncoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    rgb = np.full((3, 3, 3), 101, dtype=np.uint8)

    def lossy_encode(_self: PillowPngEncoder, _rgb: np.ndarray, _alpha: np.ndarray | None) -> bytes:
        """Return a valid PNG whose pixels differ from the input."""
        buffer = io.BytesIO()
        Image.fromarray(np.full((3, 3, 3), 100, dtype=np.uint8), mode="RGB").save(
            buffer, format="PNG"
        )
        return buffer.getvalue()

    monkeypatch.setattr(PillowPngEncoder, "_encode", lossy_encode)

    failure = encoder.encode_verified(rgb, None).unwrap_failure()

    assert failure.code is FailureCode.PNG_ROUNDTRIP_FAILED
    assert failure.safe_detail == DETAIL_RGB_MISMATCH


def test_verification_catches_encoder_errors(
    encoder: PillowPngEncoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_encode(
        _self: PillowPngEncoder, _rgb: np.ndarray, _alpha: np.ndarray | None
    ) -> bytes:
        raise OSError("disk full")

    monkeypatch.setattr(PillowPngEncoder, "_encode", failing_encode)

    failure = encoder.encode_verified(np.zeros((2, 2, 3), dtype=np.uint8), None).unwrap_failure()

    assert failure.code is FailureCode.PNG_ROUNDTRIP_FAILED


def test_failures_carry_no_pixel_data(encoder: PillowPngEncoder) -> None:
    failure = encoder.encode_verified(np.zeros((4, 4), dtype=np.uint8), None).unwrap_failure()

    assert failure.fields == ()
    assert failure.safe_detail is not None
    assert len(failure.safe_detail) < 64
