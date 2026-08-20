"""Bounded Pillow decoding tests.

Requirements: 2.1, 2.2, 4.5, 9.4
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from provenance.domain.errors import FailureCode, Result, ok
from provenance.domain.models import MAX_UPLOAD_BYTES, MediaType
from provenance.infrastructure.image_decoder import PillowImageDecoder

pytestmark = pytest.mark.unit


@pytest.fixture
def decoder() -> PillowImageDecoder:
    return PillowImageDecoder()


def _encode(image: Image.Image, image_format: str) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_png_decodes_to_contiguous_rgb(decoder: PillowImageDecoder) -> None:
    image = Image.new("RGB", (4, 3), color=(10, 20, 30))

    decoded = decoder.decode(_encode(image, "PNG")).unwrap()

    assert decoded.media_type is MediaType.PNG
    assert (decoded.width, decoded.height) == (4, 3)
    assert decoded.rgb.shape == (3, 4, 3)
    assert decoded.rgb.dtype == np.uint8
    assert decoded.rgb.flags["C_CONTIGUOUS"]
    assert decoded.alpha is None
    assert decoded.pixel_count == 12


def test_jpeg_decodes_with_jpeg_media_type(decoder: PillowImageDecoder) -> None:
    image = Image.new("RGB", (8, 8), color=(200, 100, 50))

    decoded = decoder.decode(_encode(image, "JPEG")).unwrap()

    assert decoded.media_type is MediaType.JPEG
    assert decoded.rgb.shape == (8, 8, 3)


def test_alpha_channel_is_preserved_separately(decoder: PillowImageDecoder) -> None:
    image = Image.new("RGBA", (2, 2), color=(1, 2, 3, 128))
    image.putpixel((0, 0), (1, 2, 3, 0))

    decoded = decoder.decode(_encode(image, "PNG")).unwrap()

    assert decoded.alpha is not None
    assert decoded.alpha.shape == (2, 2)
    assert decoded.alpha[0][0] == 0
    assert decoded.alpha[1][1] == 128
    assert decoded.rgb.shape == (2, 2, 3)


def test_palette_png_is_converted_to_rgb(decoder: PillowImageDecoder) -> None:
    image = Image.new("P", (3, 3))

    decoded = decoder.decode(_encode(image, "PNG")).unwrap()

    assert decoded.rgb.shape == (3, 3, 3)
    assert decoded.media_type is MediaType.PNG


def test_grayscale_png_is_converted_to_rgb(decoder: PillowImageDecoder) -> None:
    image = Image.new("L", (2, 5), color=90)

    decoded = decoder.decode(_encode(image, "PNG")).unwrap()

    assert decoded.rgb.shape == (5, 2, 3)
    assert int(decoded.rgb[0][0][0]) == 90


def test_empty_upload_is_rejected(decoder: PillowImageDecoder) -> None:
    assert decoder.decode(b"").unwrap_failure().code is FailureCode.EMPTY_FILE


def test_oversized_upload_is_rejected_before_decode(decoder: PillowImageDecoder) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_UPLOAD_BYTES

    assert decoder.decode(payload).unwrap_failure().code is FailureCode.BYTE_LIMIT


def test_unsupported_format_is_rejected(decoder: PillowImageDecoder) -> None:
    image = Image.new("RGB", (4, 4), color=(0, 0, 0))

    failure = decoder.decode(_encode(image, "BMP")).unwrap_failure()

    assert failure.code is FailureCode.UNSUPPORTED_FORMAT


def test_non_image_bytes_are_rejected(decoder: PillowImageDecoder) -> None:
    failure = decoder.decode(b"this is not an image").unwrap_failure()

    assert failure.code is FailureCode.UNSUPPORTED_FORMAT


def test_truncated_png_is_a_decode_failure(decoder: PillowImageDecoder) -> None:
    complete = _encode(Image.new("RGB", (64, 64), color=(5, 5, 5)), "PNG")

    failure = decoder.decode(complete[: len(complete) // 2]).unwrap_failure()

    assert failure.code is FailureCode.DECODE_FAILURE


def test_pixel_limit_is_enforced(
    decoder: PillowImageDecoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_bytes = _encode(Image.new("RGB", (10, 10), color=(1, 1, 1)), "PNG")

    def oversized_header(
        _self: PillowImageDecoder, _data: bytes, _operation: str
    ) -> Result[tuple[MediaType, int, int]]:
        """Report oversized dimensions without allocating any pixels."""
        return ok((MediaType.PNG, 10_000, 4_001))

    monkeypatch.setattr(PillowImageDecoder, "_inspect_header", oversized_header)

    failure = decoder.decode(image_bytes).unwrap_failure()

    assert failure.code is FailureCode.PIXEL_LIMIT


def test_failures_carry_no_library_detail(decoder: PillowImageDecoder) -> None:
    failure = decoder.decode(b"not an image").unwrap_failure()

    assert failure.safe_detail is None
    assert failure.operation == "decode_image"
