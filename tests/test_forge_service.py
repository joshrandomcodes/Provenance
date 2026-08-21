"""The Forge workflow end to end, against a real registry, decoder, and encoder.

Requirements: 2.1-2.6, 3.1-3.8, 4.1-4.11, 5.1-5.7
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import numpy as np
import pytest
from PIL import Image

from provenance.application.forge import (
    DOWNLOAD_SUFFIX,
    ForgeArtifact,
    ForgeService,
    download_name_for,
    sanitize_download_stem,
)
from provenance.domain.errors import FailureCode
from provenance.domain.models import CreatorId, CreatorMetadata, MediaType
from provenance.domain.validation import (
    FIELD_CONTACT_EMAIL,
    FIELD_CREATOR_ID,
    FIELD_DISPLAY_NAME,
    FIELD_FILE,
)
from provenance.domain.watermark import extract, payload_capacity
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.png_codec import PillowPngEncoder
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter
from tests.registry_support import RegistryHarness, temporary_registry

pytestmark = pytest.mark.integration

CREATOR = CreatorId("studio.one")
OTHER_CREATOR = CreatorId("studio.two")


class FixedClock:
    """Deterministic clock."""

    def __init__(self) -> None:
        self.utc_calls = 0

    def utc_now(self) -> datetime:
        self.utc_calls += 1
        return datetime(2026, 4, 5, 6, 7, 8, 900_000, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _metadata(creator: CreatorId = CREATOR, **overrides: object) -> CreatorMetadata:
    values: dict[str, object] = {
        "creator_id": creator,
        "display_name": "Studio One",
        "contact_email": "studio@example.com",
        "postal_address": None,
        "rights_statement": None,
    }
    values.update(overrides)
    return CreatorMetadata(**values)  # type: ignore[arg-type]


def _png(width: int = 200, height: int = 3, alpha: bool = False) -> bytes:
    generator = np.random.default_rng(seed=11)
    rgb = generator.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    if alpha:
        channel = np.full((height, width, 1), 123, dtype=np.uint8)
        image = Image.fromarray(np.concatenate([rgb, channel], axis=2), mode="RGBA")
    else:
        image = Image.fromarray(rgb, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(width: int = 200, height: int = 3) -> bytes:
    image = Image.new("RGB", (width, height), color=(120, 60, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _service(harness: RegistryHarness, clock: FixedClock | None = None) -> ForgeService:
    return ForgeService(
        decoder=PillowImageDecoder(),
        encoder=PillowPngEncoder(),
        registry=harness.adapter,
        clock=clock or FixedClock(),
    )


def test_forge_registers_and_returns_a_downloadable_watermark() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        outcome = service.forge(_png(), "artwork.png", _metadata()).unwrap()

        assert outcome.created is True
        assert outcome.download_available is True
        assert outcome.download_name == "artwork.provenance.png"
        assert outcome.asset.creator_id == CREATOR
        assert outcome.asset.registered_at == "2026-04-05T06:07:08Z"
        assert outcome.asset.source_media_type is MediaType.PNG
        assert outcome.payload_bytes <= outcome.capacity_bytes
        assert harness.count("registered_assets") == 1


def test_the_downloaded_png_carries_the_registered_payload() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        outcome = service.forge(_png(), "artwork.png", _metadata()).unwrap()

        decoded = PillowImageDecoder().decode(outcome.png_bytes).unwrap()
        extracted = extract(decoded.rgb).unwrap()

        assert extracted == outcome.payload
        assert extracted.asset_hash == outcome.asset.asset_hash
        assert extracted.creator_id == outcome.asset.creator_id


def test_prepare_touches_no_registry_state() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        before = harness.snapshot()

        artifact = service.prepare(_png(), "artwork.png", _metadata()).unwrap()

        assert isinstance(artifact, ForgeArtifact)
        assert harness.snapshot() == before
        assert harness.count("registered_assets") == 0


def test_payload_timestamp_is_sampled_once_during_prepare() -> None:
    with temporary_registry() as harness:
        clock = FixedClock()
        service = _service(harness, clock)

        artifact = service.prepare(_png(), "artwork.png", _metadata()).unwrap()

        assert clock.utc_calls == 1
        assert artifact.payload.created_at == "2026-04-05T06:07:08Z"


def test_reregistering_the_same_image_reuses_the_record() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        data = _png()

        first = service.forge(data, "artwork.png", _metadata()).unwrap()
        second = service.forge(data, "artwork-copy.png", _metadata()).unwrap()

        assert first.created is True
        assert second.created is False
        assert second.asset.asset_hash == first.asset.asset_hash
        assert second.asset.registered_at == first.asset.registered_at
        # A reused registration still yields a download, named from the new upload.
        assert second.download_available is True
        assert second.download_name == "artwork-copy.provenance.png"
        assert harness.count("registered_assets") == 1


def test_a_different_creator_conflicts_and_offers_no_download() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        data = _png()
        service.forge(data, "artwork.png", _metadata()).unwrap()
        snapshot = harness.snapshot()

        result = service.forge(data, "artwork.png", _metadata(OTHER_CREATOR))

        assert result.value is None
        assert result.unwrap_failure().code is FailureCode.IDENTITY_CONFLICT
        assert harness.snapshot() == snapshot


def test_invalid_metadata_reports_every_field_without_decoding() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        result = service.prepare(
            _png(),
            "artwork.png",
            CreatorMetadata(
                creator_id=CreatorId("bad id"),
                display_name="",
                contact_email="nope",
            ),
        )
        failure = result.unwrap_failure()

        assert {issue.field_key for issue in failure.fields} == {
            FIELD_CREATOR_ID,
            FIELD_DISPLAY_NAME,
            FIELD_CONTACT_EMAIL,
        }
        assert harness.count("registered_assets") == 0


def test_empty_upload_is_rejected() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        failure = service.prepare(b"", "artwork.png", _metadata()).unwrap_failure()

        assert failure.code is FailureCode.EMPTY_FILE
        assert failure.fields[0].field_key == FIELD_FILE


def test_unsupported_format_is_rejected() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        buffer = io.BytesIO()
        Image.new("RGB", (40, 40), color=(1, 2, 3)).save(buffer, format="BMP")

        failure = service.prepare(buffer.getvalue(), "artwork.bmp", _metadata()).unwrap_failure()

        assert failure.code is FailureCode.UNSUPPORTED_FORMAT


def test_an_image_too_small_for_the_payload_reports_exact_counts() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        failure = service.prepare(_png(width=4, height=4), "tiny.png", _metadata()).unwrap_failure()

        assert failure.code is FailureCode.CAPACITY_EXCEEDED
        assert failure.safe_detail is not None
        assert f"available_bytes={payload_capacity(4, 4)}" in failure.safe_detail
        assert harness.count("registered_assets") == 0


def test_jpeg_sources_produce_png_output_and_record_the_source_type() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        outcome = service.forge(_jpeg(), "photo.jpg", _metadata()).unwrap()

        assert outcome.asset.source_media_type is MediaType.JPEG
        assert outcome.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert outcome.download_name == "photo.provenance.png"


def test_alpha_is_preserved_through_the_workflow() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        outcome = service.forge(_png(alpha=True), "layered.png", _metadata()).unwrap()

        decoded = PillowImageDecoder().decode(outcome.png_bytes).unwrap()
        assert decoded.alpha is not None
        assert int(decoded.alpha.min()) == 123
        assert int(decoded.alpha.max()) == 123
        assert extract(decoded.rgb).unwrap() == outcome.payload


def test_a_failing_encoder_prevents_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        def failing_encode(
            _self: PillowPngEncoder, _rgb: np.ndarray, _alpha: np.ndarray | None
        ) -> bytes:
            raise OSError("encoder unavailable")

        monkeypatch.setattr(PillowPngEncoder, "_encode", failing_encode)

        result = service.prepare(_png(), "artwork.png", _metadata())

        assert result.unwrap_failure().code is FailureCode.PNG_ROUNDTRIP_FAILED
        assert harness.count("registered_assets") == 0


def test_a_closed_write_gate_blocks_registration(tmp_path_factory: pytest.TempPathFactory) -> None:
    path = tmp_path_factory.mktemp("closed") / "registry.sqlite3"
    path.write_bytes(b"not a database")
    registry = SqliteRegistry(path)
    registry.initialize()
    service = ForgeService(
        decoder=PillowImageDecoder(),
        encoder=PillowPngEncoder(),
        registry=SqliteRegistryAdapter(registry),
        clock=FixedClock(),
    )

    result = service.forge(_png(), "artwork.png", _metadata())

    assert result.value is None
    assert result.unwrap_failure().code is FailureCode.CHECKS_FAILED


def test_registration_reports_capacity_utilisation() -> None:
    with temporary_registry() as harness:
        service = _service(harness)

        # 600x1 gives 212 bytes of capacity, comfortably above the ~143 byte payload.
        outcome = service.forge(_png(width=600, height=1), "artwork.png", _metadata()).unwrap()

        assert outcome.capacity_bytes == payload_capacity(600, 1)
        assert outcome.payload_bytes == len(
            f'{{"asset_hash":"{outcome.asset.asset_hash}",'
            f'"created_at":"{outcome.payload.created_at}",'
            f'"creator_id":"{CREATOR}"}}'.encode()
        )


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("artwork.png", "artwork"),
        ("artwork.tar.gz", "artwork.tar"),
        ("photo.JPG", "photo"),
        ("..\\..\\evil.png", "evil"),
        ("/absolute/path/piece.png", "piece"),
        ("C:\\Users\\jo\\my art.png", "my art"),
        ("caf\u00e9.png", "caf\u00e9"),
        ("", "asset"),
        # A leading dot marks a hidden file, so ".png" is a name rather than a suffix.
        (".png", "png"),
        ("...", "asset"),
        ("a" * 200 + ".png", "a" * 80),
        ("we?rd:na*me.png", "werdname"),
    ],
)
def test_download_stems_are_sanitized(file_name: str, expected: str) -> None:
    assert sanitize_download_stem(file_name) == expected
    assert download_name_for(file_name) == f"{expected}{DOWNLOAD_SUFFIX}"


def test_download_names_never_contain_separators_or_control_characters() -> None:
    hostile = "../../etc/pa\x00ss\nwd.png"

    name = download_name_for(hostile)

    assert "/" not in name
    assert "\\" not in name
    assert "\x00" not in name
    assert "\n" not in name
    assert name.endswith(DOWNLOAD_SUFFIX)
