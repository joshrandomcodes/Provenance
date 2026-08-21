"""The Forge workflow: validate, watermark, verify, then register atomically.

``prepare`` performs every local computation and touches no database. ``register``
commits the registration and is the only path that can make a download available, so
an unregistered artifact can never be offered for download.

Requirements: 2.1-2.6, 3.1-3.8, 4.1-4.11, 5.1-5.7, 17.2, 17.5
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from provenance.domain.canonical_image import asset_hash_for_source
from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    AssetHash,
    CreatorMetadata,
    DecodedSource,
    MediaType,
    RegisterAsset,
    RegisteredAsset,
    UploadMetadata,
    WatermarkPayload,
)
from provenance.domain.payload import create_payload, serialize_payload
from provenance.domain.time import Clock, now_timestamp
from provenance.domain.validation import validate_forge_submission
from provenance.domain.watermark import capacity_report, embed
from provenance.ports.images import ImageDecoderPort
from provenance.ports.png import PngEncoderPort
from provenance.ports.registry import RegistryPort

PREPARE_OPERATION: Final = "forge_prepare"
REGISTER_OPERATION: Final = "forge_register"

DOWNLOAD_SUFFIX: Final = ".provenance.png"
FALLBACK_STEM: Final = "asset"
MAX_STEM_LENGTH: Final = 80

# Characters that must never reach a download filename.
_FORBIDDEN_NAME_CHARACTERS: Final = frozenset('/\\:*?"<>|')

DETAIL_RECORD_MISMATCH: Final = "registry_record_mismatch"


def sanitize_download_stem(file_name: str) -> str:
    """Reduce an uploaded filename to a safe stem.

    Directory components, path separators, reserved characters, and control
    characters are removed. Other characters, including non-ASCII letters, are kept so
    the download still resembles the creator's filename.
    """
    last_segment = PurePosixPath(file_name.replace("\\", "/")).name
    stem = PurePosixPath(last_segment).stem if "." in last_segment else last_segment
    cleaned = "".join(
        character
        for character in stem
        if character not in _FORBIDDEN_NAME_CHARACTERS
        and unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
    )
    trimmed = cleaned.strip().strip(".").strip()
    return trimmed[:MAX_STEM_LENGTH] or FALLBACK_STEM


def download_name_for(file_name: str) -> str:
    """Build the watermarked download filename for an uploaded file."""
    return f"{sanitize_download_stem(file_name)}{DOWNLOAD_SUFFIX}"


@dataclass(frozen=True, slots=True)
class ForgeArtifact:
    """A watermarked, verified image held in memory before registration.

    Callers must discard this object when registration fails so no unregistered
    download is ever exposed.
    """

    asset_hash: AssetHash
    payload: WatermarkPayload
    png_bytes: bytes
    download_name: str
    width: int
    height: int
    source_media_type: MediaType
    payload_bytes: int
    capacity_bytes: int
    metadata: CreatorMetadata


@dataclass(frozen=True, slots=True)
class ForgeOutcome:
    """A registered asset together with its downloadable watermarked image."""

    asset: RegisteredAsset
    created: bool
    png_bytes: bytes
    download_name: str
    payload: WatermarkPayload
    payload_bytes: int
    capacity_bytes: int

    @property
    def download_available(self) -> bool:
        """True only when registration produced a matching record and PNG bytes."""
        return len(self.png_bytes) > 0


class ForgeService:
    """Composes validation, watermarking, verification, and registration."""

    __slots__ = ("_decoder", "_encoder", "_registry", "_clock")

    def __init__(
        self,
        decoder: ImageDecoderPort,
        encoder: PngEncoderPort,
        registry: RegistryPort,
        clock: Clock,
    ) -> None:
        self._decoder = decoder
        self._encoder = encoder
        self._registry = registry
        self._clock = clock

    def prepare(
        self, data: bytes, file_name: str, metadata: CreatorMetadata
    ) -> Result[ForgeArtifact]:
        """Validate and watermark locally. Performs no Registry access."""
        report = validate_forge_submission(
            UploadMetadata(file_name=file_name, byte_count=len(data)), metadata
        )
        if not report.is_valid:
            return failed(report.issues[0].code, PREPARE_OPERATION, fields=report.issues)

        decoded = self._decoder.decode(data, operation=PREPARE_OPERATION)
        if decoded.failure is not None:
            return Result(failure=decoded.failure)
        source = decoded.unwrap()

        asset_hash = asset_hash_for_source(source)
        payload = create_payload(asset_hash, metadata.creator_id, self._clock)
        serialized = serialize_payload(payload)
        if serialized.failure is not None:
            return Result(failure=serialized.failure)
        payload_bytes = serialized.unwrap()

        return self._embed_and_encode(source, payload, payload_bytes, file_name, metadata)

    def _embed_and_encode(
        self,
        source: DecodedSource,
        payload: WatermarkPayload,
        payload_bytes: bytes,
        file_name: str,
        metadata: CreatorMetadata,
    ) -> Result[ForgeArtifact]:
        report = capacity_report(source.width, source.height, payload_bytes)
        embedded = embed(source.rgb, source.alpha, payload_bytes)
        if embedded.failure is not None:
            return Result(failure=embedded.failure)
        image = embedded.unwrap()

        encoded = self._encoder.encode_verified(image.rgb, image.alpha, operation=PREPARE_OPERATION)
        if encoded.failure is not None:
            return Result(failure=encoded.failure)

        return ok(
            ForgeArtifact(
                asset_hash=payload.asset_hash,
                payload=payload,
                png_bytes=encoded.unwrap(),
                download_name=download_name_for(file_name),
                width=image.width,
                height=image.height,
                source_media_type=source.media_type,
                payload_bytes=report.required_bytes,
                capacity_bytes=report.available_bytes,
                metadata=metadata,
            )
        )

    def register(self, artifact: ForgeArtifact) -> Result[ForgeOutcome]:
        """Register the artifact atomically, then expose it for download."""
        begun = self._registry.begin(REGISTER_OPERATION)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        command = RegisterAsset(
            asset_hash=artifact.asset_hash,
            creator_id=artifact.metadata.creator_id,
            registered_at=now_timestamp(self._clock),
            width=artifact.width,
            height=artifact.height,
            source_media_type=artifact.source_media_type,
            metadata=artifact.metadata,
        )

        with begun.unwrap() as uow:
            registered = uow.assets.register_or_reuse(command)
            if registered.failure is not None:
                return Result(failure=registered.failure)

            outcome = registered.unwrap()
            asset = outcome.asset
            if (
                asset.asset_hash != artifact.payload.asset_hash
                or asset.creator_id != artifact.payload.creator_id
            ):
                return failed(
                    FailureCode.IDENTITY_CONFLICT,
                    REGISTER_OPERATION,
                    safe_detail=DETAIL_RECORD_MISMATCH,
                )

            uow.commit()

        return ok(
            ForgeOutcome(
                asset=asset,
                created=outcome.created,
                png_bytes=artifact.png_bytes,
                download_name=artifact.download_name,
                payload=artifact.payload,
                payload_bytes=artifact.payload_bytes,
                capacity_bytes=artifact.capacity_bytes,
            )
        )

    def forge(self, data: bytes, file_name: str, metadata: CreatorMetadata) -> Result[ForgeOutcome]:
        """Prepare and register in one call, discarding the artifact on any failure."""
        prepared = self.prepare(data, file_name, metadata)
        if prepared.failure is not None:
            return Result(failure=prepared.failure)
        return self.register(prepared.unwrap())
