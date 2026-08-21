"""Property 20: Scan hard limits are never exceeded.

A generated event sequence drives the budget the way the scanner will: discovering
candidates, scheduling requests, streaming body chunks, following redirects, decoding
pixels, advancing a monotonic clock, and cancelling. No sequence may push any counter
past its limit.

Validates: Requirements 8.4, 8.5, 8.7, 8.8, 8.9, 8.10, 8.11, 20.17
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.cancellation import CooperativeCancellationToken
from provenance.domain.models import ExtractionKind, NormalizedUrl, ScanTerminalReason
from provenance.domain.scan_budget import (
    DiscoveryDecision,
    RedirectChain,
    ResponseKind,
    ScanBudget,
    ScanLimits,
)

# Small limits keep generated sequences able to reach every boundary.
LIMITS: Final = ScanLimits(
    html_bytes=2_000,
    image_bytes=1_500,
    total_bytes=6_000,
    unique_images=5,
    decoded_pixels=1_000,
    redirects_per_request=5,
    total_seconds=100.0,
    robots_bytes=1_000,
    redirect_body_bytes=200,
)

EVENTS: Final = (
    "discover",
    "discover_duplicate",
    "schedule",
    "read_page",
    "read_image",
    "read_robots",
    "read_redirect",
    "follow_redirect",
    "decode_pixels",
    "advance_clock",
    "cancel",
    "record_outcome",
)


class ManualClock:
    """Monotonic clock advanced by generated events."""

    def __init__(self) -> None:
        self.seconds = 500.0

    def utc_now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@st.composite
def event_sequences(draw: st.DrawFn) -> list[tuple[str, int]]:
    """Pairs of event name and magnitude."""
    length = draw(st.integers(min_value=1, max_value=60))
    return [
        (
            draw(st.sampled_from(EVENTS)),
            draw(st.integers(min_value=0, max_value=2_500)),
        )
        for _ in range(length)
    ]


def _apply(
    budget: ScanBudget,
    clock: ManualClock,
    token: CooperativeCancellationToken,
    events: list[tuple[str, int]],
) -> None:
    chain: RedirectChain = budget.open_request_chain()
    leases = {
        ResponseKind.PAGE: budget.open_response(ResponseKind.PAGE),
        ResponseKind.IMAGE: budget.open_response(ResponseKind.IMAGE),
        ResponseKind.ROBOTS: budget.open_response(ResponseKind.ROBOTS),
        ResponseKind.REDIRECT: budget.open_response(ResponseKind.REDIRECT),
    }
    discovered = 0

    for name, magnitude in events:
        match name:
            case "discover":
                url = NormalizedUrl(f"https://cdn.example.com/{discovered}.png")
                if budget.discover(url) is DiscoveryDecision.RETAINED:
                    discovered += 1
            case "discover_duplicate":
                budget.discover(NormalizedUrl("https://cdn.example.com/0.png"))
            case "schedule":
                for url in budget.retained_candidates:
                    if budget.schedule(url):
                        break
            case "read_page":
                leases[ResponseKind.PAGE].consume(magnitude)
            case "read_image":
                if leases[ResponseKind.IMAGE].consume(magnitude).failure is not None:
                    leases[ResponseKind.IMAGE] = budget.open_response(ResponseKind.IMAGE)
            case "read_robots":
                leases[ResponseKind.ROBOTS].consume(magnitude)
            case "read_redirect":
                leases[ResponseKind.REDIRECT].consume(magnitude)
            case "follow_redirect":
                if chain.follow().failure is not None:
                    chain = budget.open_request_chain()
            case "decode_pixels":
                budget.accept_decoded_pixels(magnitude)
            case "advance_clock":
                clock.advance(magnitude / 100.0)
            case "cancel":
                token.cancel()
            case "record_outcome":
                if budget.tally.recorded < budget.tally.attempted:
                    budget.tally.record(ExtractionKind.NO_WATERMARK)


@given(event_sequences())
def test_no_event_sequence_exceeds_any_limit(events: list[tuple[str, int]]) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    clock = ManualClock()
    token = CooperativeCancellationToken()
    budget = ScanBudget(LIMITS, clock, token)

    _apply(budget, clock, token, events)

    assert budget.total_bytes <= LIMITS.total_bytes
    assert budget.total_bytes_remaining == LIMITS.total_bytes - budget.total_bytes
    assert len(budget.retained_candidates) <= LIMITS.unique_images
    assert budget.tally.discovered <= LIMITS.unique_images
    assert budget.scheduled_images <= LIMITS.unique_images
    assert budget.scheduled_images == budget.tally.attempted


@given(event_sequences())
def test_per_response_limits_hold_for_every_kind(events: list[tuple[str, int]]) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    clock = ManualClock()
    token = CooperativeCancellationToken()
    budget = ScanBudget(LIMITS, clock, token)
    leases = {kind: budget.open_response(kind) for kind in ResponseKind}

    for name, magnitude in events:
        if name == "read_page":
            leases[ResponseKind.PAGE].consume(magnitude)
        elif name == "read_image":
            leases[ResponseKind.IMAGE].consume(magnitude)
        elif name == "read_robots":
            leases[ResponseKind.ROBOTS].consume(magnitude)
        elif name == "read_redirect":
            leases[ResponseKind.REDIRECT].consume(magnitude)

    for kind, lease in leases.items():
        assert lease.bytes_read <= LIMITS.limit_for(kind)
        assert lease.remaining <= LIMITS.limit_for(kind)
    assert budget.total_bytes <= LIMITS.total_bytes


@given(event_sequences())
def test_scheduling_stops_once_a_stop_reason_exists(
    events: list[tuple[str, int]],
) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    clock = ManualClock()
    token = CooperativeCancellationToken()
    budget = ScanBudget(LIMITS, clock, token)

    _apply(budget, clock, token, events)

    if budget.stop_reason() is not None:
        assert budget.can_schedule_image() is False
        for url in budget.retained_candidates:
            assert budget.schedule(url) is False


@given(event_sequences())
def test_redirect_chains_never_exceed_their_allowance(
    events: list[tuple[str, int]],
) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    clock = ManualClock()
    budget = ScanBudget(LIMITS, clock, None)
    chain = budget.open_request_chain()

    for name, _magnitude in events:
        if name == "follow_redirect":
            chain.follow()

    assert chain.followed <= LIMITS.redirects_per_request


@given(event_sequences())
def test_pixel_ceiling_is_never_accepted_above_the_limit(
    events: list[tuple[str, int]],
) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    budget = ScanBudget(LIMITS, ManualClock(), None)

    for name, magnitude in events:
        if name == "decode_pixels":
            accepted = budget.accept_decoded_pixels(magnitude)
            assert (accepted.failure is None) == (magnitude <= LIMITS.decoded_pixels)


@given(event_sequences())
def test_summary_counts_always_balance(events: list[tuple[str, int]]) -> None:
    # Feature: provenance, Property 20: Scan hard limits are never exceeded
    clock = ManualClock()
    token = CooperativeCancellationToken()
    budget = ScanBudget(LIMITS, clock, token)

    _apply(budget, clock, token, events)
    summary = budget.summary()

    assert summary.attempted + summary.skipped == summary.discovered
    categorised = (
        summary.verified
        + summary.no_watermark
        + summary.corrupt
        + summary.unregistered
        + summary.failed
        + summary.cancelled
    )
    assert categorised <= summary.attempted
    assert summary.total_response_bytes == budget.total_bytes
    assert summary.elapsed_seconds >= 0.0
    if summary.terminal_reason is ScanTerminalReason.COMPLETED:
        assert budget.is_cancelled is False
        assert budget.is_expired is False
