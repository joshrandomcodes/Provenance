"""Scan orchestration: robots, discovery, retrieval, analysis, and persistence in order.

One scan is one user-authorized pass over one page. The order of operations is a
requirement, not an implementation detail, and the types enforce it:

* ``run`` cannot be called without a ``RobotsDecision``, so robots.txt is always consulted
  before the page is fetched;
* the page is fetched and parsed before any image is scheduled;
* every image is scheduled through the budget, so the unique-image cap, byte ceilings,
  time limit, and cancellation all apply to scheduling rather than being checked after the
  fact.

Robots unavailability is a two-phase pause by design. ``evaluate_robots`` returns the
decision, the caller asks the user to continue or stop, and ``run`` is then given that
decision along with an explicit approval flag. The same ``ScanBudget`` spans the pause, so
the 120-second scan deadline keeps running while the user thinks, exactly as specified.

Failure is always local and always labelled. One image that times out, returns a non-image
body, or exceeds a limit produces exactly one terminal category for that image and leaves
every earlier committed incident untouched. Nothing here substitutes placeholder or
simulated evidence for a failed live lookup.

Analyzed pixels are dropped as soon as an image's outcome exists. Retaining them for
display is the evidence buffer's job, and only for the one incident the user selects.

Requirements: 1.5, 1.6, 8.1-8.13, 9.1-9.5, 10.1-10.7, 18.1-18.5, 18.9, 21.1, 21.5, 21.6
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from provenance.application.cross_validation import DetectionCrossValidator
from provenance.application.image_analysis import ImageAnalyzer
from provenance.domain.errors import Failure, FailureCode, Result, failed, ok
from provenance.domain.models import (
    ExtractionKind,
    NormalizedUrl,
    PageContext,
    ScanSummary,
    ScanTerminalReason,
)
from provenance.domain.scan_budget import ResponseKind, ScanBudget
from provenance.domain.urls import ALLOWED_PORTS, AbsoluteHttpUrl
from provenance.infrastructure.discovery import ImageCandidate, discover_images
from provenance.infrastructure.network.redirects import RedirectFollower
from provenance.infrastructure.network.robots import RobotsDecision, RobotsGate, RobotsVerdict
from provenance.ports.http import SafeRequest

SCAN_OPERATION: Final = "run_scan"

DETAIL_NOT_ACKNOWLEDGED: Final = "scan_not_acknowledged"
DETAIL_ROBOTS_DISALLOWED: Final = "robots_disallowed"
DETAIL_ROBOTS_DECLINED: Final = "robots_unavailable_not_approved"
DETAIL_PAGE_STATUS: Final = "page_status_not_successful"

SUCCESS_MINIMUM: Final = 200
SUCCESS_MAXIMUM: Final = 299

# Requirement 21.5: every scan summary must disclose the limits of static parsing.
STATIC_HTML_LIMITATION: Final = (
    "This scan reads the page's static HTML and does not run browser JavaScript. "
    "Images and content that only appear after JavaScript runs can be missed."
)


def _page_failure_reason(failure: Failure) -> ScanTerminalReason:
    """Classify a page-level failure, keeping cancellation and timeout distinguishable.

    A cancelled or expired scan is not a broken page, and reporting it as one would
    misdescribe what happened to the user.
    """
    match failure.code:
        case FailureCode.CANCELLED:
            return ScanTerminalReason.CANCELLED
        case FailureCode.SCAN_TIMEOUT:
            return ScanTerminalReason.TIMEOUT
        case FailureCode.TOTAL_BYTES_LIMIT:
            return ScanTerminalReason.TOTAL_BYTES_LIMIT
        case _:
            return ScanTerminalReason.PAGE_FAILURE


class ScanStage(StrEnum):
    """Which phase of the scan is currently running."""

    ROBOTS = "robots"
    PAGE = "page"
    DISCOVERY = "discovery"
    IMAGES = "images"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """A snapshot the UI can render while a scan is running."""

    stage: ScanStage
    discovered: int = 0
    attempted: int = 0
    completed: int = 0
    total_bytes: int = 0
    elapsed_seconds: float = 0.0
    current_image: NormalizedUrl | None = None


ProgressSink = Callable[[ScanProgress], None]


@dataclass(frozen=True, slots=True)
class ImageOutcome:
    """Exactly one terminal result for one attempted image."""

    image_url: NormalizedUrl
    kind: ExtractionKind
    context: PageContext
    incident_id: int | None = None
    failure_code: FailureCode | None = None
    detail: str | None = None

    @property
    def is_verified(self) -> bool:
        """True when the Registry confirmed a match and an incident exists."""
        return self.kind is ExtractionKind.VERIFIED and self.incident_id is not None


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Everything one scan produced, complete or not."""

    page_url: NormalizedUrl
    summary: ScanSummary
    outcomes: tuple[ImageOutcome, ...] = ()
    robots: RobotsDecision | None = None
    page_failure: Failure | None = None
    skipped_urls: tuple[NormalizedUrl, ...] = ()
    capped: int = 0
    limitation: str = STATIC_HTML_LIMITATION

    @property
    def terminal_reason(self) -> ScanTerminalReason:
        """Why the scan stopped."""
        return self.summary.terminal_reason

    @property
    def is_complete(self) -> bool:
        """True only when nothing cut the scan short."""
        return self.summary.terminal_reason is ScanTerminalReason.COMPLETED

    @property
    def verified(self) -> tuple[ImageOutcome, ...]:
        """Outcomes that produced a registered match."""
        return tuple(outcome for outcome in self.outcomes if outcome.is_verified)


@dataclass(frozen=True, slots=True)
class ScanRequest:
    """One user-initiated scan of one page."""

    page_url: AbsoluteHttpUrl
    acknowledged: bool = False
    robots_unavailable_approved: bool = False
    progress: ProgressSink | None = field(default=None, compare=False)


class ScanService:
    """Runs one bounded scan against one page."""

    __slots__ = ("_follower", "_robots", "_analyzer", "_validator", "_allowed_ports")

    def __init__(
        self,
        follower: RedirectFollower,
        robots: RobotsGate,
        analyzer: ImageAnalyzer,
        validator: DetectionCrossValidator,
        *,
        allowed_ports: frozenset[int] = ALLOWED_PORTS,
    ) -> None:
        self._follower = follower
        self._robots = robots
        self._analyzer = analyzer
        self._validator = validator
        self._allowed_ports = allowed_ports

    def evaluate_robots(self, request: ScanRequest, budget: ScanBudget) -> Result[RobotsDecision]:
        """Consult robots.txt. Must succeed before ``run`` may be called.

        Separate from ``run`` so an unavailable robots.txt can pause for an explicit user
        choice while this same budget's scan deadline keeps running.
        """
        if not request.acknowledged:
            return failed(
                FailureCode.MISSING_ACKNOWLEDGEMENT,
                SCAN_OPERATION,
                safe_detail=DETAIL_NOT_ACKNOWLEDGED,
            )

        self._report(request, ScanStage.ROBOTS, budget)
        return self._robots.evaluate(request.page_url, budget)

    def run(
        self, request: ScanRequest, budget: ScanBudget, *, robots: RobotsDecision
    ) -> Result[ScanReport]:
        """Scan one page, given an already obtained robots decision."""
        if not request.acknowledged:
            return failed(
                FailureCode.MISSING_ACKNOWLEDGEMENT,
                SCAN_OPERATION,
                safe_detail=DETAIL_NOT_ACKNOWLEDGED,
            )

        if robots.verdict is RobotsVerdict.DISALLOWED:
            return ok(self._stopped(request, budget, robots, ScanTerminalReason.ROBOTS_DISALLOWED))
        if robots.verdict is RobotsVerdict.UNAVAILABLE and not request.robots_unavailable_approved:
            return ok(self._stopped(request, budget, robots, ScanTerminalReason.ROBOTS_DECLINED))

        return self._scan_page(request, budget, robots)

    def _scan_page(
        self, request: ScanRequest, budget: ScanBudget, robots: RobotsDecision
    ) -> Result[ScanReport]:
        self._report(request, ScanStage.PAGE, budget)
        page = self._fetch_page(request, budget)
        if page.failure is not None:
            return ok(
                ScanReport(
                    page_url=request.page_url.normalized,
                    summary=budget.summary(_page_failure_reason(page.failure)),
                    robots=robots,
                    page_failure=page.failure,
                )
            )

        html, final_url = page.unwrap()
        self._report(request, ScanStage.DISCOVERY, budget)
        discovered = discover_images(html, final_url, budget, allowed_ports=self._allowed_ports)
        if discovered.failure is not None:
            return ok(
                ScanReport(
                    page_url=final_url.normalized,
                    summary=budget.summary(_page_failure_reason(discovered.failure)),
                    robots=robots,
                    page_failure=discovered.failure,
                )
            )

        result = discovered.unwrap()
        outcomes, skipped = self._scan_images(
            request, budget, result.candidates, final_url.normalized
        )

        self._report(request, ScanStage.FINISHED, budget)
        return ok(
            ScanReport(
                page_url=final_url.normalized,
                summary=budget.summary(),
                outcomes=outcomes,
                robots=robots,
                skipped_urls=skipped,
                # Candidates beyond the unique-image cap are never retained, so they are
                # reported here rather than silently disappearing from the summary.
                capped=result.capped,
            )
        )

    def _fetch_page(
        self, request: ScanRequest, budget: ScanBudget
    ) -> Result[tuple[bytes, AbsoluteHttpUrl]]:
        attempt = self._follower.fetch(
            SafeRequest(url=request.page_url, kind=ResponseKind.PAGE), budget
        )
        if attempt.failure is not None:
            return Result(failure=attempt.failure)

        outcome = attempt.unwrap()
        response = outcome.response
        status = response.head.status
        if not SUCCESS_MINIMUM <= status <= SUCCESS_MAXIMUM:
            response.close()
            return failed(FailureCode.HTTP_STATUS, SCAN_OPERATION, safe_detail=DETAIL_PAGE_STATUS)

        lease = budget.open_response(ResponseKind.PAGE)
        declared = lease.accept_declared_length(response.head.declared_length)
        if declared.failure is not None:
            response.close()
            return Result(failure=declared.failure)

        body = response.read_body(lease)
        if body.failure is not None:
            return Result(failure=body.failure)
        return ok((body.unwrap(), outcome.final_url))

    def _scan_images(
        self,
        request: ScanRequest,
        budget: ScanBudget,
        candidates: tuple[ImageCandidate, ...],
        page_url: NormalizedUrl,
    ) -> tuple[tuple[ImageOutcome, ...], tuple[NormalizedUrl, ...]]:
        """Attempt candidates in order until a budget limit or cancellation stops us."""
        outcomes: list[ImageOutcome] = []

        for index, candidate in enumerate(candidates):
            if not budget.can_schedule_image() or not budget.schedule(candidate.normalized):
                # Everything from here on was discovered but never attempted.
                return tuple(outcomes), tuple(item.normalized for item in candidates[index:])

            self._report(
                request,
                ScanStage.IMAGES,
                budget,
                current=candidate.normalized,
                completed=len(outcomes),
            )
            outcome = self._process_image(candidate, budget, page_url=page_url)

            # Exactly one terminal category is recorded for every attempted image.
            budget.tally.record(outcome.kind)
            outcomes.append(outcome)

        return tuple(outcomes), ()

    def _process_image(
        self, candidate: ImageCandidate, budget: ScanBudget, *, page_url: NormalizedUrl
    ) -> ImageOutcome:
        """Retrieve, analyze, and cross-validate one image. Never raises."""
        guard = budget.check_continue()
        if guard.failure is not None:
            return self._failed(candidate, guard.failure)

        attempt = self._follower.fetch(
            SafeRequest(url=candidate.image_url, kind=ResponseKind.IMAGE), budget
        )
        if attempt.failure is not None:
            return self._failed(candidate, attempt.failure)

        response = attempt.unwrap().response
        status = response.head.status
        if not SUCCESS_MINIMUM <= status <= SUCCESS_MAXIMUM:
            response.close()
            return self._failed(
                candidate,
                Failure(
                    code=FailureCode.HTTP_STATUS, operation=SCAN_OPERATION, safe_detail=str(status)
                ),
            )

        lease = budget.open_response(ResponseKind.IMAGE)
        declared = lease.accept_declared_length(response.head.declared_length)
        if declared.failure is not None:
            response.close()
            return self._failed(candidate, declared.failure)

        content_type = response.head.content_type
        body = response.read_body(lease)
        if body.failure is not None:
            return self._failed(candidate, body.failure)

        analyzed = self._analyzer.analyze(body.unwrap(), content_type=content_type, budget=budget)
        if analyzed.failure is not None:
            return self._failed(candidate, analyzed.failure)

        # The analyzed image, and with it the decoded pixels, goes out of scope when this
        # method returns. Only the evidence needed to classify the image is carried out.
        evidence = analyzed.unwrap().evidence
        validated = self._validator.cross_validate(
            evidence, page_url=page_url, image_url=candidate.normalized, context=candidate.context
        )
        if validated.failure is not None:
            return self._failed(candidate, validated.failure)

        detection = validated.unwrap()
        return ImageOutcome(
            image_url=candidate.normalized,
            kind=detection.kind,
            context=candidate.context,
            incident_id=None if detection.incident is None else detection.incident.id,
            detail=detection.detail,
        )

    def _failed(self, candidate: ImageCandidate, failure: Failure) -> ImageOutcome:
        """Label one live failure exactly, distinguishing cancellation from error."""
        kind = (
            ExtractionKind.CANCELLED
            if failure.code is FailureCode.CANCELLED
            else ExtractionKind.FAILED
        )
        return ImageOutcome(
            image_url=candidate.normalized,
            kind=kind,
            context=candidate.context,
            failure_code=failure.code,
            detail=failure.safe_detail,
        )

    def _stopped(
        self,
        request: ScanRequest,
        budget: ScanBudget,
        robots: RobotsDecision,
        reason: ScanTerminalReason,
    ) -> ScanReport:
        """A report for a scan that never reached the page."""
        self._report(request, ScanStage.FINISHED, budget)
        return ScanReport(
            page_url=request.page_url.normalized,
            summary=budget.summary(reason),
            robots=robots,
        )

    def _report(
        self,
        request: ScanRequest,
        stage: ScanStage,
        budget: ScanBudget,
        *,
        current: NormalizedUrl | None = None,
        completed: int = 0,
    ) -> None:
        """Publish one progress snapshot, if the caller asked for them."""
        if request.progress is None:
            return
        request.progress(
            ScanProgress(
                stage=stage,
                discovered=budget.tally.discovered,
                attempted=budget.tally.attempted,
                completed=completed,
                total_bytes=budget.total_bytes,
                elapsed_seconds=budget.elapsed_seconds,
                current_image=current,
            )
        )
