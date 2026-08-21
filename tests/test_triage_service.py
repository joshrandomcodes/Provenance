"""Incident triage: evidence reads, previews, and confirmed fair-use decisions.

Requirements: 11.1-11.9, 12.1-12.8, 18.6-18.8
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from provenance.application.operations import MaterialActionRunner
from provenance.application.triage import (
    DETAIL_PREVIEW_CHANGED,
    DETAIL_SOURCE_NOT_STORED,
    DETAIL_TARGET_NOT_STORED,
    TriageAction,
    TriageService,
    WhitelistEffect,
)
from provenance.domain.errors import FailureCode
from provenance.domain.models import (
    MAX_RATIONALE_CODE_POINTS,
    AssetHash,
    AuditEventType,
    CreatorId,
    IncidentStatus,
    NormalizedUrl,
)
from tests.registry_support import (
    RegistryHarness,
    detection,
    seed_asset,
    temporary_registry,
)

pytestmark = pytest.mark.integration

HASH = AssetHash("a" * 64)
OTHER_HASH = AssetHash("b" * 64)
CREATOR = CreatorId("studio.one")
PAGE = NormalizedUrl("https://shop.example.com/prints/Sunrise")
OTHER_PAGE = NormalizedUrl("https://shop.example.com/prints/sunrise")
IMAGE = NormalizedUrl("https://cdn.example.com/a.png")
SECOND_IMAGE = NormalizedUrl("https://cdn.example.com/b.png")

RATIONALE = "Reviewed and acceptable: a small editorial thumbnail with credit."


class FixedClock:
    """Deterministic clock."""

    def utc_now(self) -> datetime:
        return datetime(2026, 9, 10, 11, 12, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _service(harness: RegistryHarness) -> TriageService:
    clock = FixedClock()
    return TriageService(harness.adapter, MaterialActionRunner(harness.adapter, clock), clock)


def _seed_incident(
    harness: RegistryHarness,
    *,
    asset_hash: AssetHash = HASH,
    page_url: NormalizedUrl = PAGE,
    image_url: NormalizedUrl = IMAGE,
) -> int:
    seed_asset(harness, asset_hash, CREATOR)
    with harness.adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(
            detection(asset_hash, CREATOR, page_url, image_url)
        ).unwrap()
        uow.commit()
    return incident.id


def _mark(service: TriageService, incident_id: int, rationale: str = RATIONALE) -> None:
    preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, rationale).unwrap()
    service.confirm(preview).unwrap()


# Reads ---------------------------------------------------------------------------------


def test_a_new_incident_is_active_and_not_fair_use() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        snapshot = _service(harness).load().unwrap()

        assert [incident.id for incident in snapshot.active] == [incident_id]
        assert snapshot.fair_use == ()
        assert snapshot.is_empty is False


def test_an_empty_registry_reports_nothing_to_triage() -> None:
    with temporary_registry() as harness:
        snapshot = _service(harness).load().unwrap()

        assert snapshot.is_empty is True


def test_evidence_carries_the_incident_registration_and_exact_scope() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        evidence = _service(harness).evidence(incident_id).unwrap()

        assert evidence.incident.id == incident_id
        assert evidence.asset is not None
        assert evidence.asset.creator_id == CREATOR
        assert evidence.whitelist is None
        assert [member.id for member in evidence.scope] == [incident_id]
        assert evidence.is_fair_use is False
        assert evidence.available_actions == (TriageAction.MARK_FAIR_USE,)


def test_neither_image_representation_is_available_and_each_says_why() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        evidence = _service(harness).evidence(incident_id).unwrap()

        assert evidence.source_unavailable_detail == DETAIL_SOURCE_NOT_STORED
        assert evidence.target_unavailable_detail == DETAIL_TARGET_NOT_STORED


def test_scope_gathers_every_incident_on_the_same_asset_and_page() -> None:
    with temporary_registry() as harness:
        first = _seed_incident(harness)
        second = _seed_incident(harness, image_url=SECOND_IMAGE)

        evidence = _service(harness).evidence(first).unwrap()

        assert [member.id for member in evidence.scope] == sorted([first, second])


def test_unknown_incidents_are_reported_as_missing() -> None:
    with temporary_registry() as harness:
        result = _service(harness).evidence(4242)

        assert result.unwrap_failure().code is FailureCode.NOT_FOUND


# Previews ------------------------------------------------------------------------------


def test_a_mark_preview_states_the_before_after_and_effects() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        preview = (
            _service(harness).preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()
        )

        assert preview.current_status is IncidentStatus.DETECTED
        assert preview.proposed_status is IncidentStatus.FAIR_USE
        assert preview.affected_incident_ids == (incident_id,)
        assert preview.whitelist_effect is WhitelistEffect.CREATED
        assert preview.audit_event_type is AuditEventType.FAIR_USE_MARKED
        assert preview.rationale == RATIONALE
        assert len(preview.fingerprint) == 64
        assert preview.changes_nothing is False


def test_a_preview_writes_nothing() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        before = harness.snapshot()

        service.preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()

        assert harness.snapshot() == before


def test_a_preview_covers_every_incident_in_the_exact_scope() -> None:
    with temporary_registry() as harness:
        first = _seed_incident(harness)
        second = _seed_incident(harness, image_url=SECOND_IMAGE)

        preview = _service(harness).preview(first, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()

        assert preview.affected_incident_ids == tuple(sorted([first, second]))


@pytest.mark.parametrize(
    ("rationale", "code"),
    [
        ("", FailureCode.MISSING_FIELD),
        ("x" * (MAX_RATIONALE_CODE_POINTS + 1), FailureCode.FIELD_TOO_LONG),
        ("has a \x00 inside", FailureCode.FORBIDDEN_CHARACTER),
    ],
)
def test_an_invalid_rationale_blocks_the_preview_and_writes_nothing(
    rationale: str, code: FailureCode
) -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        before = harness.snapshot()

        result = service.preview(incident_id, TriageAction.MARK_FAIR_USE, rationale)

        failure = result.unwrap_failure()
        assert failure.code is code
        assert failure.fields[0].field_key == "fair_use_rationale"
        assert harness.snapshot() == before


def test_a_rationale_at_the_maximum_length_is_accepted() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        preview = (
            _service(harness)
            .preview(incident_id, TriageAction.MARK_FAIR_USE, "x" * MAX_RATIONALE_CODE_POINTS)
            .unwrap()
        )

        assert preview.proposed_status is IncidentStatus.FAIR_USE


def test_removal_cannot_be_previewed_without_an_entry() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)

        result = _service(harness).preview(incident_id, TriageAction.REMOVE_FAIR_USE)

        assert result.unwrap_failure().code is FailureCode.NOT_FOUND


# Confirmed decisions -------------------------------------------------------------------


def test_marking_fair_use_commits_status_entry_audit_and_receipt_together() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)

        preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()
        outcome = service.confirm(preview).unwrap()

        assert outcome.replayed is False
        assert outcome.committed_at == "2026-09-10T11:12:13Z"
        assert outcome.audit_event_id is not None
        assert [transition.incident_id for transition in outcome.transitions] == [incident_id]

        assert harness.count("whitelist_entries") == 1
        assert harness.count("operation_receipts") == 1
        with harness.adapter.begin("read").unwrap() as uow:
            stored = uow.incidents.get(incident_id)
        assert stored is not None
        assert stored.status is IncidentStatus.FAIR_USE


def test_a_marked_incident_leaves_the_active_view_for_the_fair_use_view() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)

        _mark(service, incident_id)
        snapshot = service.load().unwrap()

        assert snapshot.active == ()
        assert [incident.id for incident in snapshot.fair_use] == [incident_id]


def test_confirming_the_same_decision_twice_changes_nothing_the_second_time() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()

        first = service.confirm(preview).unwrap()
        after_first = harness.snapshot()
        second = service.confirm(preview).unwrap()

        assert first.replayed is False
        assert second.replayed is True
        assert second.committed_at == first.committed_at
        assert harness.snapshot() == after_first


def test_a_confirmation_bound_to_stale_details_is_refused() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()
        tampered = replace(preview, rationale="a different rationale than was reviewed")
        before = harness.snapshot()

        result = service.confirm(tampered)

        failure = result.unwrap_failure()
        assert failure.code is FailureCode.STALE_CONFIRMATION
        assert failure.safe_detail == DETAIL_PREVIEW_CHANGED
        assert harness.snapshot() == before


def test_a_confirmation_goes_stale_when_the_incident_moves_first() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, RATIONALE).unwrap()

        # A second incident appears in the same exact scope after the review was built,
        # so the effects the creator saw no longer describe what would happen.
        _seed_incident(harness, image_url=SECOND_IMAGE)
        result = service.confirm(preview)

        assert result.unwrap_failure().code is FailureCode.STALE_CONFIRMATION
        assert harness.count("whitelist_entries") == 0


def test_marking_the_same_scope_again_updates_one_entry_without_moving_a_status() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)
        _mark(service, incident_id)

        preview = service.preview(incident_id, TriageAction.MARK_FAIR_USE, "Revised note").unwrap()
        assert preview.whitelist_effect is WhitelistEffect.UPDATED
        assert preview.changes_nothing is True

        service.confirm(preview).unwrap()

        assert harness.count("whitelist_entries") == 1
        evidence = service.evidence(incident_id).unwrap()
        assert evidence.whitelist is not None
        assert evidence.whitelist.rationale == "Revised note"


def test_fair_use_applies_only_to_the_exact_page_address() -> None:
    with temporary_registry() as harness:
        marked = _seed_incident(harness)
        # Same asset, same host, different path case only.
        untouched = _seed_incident(harness, page_url=OTHER_PAGE)
        service = _service(harness)

        _mark(service, marked)

        with harness.adapter.begin("read").unwrap() as uow:
            other = uow.incidents.get(untouched)
        assert other is not None
        assert other.status is IncidentStatus.DETECTED


def test_fair_use_applies_only_to_the_exact_asset() -> None:
    with temporary_registry() as harness:
        marked = _seed_incident(harness)
        untouched = _seed_incident(harness, asset_hash=OTHER_HASH)
        service = _service(harness)

        _mark(service, marked)

        with harness.adapter.begin("read").unwrap() as uow:
            other = uow.incidents.get(untouched)
        assert other is not None
        assert other.status is IncidentStatus.DETECTED


def test_removing_fair_use_reopens_the_scope_as_detected() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        second = _seed_incident(harness, image_url=SECOND_IMAGE)
        service = _service(harness)
        _mark(service, incident_id)

        preview = service.preview(incident_id, TriageAction.REMOVE_FAIR_USE).unwrap()
        assert preview.whitelist_effect is WhitelistEffect.DELETED
        assert preview.proposed_status is IncidentStatus.DETECTED
        assert preview.affected_incident_ids == tuple(sorted([incident_id, second]))

        outcome = service.confirm(preview).unwrap()

        assert outcome.action is TriageAction.REMOVE_FAIR_USE
        assert harness.count("whitelist_entries") == 0
        snapshot = service.load().unwrap()
        assert sorted(incident.id for incident in snapshot.active) == sorted([incident_id, second])
        assert snapshot.fair_use == ()


def test_every_confirmed_decision_appends_exactly_one_audit_event() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        service = _service(harness)

        _mark(service, incident_id)
        assert harness.count("audit_events") == 1

        removal = service.preview(incident_id, TriageAction.REMOVE_FAIR_USE).unwrap()
        service.confirm(removal).unwrap()

        assert harness.count("audit_events") == 2


def test_triage_is_unavailable_when_the_registry_gate_is_closed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    from provenance.infrastructure.sqlite.connection import SqliteRegistry
    from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter

    path = tmp_path_factory.mktemp("closed") / "registry.sqlite3"
    path.write_bytes(b"not a database")
    registry = SqliteRegistry(path)
    registry.initialize()
    adapter = SqliteRegistryAdapter(registry)
    clock = FixedClock()
    service = TriageService(adapter, MaterialActionRunner(adapter, clock), clock)

    assert service.load().unwrap_failure().code is FailureCode.CHECKS_FAILED
    assert service.evidence(1).unwrap_failure().code is FailureCode.CHECKS_FAILED
