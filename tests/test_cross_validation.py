"""Registry cross-validation: two facts must agree before an incident exists.

Requirements: 9.5, 10.1-10.7, 18.2, 18.8, 18.9, 20.20
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from provenance.application.cross_validation import (
    DETAIL_CREATOR_MISMATCH,
    DETAIL_NOT_REGISTERED,
    DetectionCrossValidator,
    DetectionOutcome,
)
from provenance.domain.models import (
    AssetHash,
    CreatorId,
    ExtractionEvidence,
    ExtractionKind,
    IncidentStatus,
    MarkFairUse,
    NormalizedUrl,
    OperationKey,
    PageContext,
    WatermarkPayload,
)
from provenance.domain.time import Clock, UtcTimestamp
from tests.registry_support import RegistryHarness, seed_asset, temporary_registry

pytestmark = pytest.mark.integration

HASH = AssetHash("c" * 64)
OTHER_HASH = AssetHash("d" * 64)
CREATOR = CreatorId("studio.one")
OTHER_CREATOR = CreatorId("studio.two")

PAGE = NormalizedUrl("https://shop.example/product")
OTHER_PAGE = NormalizedUrl("https://shop.example/other")
IMAGE = NormalizedUrl("https://cdn.example/a.png")
OTHER_IMAGE = NormalizedUrl("https://cdn.example/b.png")

PAYLOAD_AT = UtcTimestamp("2026-01-02T03:04:05Z")
CONTEXT = PageContext(title="Shop", heading="Prints", ecommerce_evidence=("Add to cart",))
CRC = 987_654


class SteppingClock:
    """Clock that advances one second per wall-clock reading."""

    def __init__(self) -> None:
        self._value = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)

    def utc_now(self) -> datetime:
        current = self._value
        self._value += timedelta(seconds=1)
        return current

    def monotonic(self) -> float:
        return 0.0


class FixedClock:
    """Clock that never advances, so repeated detections share one second."""

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _payload(asset_hash: AssetHash = HASH, creator_id: CreatorId = CREATOR) -> WatermarkPayload:
    return WatermarkPayload(asset_hash=asset_hash, creator_id=creator_id, created_at=PAYLOAD_AT)


def _extracted(asset_hash: AssetHash = HASH, creator_id: CreatorId = CREATOR) -> ExtractionEvidence:
    """Evidence exactly as the analyzer produces it: valid payload, not yet checked."""
    return ExtractionEvidence(
        kind=ExtractionKind.UNREGISTERED, payload=_payload(asset_hash, creator_id), crc32=CRC
    )


def _cross_validate(
    harness: RegistryHarness,
    evidence: ExtractionEvidence,
    *,
    page_url: NormalizedUrl = PAGE,
    image_url: NormalizedUrl = IMAGE,
    clock: Clock | None = None,
) -> DetectionOutcome:
    validator = DetectionCrossValidator(harness.adapter, clock or SteppingClock())
    return validator.cross_validate(
        evidence, page_url=page_url, image_url=image_url, context=CONTEXT
    ).unwrap()


def _mark_fair_use(harness: RegistryHarness, page_url: NormalizedUrl, key: str) -> None:
    with harness.adapter.begin("whitelist").unwrap() as uow:
        uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=HASH,
                page_url=page_url,
                rationale="Editorial commentary.",
                at=UtcTimestamp("2026-08-20T09:00:00Z"),
                operation_key=OperationKey(key.rjust(64, "0")),
            )
        ).unwrap()
        uow.commit()


@pytest.mark.parametrize(
    "kind",
    [ExtractionKind.NO_WATERMARK, ExtractionKind.CORRUPT_WATERMARK, ExtractionKind.FAILED],
)
def test_an_extraction_without_a_payload_creates_no_incident(kind: ExtractionKind) -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        outcome = _cross_validate(harness, ExtractionEvidence(kind=kind, detail="why"))

        assert outcome.kind is kind
        assert outcome.incident is None
        assert outcome.is_verified is False
        assert harness.count("incidents") == 0


def test_a_payload_for_an_unregistered_asset_creates_no_incident() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        outcome = _cross_validate(harness, _extracted(asset_hash=OTHER_HASH))

        assert outcome.kind is ExtractionKind.UNREGISTERED
        assert outcome.detail == DETAIL_NOT_REGISTERED
        assert outcome.incident is None
        assert harness.count("incidents") == 0


def test_a_payload_naming_a_different_creator_creates_no_incident() -> None:
    """Asset_Hash alone is not enough: Creator_ID must match the registered owner."""
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        outcome = _cross_validate(harness, _extracted(creator_id=OTHER_CREATOR))

        assert outcome.kind is ExtractionKind.UNREGISTERED
        assert outcome.detail == DETAIL_CREATOR_MISMATCH
        assert harness.count("incidents") == 0


def test_an_empty_registry_yields_unregistered() -> None:
    with temporary_registry() as harness:
        outcome = _cross_validate(harness, _extracted())

        assert outcome.kind is ExtractionKind.UNREGISTERED
        assert harness.count("incidents") == 0


def test_a_full_identity_match_commits_one_detected_incident() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        outcome = _cross_validate(harness, _extracted())

        assert outcome.kind is ExtractionKind.VERIFIED
        assert outcome.is_verified is True
        incident = outcome.incident
        assert incident is not None
        assert incident.status is IncidentStatus.DETECTED
        assert (incident.asset_hash, incident.page_url, incident.image_url) == (HASH, PAGE, IMAGE)
        assert harness.count("incidents") == 1


def test_the_committed_incident_carries_the_extraction_evidence() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        incident = _cross_validate(harness, _extracted()).incident

        assert incident is not None
        assert incident.extraction_crc32 == CRC
        assert incident.payload_created_at == PAYLOAD_AT
        assert incident.creator_id_evidence == CREATOR
        assert incident.context.title == "Shop"
        assert incident.context.ecommerce_evidence == ("Add to cart",)


def test_first_and_last_seen_are_equal_on_first_detection() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        incident = _cross_validate(harness, _extracted()).incident

        assert incident is not None
        assert incident.first_seen_at == incident.last_seen_at


def test_a_detection_appends_exactly_one_audit_event() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        before = harness.count("audit_events")

        _cross_validate(harness, _extracted())

        assert harness.count("audit_events") == before + 1


def test_rediscovery_keeps_one_incident_and_moves_only_last_seen() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        clock = SteppingClock()

        first = _cross_validate(harness, _extracted(), clock=clock).incident
        second = _cross_validate(harness, _extracted(), clock=clock).incident

        assert first is not None
        assert second is not None
        assert harness.count("incidents") == 1
        assert second.id == first.id
        assert second.first_seen_at == first.first_seen_at
        assert second.last_seen_at > first.last_seen_at


def test_a_repeated_detection_in_the_same_second_adds_no_second_audit() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        clock = FixedClock()
        before = harness.count("audit_events")

        _cross_validate(harness, _extracted(), clock=clock)
        _cross_validate(harness, _extracted(), clock=clock)

        assert harness.count("incidents") == 1
        assert harness.count("audit_events") == before + 1


def test_different_images_on_one_page_are_separate_incidents() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        clock = SteppingClock()

        _cross_validate(harness, _extracted(), image_url=IMAGE, clock=clock)
        _cross_validate(harness, _extracted(), image_url=OTHER_IMAGE, clock=clock)

        assert harness.count("incidents") == 2


def test_the_same_image_on_different_pages_are_separate_incidents() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        clock = SteppingClock()

        _cross_validate(harness, _extracted(), page_url=PAGE, clock=clock)
        _cross_validate(harness, _extracted(), page_url=OTHER_PAGE, clock=clock)

        assert harness.count("incidents") == 2


def test_a_whitelisted_page_yields_a_fair_use_incident() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        _mark_fair_use(harness, PAGE, "1")

        incident = _cross_validate(harness, _extracted()).incident

        assert incident is not None
        assert incident.status is IncidentStatus.FAIR_USE


def test_the_whitelist_scope_is_exact_to_the_page() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        _mark_fair_use(harness, PAGE, "1")
        clock = SteppingClock()

        whitelisted = _cross_validate(harness, _extracted(), page_url=PAGE, clock=clock)
        other = _cross_validate(harness, _extracted(), page_url=OTHER_PAGE, clock=clock)

        assert whitelisted.incident is not None
        assert other.incident is not None
        assert whitelisted.incident.status is IncidentStatus.FAIR_USE
        assert other.incident.status is IncidentStatus.DETECTED


def test_a_rejected_detection_leaves_the_registry_byte_identical() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        before = harness.snapshot()

        _cross_validate(harness, _extracted(asset_hash=OTHER_HASH))
        _cross_validate(harness, _extracted(creator_id=OTHER_CREATOR))
        _cross_validate(harness, ExtractionEvidence(kind=ExtractionKind.CORRUPT_WATERMARK))

        assert harness.snapshot() == before


def test_a_later_nonverified_image_preserves_an_earlier_committed_incident() -> None:
    with temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)
        clock = SteppingClock()

        _cross_validate(harness, _extracted(), image_url=IMAGE, clock=clock)
        _cross_validate(
            harness,
            ExtractionEvidence(kind=ExtractionKind.NO_WATERMARK),
            image_url=OTHER_IMAGE,
            clock=clock,
        )

        assert harness.incident_keys() == {(str(HASH), str(PAGE), str(IMAGE))}
