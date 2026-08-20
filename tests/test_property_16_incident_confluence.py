"""Property 16: Incident detection is confluent and unique.

Validates: Requirements 6.5, 10.5, 10.6, 18.8, 20.12
"""

from __future__ import annotations

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import AssetHash, CreatorId, NormalizedUrl
from provenance.domain.time import UtcTimestamp
from tests.registry_support import RegistryHarness, detection, seed_asset, temporary_registry

HASH_A: Final = AssetHash("a" * 64)
HASH_B: Final = AssetHash("b" * 64)
CREATOR: Final = CreatorId("studio.one")

PAGES: Final = (
    NormalizedUrl("https://example.com/art"),
    NormalizedUrl("https://example.com/Art"),
    NormalizedUrl("https://shop.example.com/listing?id=7"),
)
IMAGES: Final = (
    NormalizedUrl("https://cdn.example.com/a.png"),
    NormalizedUrl("https://cdn.example.com/b.png"),
)
TIMES: Final = (
    UtcTimestamp("2026-01-01T00:00:00Z"),
    UtcTimestamp("2026-06-15T12:30:45Z"),
    UtcTimestamp("2026-12-31T23:59:59Z"),
)

_POOL: Final = tuple(
    (asset_hash, page, image, at)
    for asset_hash in (HASH_A, HASH_B)
    for page in PAGES
    for image in IMAGES
    for at in TIMES
)

Discovery = tuple[AssetHash, NormalizedUrl, NormalizedUrl, UtcTimestamp]


@st.composite
def discovery_orders(draw: st.DrawFn) -> tuple[tuple[Discovery, ...], tuple[Discovery, ...]]:
    """One collection of discoveries, in two independently drawn orders."""
    count = draw(st.integers(min_value=0, max_value=8))
    chosen = draw(st.lists(st.sampled_from(_POOL), min_size=count, max_size=count))
    first = draw(st.permutations(chosen))
    second = draw(st.permutations(chosen))
    return tuple(first), tuple(second)


def _apply(harness: RegistryHarness, discoveries: tuple[Discovery, ...]) -> None:
    seed_asset(harness, HASH_A, CREATOR)
    seed_asset(harness, HASH_B, CREATOR)
    for asset_hash, page, image, at in discoveries:
        with harness.adapter.begin("scan").unwrap() as uow:
            uow.incidents.upsert_detection(
                detection(asset_hash, CREATOR, page, image, at=at)
            ).unwrap()
            uow.commit()


@given(discovery_orders())
def test_any_order_produces_the_same_incident_key_set(
    orders: tuple[tuple[Discovery, ...], tuple[Discovery, ...]],
) -> None:
    # Feature: provenance, Property 16: Incident detection is confluent and unique
    first_order, second_order = orders

    with temporary_registry() as first, temporary_registry() as second:
        _apply(first, first_order)
        _apply(second, second_order)

        assert first.incident_keys() == second.incident_keys()
        assert first.count("incidents") == second.count("incidents")


@given(discovery_orders())
def test_incident_count_equals_the_number_of_unique_keys(
    orders: tuple[tuple[Discovery, ...], tuple[Discovery, ...]],
) -> None:
    # Feature: provenance, Property 16: Incident detection is confluent and unique
    discoveries, _ = orders
    expected = {(str(item[0]), str(item[1]), str(item[2])) for item in discoveries}

    with temporary_registry() as harness:
        _apply(harness, discoveries)

        assert harness.incident_keys() == expected
        assert harness.count("incidents") == len(expected)


@given(
    st.lists(st.sampled_from(TIMES), min_size=1, max_size=5),
)
def test_rediscovery_preserves_first_seen_and_advances_last_seen(
    times: list[UtcTimestamp],
) -> None:
    # Feature: provenance, Property 16: Incident detection is confluent and unique
    page, image = PAGES[0], IMAGES[0]

    with temporary_registry() as harness:
        seed_asset(harness, HASH_A, CREATOR)
        stored_ids: set[int] = set()

        for at in times:
            with harness.adapter.begin("scan").unwrap() as uow:
                incident = uow.incidents.upsert_detection(
                    detection(HASH_A, CREATOR, page, image, at=at)
                ).unwrap()
                uow.commit()
            stored_ids.add(incident.id)

        assert len(stored_ids) == 1
        assert incident.first_seen_at == times[0]
        assert incident.last_seen_at == times[-1]
        assert harness.count("incidents") == 1
