"""Scan resource accounting, cancellation, and terminal summaries.

Every limit is enforced before bytes are retained or pixels are allocated, so a
hostile or merely large site cannot push the scan past its budget. Byte accounting is
charged by exact chunk length, which makes totals independent of transfer chunk
boundaries.

Durations use a monotonic clock only. The next-byte interval restarts when a byte
arrives; the total scan interval never restarts or extends, including across a pause
waiting for the user's robots.txt decision.

Requirements: 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 18.1, 18.3, 18.4, 18.5,
18.9, 20.17
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from provenance.domain.cancellation import CancellationToken, NeverCancelled
from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    ExtractionKind,
    NormalizedUrl,
    ScanSummary,
    ScanTerminalReason,
)
from provenance.domain.time import Clock

MEBIBYTE: Final = 1_048_576


class ResponseKind(StrEnum):
    """Which budget line a response body is charged against."""

    ROBOTS = "robots"
    PAGE = "page"
    REDIRECT = "redirect"
    IMAGE = "image"


class DiscoveryDecision(StrEnum):
    """What happened to one discovered image candidate."""

    RETAINED = "retained"
    DUPLICATE = "duplicate"
    CAPPED = "capped"


@dataclass(frozen=True, slots=True)
class ScanLimits:
    """The Scan_Budget. Specified limits are exact; two are defensive additions."""

    html_bytes: int = 2 * MEBIBYTE
    image_bytes: int = 10 * MEBIBYTE
    total_bytes: int = 50 * MEBIBYTE
    unique_images: int = 100
    decoded_pixels: int = 40_000_000
    redirects_per_request: int = 5
    connect_seconds: float = 5.0
    next_byte_seconds: float = 15.0
    total_seconds: float = 120.0

    # The requirements do not size these two bodies. robots.txt is text, so it gets the
    # HTML allowance, and redirect bodies are discarded, so they get a small one. Both
    # are additionally charged against total_bytes.
    robots_bytes: int = 2 * MEBIBYTE
    redirect_body_bytes: int = 64 * 1024

    def limit_for(self, kind: ResponseKind) -> int:
        """Per-response byte ceiling for one response kind."""
        match kind:
            case ResponseKind.PAGE:
                return self.html_bytes
            case ResponseKind.ROBOTS:
                return self.robots_bytes
            case ResponseKind.IMAGE:
                return self.image_bytes
            case ResponseKind.REDIRECT:
                return self.redirect_body_bytes

    def code_for(self, kind: ResponseKind) -> FailureCode:
        """Failure code reported when one response kind exceeds its ceiling."""
        match kind:
            case ResponseKind.PAGE | ResponseKind.ROBOTS | ResponseKind.REDIRECT:
                return FailureCode.HTML_LIMIT
            case ResponseKind.IMAGE:
                return FailureCode.IMAGE_BYTES_LIMIT


BUDGET_OPERATION: Final = "scan_budget"


class ResponseLease:
    """Byte accounting for one response body."""

    __slots__ = ("_budget", "_kind", "_limit", "_bytes")

    def __init__(self, budget: ScanBudget, kind: ResponseKind, limit: int) -> None:
        self._budget = budget
        self._kind = kind
        self._limit = limit
        self._bytes = 0

    @property
    def kind(self) -> ResponseKind:
        """Which budget line this response is charged against."""
        return self._kind

    @property
    def bytes_read(self) -> int:
        """Bytes charged so far for this response."""
        return self._bytes

    @property
    def remaining(self) -> int:
        """Bytes still permitted, honoring both the per-response and total ceilings."""
        return max(0, min(self._limit - self._bytes, self._budget.total_bytes_remaining))

    def accept_declared_length(self, declared: int | None) -> Result[int]:
        """Reject an over-limit declared body length before reading anything."""
        if declared is None:
            return ok(0)
        if declared < 0:
            return failed(FailureCode.HTTP_STATUS, BUDGET_OPERATION, safe_detail="bad_length")
        if declared > self._limit - self._bytes:
            return failed(self._budget.limits.code_for(self._kind), BUDGET_OPERATION)
        if declared > self._budget.total_bytes_remaining:
            return failed(FailureCode.TOTAL_BYTES_LIMIT, BUDGET_OPERATION)
        return ok(declared)

    def consume(self, size: int) -> Result[int]:
        """Charge received bytes. Called before the bytes are retained or parsed."""
        if size <= 0:
            return ok(self._bytes)
        if self._bytes + size > self._limit:
            return failed(self._budget.limits.code_for(self._kind), BUDGET_OPERATION)
        if size > self._budget.total_bytes_remaining:
            return failed(FailureCode.TOTAL_BYTES_LIMIT, BUDGET_OPERATION)

        self._bytes += size
        self._budget.charge_total(size)
        return ok(self._bytes)

    def next_read_size(self, preferred: int) -> int:
        """Size to request next: never more than the allowance plus one sentinel byte.

        The sentinel makes an over-limit body detectable without retaining it.
        """
        return max(1, min(preferred, self.remaining + 1))


@dataclass(slots=True)
class ScanTally:
    """Counts every discovered candidate and every terminal image outcome."""

    discovered: int = 0
    attempted: int = 0
    capped: int = 0
    outcomes: dict[ExtractionKind, int] = field(default_factory=dict)

    def discover(self) -> None:
        """Record one retained unique candidate."""
        self.discovered += 1

    def cap(self) -> None:
        """Record one candidate dropped because the unique-image cap was reached."""
        self.capped += 1

    def attempt(self) -> None:
        """Record that validation, retrieval, or analysis began for one candidate."""
        self.attempted += 1

    def record(self, kind: ExtractionKind) -> None:
        """Record exactly one terminal outcome for an attempted candidate."""
        self.outcomes[kind] = self.outcomes.get(kind, 0) + 1

    def count(self, kind: ExtractionKind) -> int:
        """Outcomes recorded for one category."""
        return self.outcomes.get(kind, 0)

    @property
    def recorded(self) -> int:
        """Total terminal outcomes recorded."""
        return sum(self.outcomes.values())

    @property
    def skipped(self) -> int:
        """Discovered candidates for which no attempt began."""
        return max(0, self.discovered - self.attempted)

    def summary(
        self, reason: ScanTerminalReason, total_response_bytes: int, elapsed_seconds: float
    ) -> ScanSummary:
        """Build the terminal summary for display."""
        return ScanSummary(
            discovered=self.discovered,
            attempted=self.attempted,
            verified=self.count(ExtractionKind.VERIFIED),
            no_watermark=self.count(ExtractionKind.NO_WATERMARK),
            corrupt=self.count(ExtractionKind.CORRUPT_WATERMARK),
            unregistered=self.count(ExtractionKind.UNREGISTERED),
            failed=self.count(ExtractionKind.FAILED),
            cancelled=self.count(ExtractionKind.CANCELLED),
            skipped=self.skipped,
            total_response_bytes=total_response_bytes,
            elapsed_seconds=elapsed_seconds,
            terminal_reason=reason,
        )


class ScanBudget:
    """Mutable, thread-confined accounting for one scan."""

    __slots__ = ("_limits", "_clock", "_cancel", "_started", "_total_bytes", "_candidates", "tally")

    def __init__(
        self,
        limits: ScanLimits,
        clock: Clock,
        cancel: CancellationToken | None = None,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._cancel = cancel if cancel is not None else NeverCancelled()
        self._started = clock.monotonic()
        self._total_bytes = 0
        self._candidates: dict[NormalizedUrl, bool] = {}
        self.tally = ScanTally()

    @property
    def limits(self) -> ScanLimits:
        """The limits this scan runs under."""
        return self._limits

    @property
    def total_bytes(self) -> int:
        """Response-body bytes charged across every request in this scan."""
        return self._total_bytes

    @property
    def total_bytes_remaining(self) -> int:
        """Response-body bytes still permitted for the whole scan."""
        return max(0, self._limits.total_bytes - self._total_bytes)

    @property
    def elapsed_seconds(self) -> float:
        """Monotonic seconds since the scan started."""
        return self._clock.monotonic() - self._started

    @property
    def seconds_remaining(self) -> float:
        """Monotonic seconds left before the scan must stop."""
        return max(0.0, self._limits.total_seconds - self.elapsed_seconds)

    @property
    def is_expired(self) -> bool:
        """True once the total scan interval has elapsed."""
        return self.elapsed_seconds >= self._limits.total_seconds

    @property
    def is_cancelled(self) -> bool:
        """True once the user cancelled the scan."""
        return self._cancel.is_cancelled

    @property
    def scheduled_images(self) -> int:
        """Unique candidates for which retrieval was scheduled."""
        return sum(1 for scheduled in self._candidates.values() if scheduled)

    @property
    def retained_candidates(self) -> tuple[NormalizedUrl, ...]:
        """Unique retained candidates in first-occurrence order."""
        return tuple(self._candidates)

    def charge_total(self, size: int) -> None:
        """Add bytes to the scan total. Called only by a response lease."""
        self._total_bytes += size

    def check_continue(self) -> Result[bool]:
        """Refuse to continue once cancelled or out of time."""
        if self._cancel.is_cancelled:
            return failed(FailureCode.CANCELLED, BUDGET_OPERATION)
        if self.is_expired:
            return failed(FailureCode.SCAN_TIMEOUT, BUDGET_OPERATION)
        return ok(True)

    def open_response(self, kind: ResponseKind) -> ResponseLease:
        """Start accounting for one response body."""
        return ResponseLease(self, kind, self._limits.limit_for(kind))

    def discover(self, url: NormalizedUrl) -> DiscoveryDecision:
        """Retain a unique candidate until the unique-image cap is reached."""
        if url in self._candidates:
            return DiscoveryDecision.DUPLICATE
        if len(self._candidates) >= self._limits.unique_images:
            self.tally.cap()
            return DiscoveryDecision.CAPPED

        self._candidates[url] = False
        self.tally.discover()
        return DiscoveryDecision.RETAINED

    def can_schedule_image(self) -> bool:
        """True when another image request may start under every current limit."""
        return (
            not self._cancel.is_cancelled
            and not self.is_expired
            and self.total_bytes_remaining > 0
            and self.scheduled_images < self._limits.unique_images
        )

    def schedule(self, url: NormalizedUrl) -> bool:
        """Mark a retained candidate as attempted, if scheduling is still permitted."""
        if url not in self._candidates or self._candidates[url]:
            return False
        if not self.can_schedule_image():
            return False
        self._candidates[url] = True
        self.tally.attempt()
        return True

    def accept_decoded_pixels(self, pixels: int) -> Result[int]:
        """Reject an image whose decoded pixel count exceeds the ceiling."""
        if pixels > self._limits.decoded_pixels:
            return failed(FailureCode.PIXEL_LIMIT, BUDGET_OPERATION)
        return ok(pixels)

    def open_request_chain(self) -> RedirectChain:
        """Start redirect accounting for one logical request."""
        return RedirectChain(self._limits.redirects_per_request)

    def stop_reason(self) -> ScanTerminalReason | None:
        """The budget-driven reason to stop, or None when work may continue."""
        if self._cancel.is_cancelled:
            return ScanTerminalReason.CANCELLED
        if self.is_expired:
            return ScanTerminalReason.TIMEOUT
        if self.total_bytes_remaining <= 0:
            return ScanTerminalReason.TOTAL_BYTES_LIMIT
        if self.scheduled_images >= self._limits.unique_images:
            return ScanTerminalReason.IMAGE_COUNT_LIMIT
        return None

    def terminal_reason(self) -> ScanTerminalReason:
        """Completed only when nothing stopped the scan and no candidate was skipped."""
        stop = self.stop_reason()
        if stop is not None and self.tally.skipped > 0:
            return stop
        if stop in {ScanTerminalReason.CANCELLED, ScanTerminalReason.TIMEOUT}:
            return stop
        return ScanTerminalReason.COMPLETED

    def summary(self, reason: ScanTerminalReason | None = None) -> ScanSummary:
        """Build the terminal summary, resolving the reason when not supplied."""
        return self.tally.summary(
            reason if reason is not None else self.terminal_reason(),
            self._total_bytes,
            self.elapsed_seconds,
        )


class RedirectChain:
    """Redirect accounting for one logical request."""

    __slots__ = ("_limit", "_followed")

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._followed = 0

    @property
    def followed(self) -> int:
        """Redirects followed so far."""
        return self._followed

    def follow(self) -> Result[int]:
        """Permit one more redirect, or refuse at the limit."""
        if self._followed >= self._limit:
            return failed(FailureCode.REDIRECT_LIMIT, BUDGET_OPERATION)
        self._followed += 1
        return ok(self._followed)
