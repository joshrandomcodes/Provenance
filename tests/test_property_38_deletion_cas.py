"""Property 38: Deletion confirmation is a compare-and-swap.

Validates: Requirements 17.7, 17.8, 17.10
"""

from __future__ import annotations

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.errors import FailureCode
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CreatorId,
    MarkFairUse,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
)
from provenance.domain.time import UtcTimestamp
from tests.registry_support import RegistryHarness, detection, seed_asset, temporary_registry

HASH: Final = AssetHash("a" * 64)
MISSING_HASH: Final = AssetHash("9" * 64)
CREATOR: Final = CreatorId("studio.one")
PAGE: Final = NormalizedUrl("https://example.com/art")
AT: Final = UtcTimestamp("2026-05-06T07:08:09Z")


def _image(index: int) -> NormalizedUrl:
    return NormalizedUrl(f"https://cdn.example.com/{index}.png")


def _seed(harness: RegistryHarness, incidents: int, whitelisted: bool, audits: int) -> None:
    seed_asset(harness, HASH, CREATOR)
    with harness.adapter.begin("seed").unwrap() as uow:
        for index in range(incidents):
            uow.incidents.upsert_detection(detection(HASH, CREATOR, PAGE, _image(index))).unwrap()
        for index in range(audits):
            uow.audits.append(
                NewAuditEvent(
                    event_type=AuditEventType.INCIDENT_DETECTED,
                    occurred_at=AT,
                    operation_key=OperationKey(f"{index:064d}"),
                    asset_hash_tombstone=HASH,
                )
            ).unwrap()
        uow.commit()

    if whitelisted:
        with harness.adapter.begin("mark_fair_use").unwrap() as uow:
            uow.whitelist.upsert_and_mark_fair_use(
                MarkFairUse(
                    asset_hash=HASH,
                    page_url=PAGE,
                    rationale="commentary",
                    at=AT,
                    operation_key=OperationKey("f" * 64),
                )
            ).unwrap()
            uow.commit()


@given(
    st.integers(min_value=0, max_value=4),
    st.booleans(),
    st.integers(min_value=0, max_value=3),
)
def test_unchanged_counts_allow_deletion(incidents: int, whitelisted: bool, audits: int) -> None:
    # Feature: provenance, Property 38: Deletion confirmation is a compare-and-swap
    with temporary_registry() as harness:
        _seed(harness, incidents, whitelisted, audits)

        with harness.adapter.begin("delete").unwrap() as uow:
            preview = uow.assets.deletion_preview(HASH).unwrap()

        assert preview.counts.incidents == incidents
        assert preview.counts.whitelist_entries == (1 if whitelisted else 0)

        with harness.adapter.begin("delete").unwrap() as uow:
            outcome = uow.assets.delete_if_preview_matches(preview).unwrap()
            uow.commit()

        assert outcome.deleted is True
        assert harness.count("registered_assets") == 0
        assert harness.count("incidents") == 0
        assert harness.count("whitelist_entries") == 0
        # Audit history is retained as a tombstone reference.
        assert harness.count("audit_events") >= audits


@given(
    st.integers(min_value=0, max_value=3),
    st.booleans(),
    st.integers(min_value=1, max_value=3),
)
def test_added_dependants_invalidate_the_preview(
    incidents: int, whitelisted: bool, added: int
) -> None:
    # Feature: provenance, Property 38: Deletion confirmation is a compare-and-swap
    with temporary_registry() as harness:
        _seed(harness, incidents, whitelisted, audits=0)

        with harness.adapter.begin("delete").unwrap() as uow:
            preview = uow.assets.deletion_preview(HASH).unwrap()

        with harness.adapter.begin("scan").unwrap() as uow:
            for index in range(added):
                uow.incidents.upsert_detection(
                    detection(HASH, CREATOR, PAGE, _image(100 + index))
                ).unwrap()
            uow.commit()

        snapshot = harness.snapshot()

        with harness.adapter.begin("delete").unwrap() as uow:
            outcome = uow.assets.delete_if_preview_matches(preview).unwrap()
            uow.commit()

        assert outcome.deleted is False
        assert outcome.refreshed_preview is not None
        assert outcome.refreshed_preview.counts.incidents == incidents + added
        assert harness.snapshot() == snapshot

        # The refreshed preview is what makes deletion possible again.
        with harness.adapter.begin("delete").unwrap() as uow:
            confirmed = uow.assets.delete_if_preview_matches(outcome.refreshed_preview).unwrap()
            uow.commit()

        assert confirmed.deleted is True


@given(st.integers(min_value=1, max_value=4))
def test_removed_dependants_also_invalidate_the_preview(incidents: int) -> None:
    # Feature: provenance, Property 38: Deletion confirmation is a compare-and-swap
    with temporary_registry() as harness:
        _seed(harness, incidents, whitelisted=False, audits=0)

        with harness.adapter.begin("delete").unwrap() as uow:
            preview = uow.assets.deletion_preview(HASH).unwrap()
            targets = uow.incidents.by_scope(HASH, PAGE)

        # Remove one dependant so the confirmed counts no longer match.
        with harness.registry.connect_for_write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM incidents WHERE id = ?", (targets[0].id,))
            connection.execute("COMMIT")

        with harness.adapter.begin("delete").unwrap() as uow:
            outcome = uow.assets.delete_if_preview_matches(preview).unwrap()
            uow.commit()

        assert outcome.deleted is False
        assert outcome.refreshed_preview is not None
        assert outcome.refreshed_preview.counts.incidents == incidents - 1


@given(st.integers(min_value=0, max_value=3))
def test_preview_for_a_missing_asset_is_not_found(incidents: int) -> None:
    # Feature: provenance, Property 38: Deletion confirmation is a compare-and-swap
    with temporary_registry() as harness:
        _seed(harness, incidents, whitelisted=False, audits=0)

        with harness.adapter.begin("delete").unwrap() as uow:
            result = uow.assets.deletion_preview(MISSING_HASH)

        assert result.unwrap_failure().code is FailureCode.NOT_FOUND


@given(st.integers(min_value=0, max_value=3))
def test_deleting_twice_reports_the_asset_is_gone(incidents: int) -> None:
    # Feature: provenance, Property 38: Deletion confirmation is a compare-and-swap
    with temporary_registry() as harness:
        _seed(harness, incidents, whitelisted=False, audits=0)

        with harness.adapter.begin("delete").unwrap() as uow:
            preview = uow.assets.deletion_preview(HASH).unwrap()
            uow.assets.delete_if_preview_matches(preview).unwrap()
            uow.commit()

        with harness.adapter.begin("delete").unwrap() as uow:
            repeated = uow.assets.delete_if_preview_matches(preview)

        assert repeated.unwrap_failure().code is FailureCode.NOT_FOUND
