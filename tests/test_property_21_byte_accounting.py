"""Property 21: Byte accounting is invariant under chunking.

Validates: Requirements 8.6, 18.5
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.scan_budget import ResponseKind, ScanBudget, ScanLimits


class FrozenClock:
    """Clock that never advances, isolating byte accounting from timing."""

    def utc_now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


@st.composite
def body_and_partitions(draw: st.DrawFn) -> tuple[int, list[list[int]]]:
    """One body size plus several different chunk partitions of it."""
    size = draw(st.integers(min_value=0, max_value=4_000))
    partitions: list[list[int]] = []
    for _ in range(draw(st.integers(min_value=2, max_value=4))):
        remaining = size
        chunks: list[int] = []
        while remaining > 0:
            chunk = draw(st.integers(min_value=1, max_value=max(1, remaining)))
            chunks.append(chunk)
            remaining -= chunk
        partitions.append(chunks)
    return size, partitions


def _feed(limit: int, total: int, chunks: list[int]) -> tuple[int, int, bool]:
    """Charge chunks until one is refused. Returns charged, total, and refusal flag."""
    budget = ScanBudget(ScanLimits(image_bytes=limit, total_bytes=total), FrozenClock(), None)
    lease = budget.open_response(ResponseKind.IMAGE)
    refused = False
    for chunk in chunks:
        if lease.consume(chunk).failure is not None:
            refused = True
            break
    return lease.bytes_read, budget.total_bytes, refused


@given(body_and_partitions(), st.integers(min_value=1, max_value=4_000))
def test_charged_bytes_are_identical_across_partitions(
    case: tuple[int, list[list[int]]], limit: int
) -> None:
    # Feature: provenance, Property 21: Byte accounting is invariant under chunking
    size, partitions = case
    outcomes = {_feed(limit, 10_000_000, chunks) for chunks in partitions}

    # Every partition of the same body reaches the same charged total and verdict.
    if size <= limit:
        assert outcomes == {(size, size, False)}
    else:
        assert all(refused for _charged, _total, refused in outcomes)
        assert all(charged <= limit for charged, _total, _refused in outcomes)


@given(body_and_partitions())
def test_every_accepted_byte_is_charged_exactly_once(
    case: tuple[int, list[list[int]]],
) -> None:
    # Feature: provenance, Property 21: Byte accounting is invariant under chunking
    size, partitions = case
    for chunks in partitions:
        charged, total, refused = _feed(10_000_000, 10_000_000, chunks)

        assert refused is False
        assert charged == size
        assert total == size
        assert sum(chunks) == size


@given(
    st.lists(st.lists(st.integers(min_value=1, max_value=500), max_size=8), max_size=6),
    st.integers(min_value=1, max_value=5_000),
)
def test_the_total_never_exceeds_its_limit_across_many_responses(
    bodies: list[list[int]], total_limit: int
) -> None:
    # Feature: provenance, Property 21: Byte accounting is invariant under chunking
    budget = ScanBudget(
        ScanLimits(total_bytes=total_limit, image_bytes=10_000_000), FrozenClock(), None
    )
    charged_sum = 0

    for chunks in bodies:
        lease = budget.open_response(ResponseKind.IMAGE)
        for chunk in chunks:
            if lease.consume(chunk).failure is not None:
                break
        charged_sum += lease.bytes_read

    assert budget.total_bytes == charged_sum
    assert budget.total_bytes <= total_limit
    assert budget.total_bytes_remaining == total_limit - budget.total_bytes


@given(st.integers(min_value=0, max_value=2_000), st.integers(min_value=1, max_value=2_000))
def test_a_refused_chunk_leaves_both_counters_untouched(size: int, limit: int) -> None:
    # Feature: provenance, Property 21: Byte accounting is invariant under chunking
    budget = ScanBudget(ScanLimits(image_bytes=limit, total_bytes=10_000_000), FrozenClock(), None)
    lease = budget.open_response(ResponseKind.IMAGE)

    result = lease.consume(size)

    if result.failure is None:
        assert lease.bytes_read == size
        assert budget.total_bytes == size
    else:
        assert lease.bytes_read == 0
        assert budget.total_bytes == 0
