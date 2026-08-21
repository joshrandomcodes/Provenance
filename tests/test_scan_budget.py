"""Scan budget accounting, cancellation, and terminal summaries.

Requirements: 8.4-8.12, 18.1, 18.3, 18.4, 18.5, 18.9
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from provenance.domain.cancellation import CooperativeCancellationToken
from provenance.domain.errors import FailureCode
from provenance.domain.models import ExtractionKind, NormalizedUrl, ScanTerminalReason
from provenance.domain.scan_budget import (
    DiscoveryDecision,
    ResponseKind,
    ScanBudget,
    ScanLimits,
    ScanTally,
)

pytestmark = pytest.mark.unit


class ManualClock:
    """Monotonic clock advanced explicitly by tests."""

    def __init__(self) -> None:
        self.seconds = 1_000.0

    def utc_now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def _url(index: int) -> NormalizedUrl:
    return NormalizedUrl(f"https://cdn.example.com/{index}.png")


def _budget(
    clock: ManualClock | None = None,
    limits: ScanLimits | None = None,
    cancel: CooperativeCancellationToken | None = None,
) -> tuple[ScanBudget, ManualClock]:
    active_clock = clock or ManualClock()
    return ScanBudget(limits or ScanLimits(), active_clock, cancel), active_clock


def test_specified_limits_match_the_scan_budget() -> None:
    limits = ScanLimits()

    assert limits.html_bytes == 2_097_152
    assert limits.image_bytes == 10_485_760
    assert limits.total_bytes == 52_428_800
    assert limits.unique_images == 100
    assert limits.decoded_pixels == 40_000_000
    assert limits.redirects_per_request == 5
    assert limits.connect_seconds == 5.0
    assert limits.next_byte_seconds == 15.0
    assert limits.total_seconds == 120.0


def test_html_bytes_are_capped() -> None:
    budget, _ = _budget()
    lease = budget.open_response(ResponseKind.PAGE)

    assert lease.consume(budget.limits.html_bytes).failure is None
    assert lease.consume(1).unwrap_failure().code is FailureCode.HTML_LIMIT
    assert lease.bytes_read == budget.limits.html_bytes


def test_image_bytes_are_capped() -> None:
    budget, _ = _budget()
    lease = budget.open_response(ResponseKind.IMAGE)

    assert lease.consume(budget.limits.image_bytes).failure is None
    assert lease.consume(1).unwrap_failure().code is FailureCode.IMAGE_BYTES_LIMIT


def test_total_bytes_are_capped_across_responses() -> None:
    limits = ScanLimits(total_bytes=1_000, image_bytes=800)
    budget, _ = _budget(limits=limits)

    first = budget.open_response(ResponseKind.IMAGE)
    second = budget.open_response(ResponseKind.IMAGE)
    assert first.consume(800).failure is None
    assert second.consume(200).failure is None

    third = budget.open_response(ResponseKind.IMAGE)
    assert third.consume(1).unwrap_failure().code is FailureCode.TOTAL_BYTES_LIMIT
    assert budget.total_bytes == 1_000
    assert budget.total_bytes_remaining == 0


def test_declared_length_is_refused_before_reading() -> None:
    limits = ScanLimits(image_bytes=500, total_bytes=10_000)
    budget, _ = _budget(limits=limits)
    lease = budget.open_response(ResponseKind.IMAGE)

    refused = lease.accept_declared_length(501)

    assert refused.unwrap_failure().code is FailureCode.IMAGE_BYTES_LIMIT
    assert lease.bytes_read == 0
    assert budget.total_bytes == 0
    assert lease.accept_declared_length(500).failure is None
    assert lease.accept_declared_length(None).failure is None


def test_declared_length_respects_the_total_ceiling() -> None:
    limits = ScanLimits(image_bytes=5_000, total_bytes=1_000)
    budget, _ = _budget(limits=limits)
    budget.open_response(ResponseKind.IMAGE).consume(900)

    lease = budget.open_response(ResponseKind.IMAGE)

    assert lease.accept_declared_length(200).unwrap_failure().code is FailureCode.TOTAL_BYTES_LIMIT


def test_a_refused_chunk_charges_nothing() -> None:
    limits = ScanLimits(image_bytes=100, total_bytes=10_000)
    budget, _ = _budget(limits=limits)
    lease = budget.open_response(ResponseKind.IMAGE)
    lease.consume(90)

    assert lease.consume(20).value is None
    assert lease.bytes_read == 90
    assert budget.total_bytes == 90


def test_next_read_size_offers_one_sentinel_byte() -> None:
    limits = ScanLimits(image_bytes=100, total_bytes=10_000)
    budget, _ = _budget(limits=limits)
    lease = budget.open_response(ResponseKind.IMAGE)

    assert lease.next_read_size(8_192) == 101
    lease.consume(100)
    assert lease.next_read_size(8_192) == 1
    assert lease.remaining == 0


def test_unique_candidates_are_retained_and_deduplicated() -> None:
    budget, _ = _budget()

    assert budget.discover(_url(1)) is DiscoveryDecision.RETAINED
    assert budget.discover(_url(1)) is DiscoveryDecision.DUPLICATE
    assert budget.discover(_url(2)) is DiscoveryDecision.RETAINED
    assert budget.tally.discovered == 2
    assert budget.retained_candidates == (_url(1), _url(2))


def test_candidates_past_the_cap_are_not_retained() -> None:
    limits = ScanLimits(unique_images=3)
    budget, _ = _budget(limits=limits)

    decisions = [budget.discover(_url(index)) for index in range(6)]

    assert decisions.count(DiscoveryDecision.RETAINED) == 3
    assert decisions.count(DiscoveryDecision.CAPPED) == 3
    assert budget.tally.discovered == 3
    assert budget.tally.capped == 3


def test_scheduling_stops_at_the_unique_image_cap() -> None:
    limits = ScanLimits(unique_images=2)
    budget, _ = _budget(limits=limits)
    for index in range(2):
        budget.discover(_url(index))

    assert budget.schedule(_url(0)) is True
    assert budget.schedule(_url(1)) is True
    assert budget.schedule(_url(0)) is False  # already scheduled
    assert budget.can_schedule_image() is False
    assert budget.stop_reason() is ScanTerminalReason.IMAGE_COUNT_LIMIT


def test_scheduling_stops_when_total_bytes_are_exhausted() -> None:
    limits = ScanLimits(total_bytes=100, image_bytes=100)
    budget, _ = _budget(limits=limits)
    budget.discover(_url(1))
    budget.discover(_url(2))
    budget.open_response(ResponseKind.IMAGE).consume(100)

    assert budget.can_schedule_image() is False
    assert budget.stop_reason() is ScanTerminalReason.TOTAL_BYTES_LIMIT


def test_decoded_pixels_are_capped() -> None:
    budget, _ = _budget()

    assert budget.accept_decoded_pixels(40_000_000).failure is None
    assert budget.accept_decoded_pixels(40_000_001).unwrap_failure().code is FailureCode.PIXEL_LIMIT


def test_redirects_are_capped_per_request() -> None:
    budget, _ = _budget()
    chain = budget.open_request_chain()

    for expected in range(1, 6):
        assert chain.follow().unwrap() == expected

    assert chain.follow().unwrap_failure().code is FailureCode.REDIRECT_LIMIT
    assert chain.followed == 5


def test_each_request_gets_its_own_redirect_allowance() -> None:
    budget, _ = _budget()
    first = budget.open_request_chain()
    for _ in range(5):
        first.follow()

    second = budget.open_request_chain()

    assert second.follow().failure is None


def test_elapsed_time_uses_the_monotonic_clock() -> None:
    budget, clock = _budget()

    clock.advance(30.0)

    assert budget.elapsed_seconds == pytest.approx(30.0)
    assert budget.seconds_remaining == pytest.approx(90.0)
    assert budget.is_expired is False


def test_the_scan_expires_at_the_total_interval() -> None:
    budget, clock = _budget()

    clock.advance(120.0)

    assert budget.is_expired is True
    assert budget.seconds_remaining == 0.0
    assert budget.check_continue().unwrap_failure().code is FailureCode.SCAN_TIMEOUT
    assert budget.stop_reason() is ScanTerminalReason.TIMEOUT


def test_the_total_interval_is_not_extended_by_activity() -> None:
    budget, clock = _budget()

    for _ in range(12):
        clock.advance(10.0)
        budget.open_response(ResponseKind.IMAGE).consume(10)

    assert budget.is_expired is True


def test_cancellation_stops_the_scan_immediately() -> None:
    token = CooperativeCancellationToken()
    budget, _ = _budget(cancel=token)
    budget.discover(_url(1))

    token.cancel()

    assert budget.is_cancelled is True
    assert budget.check_continue().unwrap_failure().code is FailureCode.CANCELLED
    assert budget.can_schedule_image() is False
    assert budget.stop_reason() is ScanTerminalReason.CANCELLED


def test_cancellation_outranks_other_stop_reasons() -> None:
    limits = ScanLimits(total_bytes=10, image_bytes=10)
    token = CooperativeCancellationToken()
    budget, clock = _budget(limits=limits, cancel=token)
    budget.open_response(ResponseKind.IMAGE).consume(10)
    clock.advance(200.0)
    token.cancel()

    assert budget.stop_reason() is ScanTerminalReason.CANCELLED


def test_summary_accounting_balances() -> None:
    budget, clock = _budget()
    for index in range(5):
        budget.discover(_url(index))
    for index in range(3):
        budget.schedule(_url(index))
    budget.tally.record(ExtractionKind.VERIFIED)
    budget.tally.record(ExtractionKind.NO_WATERMARK)
    budget.tally.record(ExtractionKind.CORRUPT_WATERMARK)
    budget.open_response(ResponseKind.PAGE).consume(2_000)
    clock.advance(12.5)

    summary = budget.summary()

    assert summary.discovered == 5
    assert summary.attempted == 3
    assert summary.skipped == 2
    assert summary.verified == 1
    assert summary.no_watermark == 1
    assert summary.corrupt == 1
    assert summary.total_response_bytes == 2_000
    assert summary.elapsed_seconds == pytest.approx(12.5)
    assert summary.attempted + summary.skipped == summary.discovered


def test_a_scan_with_all_work_finished_is_complete() -> None:
    budget, _ = _budget()
    budget.discover(_url(1))
    budget.schedule(_url(1))
    budget.tally.record(ExtractionKind.VERIFIED)

    summary = budget.summary()

    assert summary.terminal_reason is ScanTerminalReason.COMPLETED
    assert summary.is_complete is True
    assert summary.skipped == 0


def test_a_scan_stopped_with_work_left_is_incomplete() -> None:
    limits = ScanLimits(unique_images=2)
    budget, _ = _budget(limits=limits)
    budget.discover(_url(1))
    budget.discover(_url(2))
    budget.schedule(_url(1))
    budget.schedule(_url(2))
    # A third candidate was retained before the cap, but never scheduled.
    budget.tally.discover()

    summary = budget.summary()

    assert summary.skipped == 1
    assert summary.terminal_reason is ScanTerminalReason.IMAGE_COUNT_LIMIT
    assert summary.is_complete is False


def test_tally_records_one_outcome_per_category() -> None:
    tally = ScanTally()
    tally.discover()
    tally.attempt()
    tally.record(ExtractionKind.FAILED)

    assert tally.recorded == 1
    assert tally.count(ExtractionKind.FAILED) == 1
    assert tally.count(ExtractionKind.VERIFIED) == 0
    assert tally.skipped == 0


def test_explicit_terminal_reasons_are_preserved() -> None:
    budget, _ = _budget()

    summary = budget.summary(ScanTerminalReason.PAGE_FAILURE)

    assert summary.terminal_reason is ScanTerminalReason.PAGE_FAILURE
    assert summary.is_complete is False
