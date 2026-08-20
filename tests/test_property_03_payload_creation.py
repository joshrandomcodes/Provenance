"""Property 3: Payload creation uses exact validated identity and sampled UTC second.

Validates: Requirements 3.2, 6.12
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import AssetHash, CreatorId
from provenance.domain.payload import create_payload
from provenance.domain.time import (
    TIMESTAMP_LENGTH,
    format_utc_timestamp,
    is_valid_utc_timestamp,
)
from tests.strategies import asset_hashes, aware_utc_datetimes, creator_ids


class CountingClock:
    """Clock that records how many times wall-clock time was sampled."""

    def __init__(self, value: datetime) -> None:
        self._value = value
        self.utc_calls = 0

    def utc_now(self) -> datetime:
        self.utc_calls += 1
        return self._value

    def monotonic(self) -> float:
        return 0.0


@given(asset_hashes(), creator_ids(), aware_utc_datetimes())
def test_payload_preserves_identity_and_samples_the_clock_once(
    asset_hash: AssetHash, creator_id: CreatorId, moment: datetime
) -> None:
    # Feature: provenance, Property 3: Payload creation uses exact validated identity
    # and sampled UTC second
    clock = CountingClock(moment)

    payload = create_payload(asset_hash, creator_id, clock)

    assert payload.asset_hash == asset_hash
    assert payload.creator_id == creator_id
    assert payload.created_at == format_utc_timestamp(moment)
    assert clock.utc_calls == 1
    assert len(payload.created_at) == TIMESTAMP_LENGTH
    assert is_valid_utc_timestamp(payload.created_at)
    assert payload.created_at.endswith("Z")


@given(
    asset_hashes(),
    creator_ids(),
    aware_utc_datetimes(),
    st.integers(min_value=-12, max_value=14),
)
def test_equivalent_instants_in_other_zones_produce_the_same_timestamp(
    asset_hash: AssetHash, creator_id: CreatorId, moment: datetime, offset_hours: int
) -> None:
    # Feature: provenance, Property 3: Payload creation uses exact validated identity
    # and sampled UTC second
    shifted = moment.astimezone(timezone(timedelta(hours=offset_hours)))

    utc_payload = create_payload(asset_hash, creator_id, CountingClock(moment))
    shifted_payload = create_payload(asset_hash, creator_id, CountingClock(shifted))

    assert shifted_payload.created_at == utc_payload.created_at
