"""Property 15: Failed transactions restore the exact prior registry state.

Validates: Requirements 5.5, 6.7, 6.8, 11.8, 17.11, 18.10, 20.16
"""

from __future__ import annotations

import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CommittedOperation,
    ContentHash,
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
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter, SqliteUnitOfWork

TABLES: Final = (
    "registered_assets",
    "incidents",
    "whitelist_entries",
    "audit_events",
    "operation_receipts",
    "schema_migrations",
)

SEEDED_HASH: Final = AssetHash("a" * 64)
SECOND_HASH: Final = AssetHash("b" * 64)
CREATOR: Final = CreatorId("studio.one")
OTHER_CREATOR: Final = CreatorId("studio.two")
AT: Final = UtcTimestamp("2026-05-06T07:08:09Z")
LATER: Final = UtcTimestamp("2026-07-08T09:10:11Z")
PAGE: Final = NormalizedUrl("https://example.com/Art")
IMAGE: Final = NormalizedUrl("https://cdn.example.com/a.png")
OTHER_IMAGE: Final = NormalizedUrl("https://cdn.example.com/b.png")

OPERATIONS: Final = (
    "register_new_asset",
    "register_duplicate_asset",
    "register_conflicting_creator",
    "detect_new_image",
    "rediscover_image",
    "mark_fair_use",
    "remove_fair_use",
    "authorize_strike",
    "append_audit",
    "record_receipt",
    "delete_asset",
)


class InjectedFailureError(RuntimeError):
    """Simulates an unexpected error part-way through a transaction."""


def _asset(asset_hash: AssetHash, creator: CreatorId) -> RegisterAsset:
    return RegisterAsset(
        asset_hash=asset_hash,
        creator_id=creator,
        registered_at=AT,
        width=8,
        height=8,
        source_media_type=MediaType.PNG,
        metadata=CreatorMetadata(creator_id=creator, display_name="Studio"),
    )


def _detection(image: NormalizedUrl, at: UtcTimestamp) -> VerifiedDetection:
    return VerifiedDetection(
        asset_hash=SEEDED_HASH,
        creator_id=CREATOR,
        page_url=PAGE,
        image_url=image,
        payload_created_at=AT,
        extraction_crc32=99,
        context=PageContext(title="Shop"),
        discovered_at=at,
    )


def _snapshot(registry: SqliteRegistry) -> dict[str, list[tuple[object, ...]]]:
    snapshot: dict[str, list[tuple[object, ...]]] = {}
    with registry.connect_for_read() as connection:
        for table in TABLES:
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            snapshot[table] = sorted(tuple(row) for row in rows)
    return snapshot


def _seed(adapter: SqliteRegistryAdapter) -> None:
    with adapter.begin("seed").unwrap() as uow:
        uow.assets.register_or_reuse(_asset(SEEDED_HASH, CREATOR)).unwrap()
        uow.incidents.upsert_detection(_detection(IMAGE, AT)).unwrap()
        uow.audits.append(
            NewAuditEvent(
                event_type=AuditEventType.ASSET_REGISTERED,
                occurred_at=AT,
                operation_key=OperationKey("1" * 64),
                asset_hash_tombstone=SEEDED_HASH,
            )
        ).unwrap()
        uow.commit()


def _run_operation(uow: SqliteUnitOfWork, name: str, key_seed: int) -> None:
    """Attempt one operation, ignoring expected failures."""
    key = OperationKey(f"{key_seed:064d}")

    if name == "register_new_asset":
        uow.assets.register_or_reuse(_asset(SECOND_HASH, CREATOR))
    elif name == "register_duplicate_asset":
        uow.assets.register_or_reuse(_asset(SEEDED_HASH, CREATOR))
    elif name == "register_conflicting_creator":
        uow.assets.register_or_reuse(_asset(SEEDED_HASH, OTHER_CREATOR))
    elif name == "detect_new_image":
        uow.incidents.upsert_detection(_detection(OTHER_IMAGE, LATER))
    elif name == "rediscover_image":
        uow.incidents.upsert_detection(_detection(IMAGE, LATER))
    elif name == "mark_fair_use":
        uow.whitelist.upsert_and_mark_fair_use(
            MarkFairUse(
                asset_hash=SEEDED_HASH,
                page_url=PAGE,
                rationale="commentary",
                at=LATER,
                operation_key=key,
            )
        )
    elif name == "remove_fair_use":
        uow.whitelist.remove_and_reopen(
            RemoveFairUse(asset_hash=SEEDED_HASH, page_url=PAGE, at=LATER, operation_key=key)
        )
    elif name == "authorize_strike":
        incidents = uow.incidents.by_scope(SEEDED_HASH, PAGE)
        if incidents:
            target = incidents[0]
            uow.incidents.apply_status_plan(
                IncidentTransitionPlan(
                    transitions=(
                        IncidentTransition(
                            incident_id=target.id,
                            previous_status=target.status,
                            new_status=IncidentStatus.STRIKE_AUTHORIZED,
                        ),
                    ),
                    audit=NewAuditEvent(
                        event_type=AuditEventType.STRIKE_AUTHORIZED,
                        occurred_at=LATER,
                        operation_key=key,
                        incident_id=target.id,
                    ),
                )
            )
    elif name == "append_audit":
        uow.audits.append(
            NewAuditEvent(
                event_type=AuditEventType.CREDIT_REQUESTED,
                occurred_at=LATER,
                operation_key=key,
                asset_hash_tombstone=SEEDED_HASH,
            )
        )
    elif name == "record_receipt":
        uow.operations.record(
            CommittedOperation(
                operation_key=key,
                operation_type="mark_fair_use",
                target_ids={"asset_hash": SEEDED_HASH},
                requested_values_hash=ContentHash("e" * 64),
                outcome={"status": "Fair Use"},
                committed_at=LATER,
            )
        )
    elif name == "delete_asset":
        preview = uow.assets.deletion_preview(SEEDED_HASH)
        if preview.failure is None:
            uow.assets.delete_if_preview_matches(preview.unwrap())


@given(
    st.lists(st.sampled_from(OPERATIONS), min_size=1, max_size=6),
    st.booleans(),
)
def test_abandoned_transactions_leave_the_registry_untouched(
    operations: list[str], raise_midway: bool
) -> None:
    # Feature: provenance, Property 15: Failed transactions restore the exact prior
    # registry state
    with tempfile.TemporaryDirectory() as directory:
        registry = SqliteRegistry(Path(directory) / "registry.sqlite3")
        registry.initialize().unwrap()
        adapter = SqliteRegistryAdapter(registry)
        _seed(adapter)

        before = _snapshot(registry)

        def apply_batch() -> None:
            with adapter.begin("batch").unwrap() as uow:
                for index, name in enumerate(operations):
                    _run_operation(uow, name, index + 10)
                    if raise_midway and index == len(operations) // 2:
                        raise InjectedFailureError(name)
                # No commit: the transaction is abandoned either way.

        if raise_midway:
            with suppress(InjectedFailureError):
                apply_batch()
        else:
            apply_batch()

        assert _snapshot(registry) == before


@given(st.lists(st.sampled_from(OPERATIONS), min_size=1, max_size=6))
def test_explicit_rollback_leaves_the_registry_untouched(operations: list[str]) -> None:
    # Feature: provenance, Property 15: Failed transactions restore the exact prior
    # registry state
    with tempfile.TemporaryDirectory() as directory:
        registry = SqliteRegistry(Path(directory) / "registry.sqlite3")
        registry.initialize().unwrap()
        adapter = SqliteRegistryAdapter(registry)
        _seed(adapter)

        before = _snapshot(registry)

        with adapter.begin("batch").unwrap() as uow:
            for index, name in enumerate(operations):
                _run_operation(uow, name, index + 40)
            uow.rollback()

        assert _snapshot(registry) == before


@given(st.lists(st.sampled_from(OPERATIONS), min_size=1, max_size=4))
def test_committed_batches_keep_the_registry_consistent(operations: list[str]) -> None:
    # Feature: provenance, Property 15: Failed transactions restore the exact prior
    # registry state
    with tempfile.TemporaryDirectory() as directory:
        registry = SqliteRegistry(Path(directory) / "registry.sqlite3")
        registry.initialize().unwrap()
        adapter = SqliteRegistryAdapter(registry)
        _seed(adapter)

        with adapter.begin("batch").unwrap() as uow:
            for index, name in enumerate(operations):
                _run_operation(uow, name, index + 70)
            uow.commit()

        # A committed batch must never leave the database inconsistent.
        with registry.connect_for_read() as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
