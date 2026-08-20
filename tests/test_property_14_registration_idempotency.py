"""Property 14: Registration is creator-bound and idempotent.

Validates: Requirements 5.2, 5.3, 5.4, 6.4, 20.11
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.errors import FailureCode
from provenance.domain.models import AssetHash, CreatorId
from provenance.domain.time import UtcTimestamp
from tests.registry_support import register_command, temporary_registry
from tests.strategies import asset_hashes, creator_ids, utc_timestamps


@given(
    asset_hashes(),
    creator_ids(),
    utc_timestamps(),
    utc_timestamps(),
    st.text(min_size=1, max_size=40),
    st.integers(min_value=1, max_value=4),
)
def test_repeat_registration_reuses_the_original_record(
    asset_hash: AssetHash,
    creator_id: CreatorId,
    first_at: UtcTimestamp,
    second_at: UtcTimestamp,
    later_display_name: str,
    repeats: int,
) -> None:
    # Feature: provenance, Property 14: Registration is creator-bound and idempotent
    with temporary_registry() as harness:
        with harness.adapter.begin("register").unwrap() as uow:
            first = uow.assets.register_or_reuse(
                register_command(asset_hash, creator_id, at=first_at, display_name="Original")
            ).unwrap()
            uow.commit()

        assert first.created is True

        for _ in range(repeats):
            with harness.adapter.begin("register").unwrap() as uow:
                repeated = uow.assets.register_or_reuse(
                    register_command(
                        asset_hash,
                        creator_id,
                        at=second_at,
                        display_name=later_display_name,
                    )
                ).unwrap()
                uow.commit()

            assert repeated.created is False
            # Original timestamp and metadata survive, whatever the retry supplied.
            assert repeated.asset.registered_at == first_at
            assert repeated.asset.metadata.display_name == "Original"

        assert harness.count("registered_assets") == 1


@given(asset_hashes(), creator_ids(), creator_ids())
def test_a_different_creator_conflicts_without_mutation(
    asset_hash: AssetHash, owner: CreatorId, intruder: CreatorId
) -> None:
    # Feature: provenance, Property 14: Registration is creator-bound and idempotent
    if owner == intruder:
        return

    with temporary_registry() as harness:
        with harness.adapter.begin("register").unwrap() as uow:
            uow.assets.register_or_reuse(register_command(asset_hash, owner)).unwrap()
            uow.commit()

        snapshot = harness.snapshot()

        with harness.adapter.begin("register").unwrap() as uow:
            result = uow.assets.register_or_reuse(register_command(asset_hash, intruder))
            uow.commit()

        assert result.value is None
        assert result.unwrap_failure().code is FailureCode.IDENTITY_CONFLICT
        assert harness.snapshot() == snapshot


@given(st.lists(asset_hashes(), min_size=1, max_size=6, unique=True), creator_ids())
def test_distinct_hashes_create_distinct_records(
    hashes: list[AssetHash], creator_id: CreatorId
) -> None:
    # Feature: provenance, Property 14: Registration is creator-bound and idempotent
    with temporary_registry() as harness:
        with harness.adapter.begin("register").unwrap() as uow:
            for asset_hash in hashes:
                uow.assets.register_or_reuse(register_command(asset_hash, creator_id)).unwrap()
            uow.commit()

        assert harness.count("registered_assets") == len(hashes)
