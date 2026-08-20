"""Transaction ownership, isolation, rollback, and repository behavior.

Requirements: 5.2-5.5, 6.5, 6.7, 6.8, 10.5-10.7, 12.1-12.7, 17.7-17.11, 18.6-18.8
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from provenance.domain.errors import FailureCode
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CreatorId,
    CreatorMetadata,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionPlan,
    MarkFairUse,
    MediaType,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
    PageContext,
    RegisterAsset,
    RemoveFairUse,
    VerifiedDetection,
)
from provenance.domain.time import UtcTimestamp
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.repositories import (
    DETAIL_CREATOR_MISMATCH,
    DETAIL_STATUS_CHANGED,
)
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter, SqliteUnitOfWork

pytestmark = pytest.mark.integration

HASH_A = AssetHash("a" * 64)
HASH_B = AssetHash("b" * 64)
CREATOR = CreatorId("studio.one")
OTHER_CREATOR = CreatorId("studio.two")
AT = UtcTimestamp("2026-05-06T07:08:09Z")
LATER = UtcTimestamp("2026-06-07T08:09:10Z")
PAGE = NormalizedUrl("https://example.com/Art")
OTHER_PAGE = NormalizedUrl("https://example.com/art")
IMAGE = NormalizedUrl("https://cdn.example.com/a.png")
OTHER_IMAGE = NormalizedUrl("https://cdn.example.com/b.png")


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteRegistryAdapter:
    registry = SqliteRegistry(tmp_path / "registry.sqlite3")
    registry.initialize().unwrap()
    return SqliteRegistryAdapter(registry)


def _register(
    asset_hash: AssetHash = HASH_A, creator: CreatorId = CREATOR, at: UtcTimestamp = AT
) -> RegisterAsset:
    return RegisterAsset(
        asset_hash=asset_hash,
        creator_id=creator,
        registered_at=at,
        width=8,
        height=8,
        source_media_type=MediaType.PNG,
        metadata=CreatorMetadata(
            creator_id=creator,
            display_name="Studio One",
            contact_email="studio@example.com",
        ),
    )


def _detection(
    page: NormalizedUrl = PAGE,
    image: NormalizedUrl = IMAGE,
    at: UtcTimestamp = AT,
    asset_hash: AssetHash = HASH_A,
    creator: CreatorId = CREATOR,
    context: PageContext | None = None,
) -> VerifiedDetection:
    return VerifiedDetection(
        asset_hash=asset_hash,
        creator_id=creator,
        page_url=page,
        image_url=image,
        payload_created_at=AT,
        extraction_crc32=4242,
        context=context or PageContext(title="Shop", alt="artwork"),
        discovered_at=at,
    )


def _key(seed: str) -> OperationKey:
    return OperationKey(seed[0] * 64)


def _seed_asset(adapter: SqliteRegistryAdapter) -> None:
    with adapter.begin("register").unwrap() as uow:
        uow.assets.register_or_reuse(_register()).unwrap()
        uow.commit()


def test_uncommitted_work_is_discarded(adapter: SqliteRegistryAdapter) -> None:
    with adapter.begin("register").unwrap() as uow:
        uow.assets.register_or_reuse(_register()).unwrap()
        # No commit.

    with adapter.begin("read").unwrap() as uow:
        assert uow.assets.get(HASH_A) is None


def test_committed_work_is_visible_to_later_transactions(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)

    with adapter.begin("read").unwrap() as uow:
        asset = uow.assets.get(HASH_A)

    assert asset is not None
    assert asset.creator_id == CREATOR
    assert asset.metadata.contact_email == "studio@example.com"


def test_changes_are_invisible_to_another_connection_before_commit(
    adapter: SqliteRegistryAdapter, tmp_path: Path
) -> None:
    with adapter.begin("register").unwrap() as uow:
        uow.assets.register_or_reuse(_register()).unwrap()

        observer = sqlite3.connect(tmp_path / "registry.sqlite3", isolation_level=None)
        try:
            visible = observer.execute("SELECT count(*) FROM registered_assets").fetchone()[0]
        finally:
            observer.close()

        assert visible == 0
        uow.commit()

    with adapter.begin("read").unwrap() as uow:
        assert uow.assets.get(HASH_A) is not None


def test_exception_inside_the_context_rolls_back(adapter: SqliteRegistryAdapter) -> None:
    def register_then_fail() -> None:
        with adapter.begin("register").unwrap() as uow:
            uow.assets.register_or_reuse(_register()).unwrap()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        register_then_fail()

    with adapter.begin("read").unwrap() as uow:
        assert uow.assets.get(HASH_A) is None


def test_commit_twice_is_refused(adapter: SqliteRegistryAdapter) -> None:
    with adapter.begin("register").unwrap() as uow:
        uow.assets.register_or_reuse(_register()).unwrap()
        uow.commit()

        with pytest.raises(RuntimeError):
            uow.commit()


def test_registration_is_idempotent_for_the_same_creator(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)

    with adapter.begin("register").unwrap() as uow:
        outcome = uow.assets.register_or_reuse(_register(at=LATER)).unwrap()
        uow.commit()

    assert outcome.created is False
    assert outcome.asset.registered_at == AT  # original timestamp preserved


def test_registration_conflict_for_a_different_creator(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)

    with adapter.begin("register").unwrap() as uow:
        result = uow.assets.register_or_reuse(_register(creator=OTHER_CREATOR))

    failure = result.unwrap_failure()
    assert failure.code is FailureCode.IDENTITY_CONFLICT
    assert failure.safe_detail == DETAIL_CREATOR_MISMATCH


def test_detection_creates_then_refreshes_one_incident(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)

    with adapter.begin("scan").unwrap() as uow:
        first = uow.incidents.upsert_detection(_detection()).unwrap()
        uow.commit()

    with adapter.begin("scan").unwrap() as uow:
        second = uow.incidents.upsert_detection(
            _detection(at=LATER, context=PageContext(title="Updated"))
        ).unwrap()
        uow.commit()

    assert second.id == first.id
    assert second.first_seen_at == AT
    assert second.last_seen_at == LATER
    assert second.context.title == "Updated"
    assert second.status is IncidentStatus.DETECTED


def test_detection_requires_a_matching_creator(adapter: SqliteRegistryAdapter) -> None:
    _seed_asset(adapter)

    with adapter.begin("scan").unwrap() as uow:
        result = uow.incidents.upsert_detection(_detection(creator=OTHER_CREATOR))

    assert result.unwrap_failure().code is FailureCode.IDENTITY_CONFLICT


def test_detection_for_an_unregistered_asset_is_refused(
    adapter: SqliteRegistryAdapter,
) -> None:
    with adapter.begin("scan").unwrap() as uow:
        result = uow.incidents.upsert_detection(_detection(asset_hash=HASH_B))

    assert result.unwrap_failure().code is FailureCode.NOT_FOUND


def test_distinct_image_urls_are_distinct_incidents(adapter: SqliteRegistryAdapter) -> None:
    _seed_asset(adapter)

    with adapter.begin("scan").unwrap() as uow:
        first = uow.incidents.upsert_detection(_detection()).unwrap()
        second = uow.incidents.upsert_detection(_detection(image=OTHER_IMAGE)).unwrap()
        uow.commit()

    assert first.id != second.id


def test_mark_fair_use_suppresses_only_the_exact_scope(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        uow.incidents.upsert_detection(_detection()).unwrap()
        uow.incidents.upsert_detection(_detection(image=OTHER_IMAGE)).unwrap()
        uow.incidents.upsert_detection(_detection(page=OTHER_PAGE)).unwrap()
        uow.commit()

    with adapter.begin("mark_fair_use").unwrap() as uow:
        transitions = uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=HASH_A,
                page_url=PAGE,
                rationale="commentary",
                at=LATER,
                operation_key=_key("1"),
            )
        ).unwrap()
        uow.commit()

    assert len(transitions.transitions) == 2
    assert transitions.audit.event_type is AuditEventType.FAIR_USE_MARKED

    with adapter.begin("read").unwrap() as uow:
        active = uow.incidents.list_active()
        fair_use = uow.incidents.list_fair_use()

    # The differently cased path remains active.
    assert [incident.page_url for incident in active] == [OTHER_PAGE]
    assert len(fair_use) == 2


def test_second_mark_fair_use_updates_one_entry(adapter: SqliteRegistryAdapter) -> None:
    _seed_asset(adapter)
    command = MarkFairUse(
        asset_hash=HASH_A,
        page_url=PAGE,
        rationale="first reason",
        at=AT,
        operation_key=_key("1"),
    )

    with adapter.begin("mark_fair_use").unwrap() as uow:
        uow.whitelist.upsert_and_mark_fair_use(command).unwrap()
        uow.commit()

    with adapter.begin("mark_fair_use").unwrap() as uow:
        updated = uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=HASH_A,
                page_url=PAGE,
                rationale="second reason",
                at=LATER,
                operation_key=_key("2"),
            )
        )
        assert updated.failure is None
        entry = uow.whitelist.exact(HASH_A, PAGE)
        uow.commit()

    assert entry is not None
    assert entry.rationale == "second reason"
    assert entry.created_at == AT
    assert entry.modified_at == LATER


def test_detection_inside_a_whitelisted_scope_is_recorded_as_fair_use(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)
    with adapter.begin("mark_fair_use").unwrap() as uow:
        uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=HASH_A,
                page_url=PAGE,
                rationale="teaching",
                at=AT,
                operation_key=_key("1"),
            )
        ).unwrap()
        uow.commit()

    with adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(_detection()).unwrap()
        uow.commit()

    assert incident.status is IncidentStatus.FAIR_USE
    with adapter.begin("read").unwrap() as uow:
        assert uow.incidents.list_active() == []


def test_removing_a_whitelist_entry_reopens_only_fair_use_incidents(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        uow.incidents.upsert_detection(_detection()).unwrap()
        second = uow.incidents.upsert_detection(_detection(image=OTHER_IMAGE)).unwrap()
        uow.commit()

    with adapter.begin("mark_fair_use").unwrap() as uow:
        uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=HASH_A,
                page_url=PAGE,
                rationale="review",
                at=AT,
                operation_key=_key("1"),
            )
        ).unwrap()
        uow.commit()

    # A credit request on one incident must survive the removal unchanged.
    with adapter.begin("credit").unwrap() as uow:
        uow.incidents.apply_status_plan(
            IncidentTransitionPlan(
                transitions=(
                    IncidentTransition(
                        incident_id=second.id,
                        previous_status=IncidentStatus.FAIR_USE,
                        new_status=IncidentStatus.CREDIT_REQUESTED,
                    ),
                ),
                audit=NewAuditEvent(
                    event_type=AuditEventType.CREDIT_REQUESTED,
                    occurred_at=AT,
                    operation_key=_key("3"),
                    incident_id=second.id,
                ),
            )
        ).unwrap()
        uow.commit()

    with adapter.begin("remove_fair_use").unwrap() as uow:
        removed = uow.whitelist.remove_and_reopen(
            RemoveFairUse(asset_hash=HASH_A, page_url=PAGE, at=LATER, operation_key=_key("4"))
        ).unwrap()
        uow.commit()

    assert len(removed.transitions) == 1
    assert removed.transitions[0].new_status is IncidentStatus.DETECTED

    with adapter.begin("read").unwrap() as uow:
        statuses = {incident.id: incident.status for incident in uow.incidents.list_active()}
        assert uow.whitelist.exact(HASH_A, PAGE) is None

    assert statuses[second.id] is IncidentStatus.CREDIT_REQUESTED


def test_removing_a_missing_whitelist_entry_is_not_found(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)

    with adapter.begin("remove_fair_use").unwrap() as uow:
        result = uow.whitelist.remove_and_reopen(
            RemoveFairUse(asset_hash=HASH_A, page_url=PAGE, at=AT, operation_key=_key("1"))
        )

    assert result.unwrap_failure().code is FailureCode.NOT_FOUND


def test_status_plan_refuses_a_stale_previous_status(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(_detection()).unwrap()
        uow.commit()

    plan = IncidentTransitionPlan(
        transitions=(
            IncidentTransition(
                incident_id=incident.id,
                previous_status=IncidentStatus.CREDIT_REQUESTED,  # not the current status
                new_status=IncidentStatus.STRIKE_AUTHORIZED,
            ),
        ),
        audit=NewAuditEvent(
            event_type=AuditEventType.STRIKE_AUTHORIZED,
            occurred_at=AT,
            operation_key=_key("5"),
            incident_id=incident.id,
        ),
    )

    with adapter.begin("strike").unwrap() as uow:
        result = uow.incidents.apply_status_plan(plan)

    failure = result.unwrap_failure()
    assert failure.code is FailureCode.STALE_PREVIEW
    assert failure.safe_detail == DETAIL_STATUS_CHANGED


def test_status_change_and_audit_commit_together(adapter: SqliteRegistryAdapter) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(_detection()).unwrap()
        uow.commit()

    plan = IncidentTransitionPlan(
        transitions=(
            IncidentTransition(
                incident_id=incident.id,
                previous_status=IncidentStatus.DETECTED,
                new_status=IncidentStatus.STRIKE_AUTHORIZED,
            ),
        ),
        audit=NewAuditEvent(
            event_type=AuditEventType.STRIKE_AUTHORIZED,
            occurred_at=LATER,
            operation_key=_key("6"),
            incident_id=incident.id,
            previous_statuses={incident.id: IncidentStatus.DETECTED},
            new_statuses={incident.id: IncidentStatus.STRIKE_AUTHORIZED},
        ),
    )

    with adapter.begin("strike").unwrap() as uow:
        applied = uow.incidents.apply_status_plan(plan).unwrap()
        uow.commit()

    assert applied.audit.event_type is AuditEventType.STRIKE_AUTHORIZED
    assert applied.audit.previous_statuses == {incident.id: IncidentStatus.DETECTED}

    with adapter.begin("read").unwrap() as uow:
        stored = uow.incidents.get(incident.id)
        audit = uow.audits.by_operation_key(_key("6"))

    assert stored is not None
    assert stored.status is IncidentStatus.STRIKE_AUTHORIZED
    assert audit is not None


def test_failed_audit_prevents_the_status_change_from_committing(
    adapter: SqliteRegistryAdapter,
) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(_detection()).unwrap()
        uow.audits.append(
            NewAuditEvent(
                event_type=AuditEventType.INCIDENT_DETECTED,
                occurred_at=AT,
                operation_key=_key("7"),
                incident_id=incident.id,
            )
        ).unwrap()
        uow.commit()

    # Reusing the operation key must fail, leaving the status untouched.
    plan = IncidentTransitionPlan(
        transitions=(
            IncidentTransition(
                incident_id=incident.id,
                previous_status=IncidentStatus.DETECTED,
                new_status=IncidentStatus.STRIKE_AUTHORIZED,
            ),
        ),
        audit=NewAuditEvent(
            event_type=AuditEventType.STRIKE_AUTHORIZED,
            occurred_at=LATER,
            operation_key=_key("7"),
            incident_id=incident.id,
        ),
    )

    with adapter.begin("strike").unwrap() as uow:
        result = uow.incidents.apply_status_plan(plan)
        assert result.failure is not None
        # The caller abandons the transaction, so nothing is committed.

    with adapter.begin("read").unwrap() as uow:
        stored = uow.incidents.get(incident.id)

    assert stored is not None
    assert stored.status is IncidentStatus.DETECTED


def test_operation_receipts_make_retries_idempotent(
    adapter: SqliteRegistryAdapter,
) -> None:
    from provenance.domain.models import CommittedOperation, ContentHash

    receipt = CommittedOperation(
        operation_key=_key("8"),
        operation_type="mark_fair_use",
        target_ids={"asset_hash": HASH_A},
        requested_values_hash=ContentHash("d" * 64),
        outcome={"status": "Fair Use"},
        committed_at=AT,
    )

    with adapter.begin("mark_fair_use").unwrap() as uow:
        assert uow.operations.committed(receipt.operation_key) is None
        uow.operations.record(receipt).unwrap()
        uow.commit()

    with adapter.begin("mark_fair_use").unwrap() as uow:
        existing = uow.operations.committed(receipt.operation_key)
        duplicate = uow.operations.record(receipt)

    assert existing is not None
    assert existing.outcome == {"status": "Fair Use"}
    assert duplicate.unwrap_failure().code is FailureCode.CONSTRAINT


def test_deletion_preview_is_a_compare_and_swap(adapter: SqliteRegistryAdapter) -> None:
    _seed_asset(adapter)
    with adapter.begin("scan").unwrap() as uow:
        uow.incidents.upsert_detection(_detection()).unwrap()
        uow.commit()

    with adapter.begin("delete").unwrap() as uow:
        preview = uow.assets.deletion_preview(HASH_A).unwrap()

    assert preview.counts.incidents == 1

    # A new incident appears after the preview was shown.
    with adapter.begin("scan").unwrap() as uow:
        uow.incidents.upsert_detection(_detection(image=OTHER_IMAGE)).unwrap()
        uow.commit()

    with adapter.begin("delete").unwrap() as uow:
        outcome = uow.assets.delete_if_preview_matches(preview).unwrap()
        uow.commit()

    assert outcome.deleted is False
    assert outcome.refreshed_preview is not None
    assert outcome.refreshed_preview.counts.incidents == 2

    with adapter.begin("delete").unwrap() as uow:
        confirmed = uow.assets.delete_if_preview_matches(outcome.refreshed_preview).unwrap()
        uow.commit()

    assert confirmed.deleted is True
    with adapter.begin("read").unwrap() as uow:
        assert uow.assets.get(HASH_A) is None
        assert uow.incidents.list_active() == []


def test_deletion_preview_for_a_missing_asset_is_not_found(
    adapter: SqliteRegistryAdapter,
) -> None:
    with adapter.begin("delete").unwrap() as uow:
        result = uow.assets.deletion_preview(HASH_B)

    assert result.unwrap_failure().code is FailureCode.NOT_FOUND


def test_begin_is_refused_when_the_gate_is_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    path.write_bytes(b"not a database")
    registry = SqliteRegistry(path)
    registry.initialize()

    result = SqliteRegistryAdapter(registry).begin("register")

    assert result.value is None
    assert result.unwrap_failure().code is FailureCode.CHECKS_FAILED


def test_unit_of_work_exposes_every_repository(adapter: SqliteRegistryAdapter) -> None:
    with adapter.begin("read").unwrap() as uow:
        assert isinstance(uow, SqliteUnitOfWork)
        assert uow.operation == "read"
        assert uow.committed is False
        assert uow.assets is not None
        assert uow.incidents is not None
        assert uow.whitelist is not None
        assert uow.audits is not None
        assert uow.operations is not None
