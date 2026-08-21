"""View models for the Web Radar tab.

Kept free of Streamlit so the wording, labelling, and counting of scan results can be
tested directly. Every value that came from a remote page is passed through unchanged here
and rendered inertly by the view; this module never marks anything up.

Two rules shape the wording. Findings are described as evidence and never as a legal
conclusion, and a limit that cut a scan short is always named so a partial result cannot be
mistaken for a complete one.

Requirements: 9.6, 9.8, 18.9, 19.1, 19.3, 21.4, 21.5
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from provenance.application.scan import ImageOutcome, ScanProgress, ScanReport, ScanStage
from provenance.domain.models import ExtractionKind, PageContext, ScanTerminalReason
from provenance.infrastructure.network.robots import RobotsDecision, RobotsVerdict
from provenance.ui.messages import message_for

EVIDENCE_NOTE: Final = (
    "These findings are evidence for your review. On their own or together they do not "
    "determine ownership, infringement, or fair use."
)

VERIFIED_NOTE: Final = (
    "A registered match means the watermark named one of your registered images and your "
    "creator ID. Review each incident before taking any action."
)

STATUS_LABELS: Final = {
    ExtractionKind.VERIFIED: "Registered match",
    ExtractionKind.NO_WATERMARK: "No Provenance watermark found",
    ExtractionKind.CORRUPT_WATERMARK: "Marker found but did not verify",
    ExtractionKind.UNREGISTERED: "Valid watermark, not in your registry",
    ExtractionKind.FAILED: "Could not be checked",
    ExtractionKind.CANCELLED: "Cancelled before completion",
}

TERMINAL_LABELS: Final = {
    ScanTerminalReason.COMPLETED: "Completed",
    ScanTerminalReason.PAGE_FAILURE: "Stopped: the page could not be read",
    ScanTerminalReason.ROBOTS_DISALLOWED: "Stopped: robots.txt disallows this page",
    ScanTerminalReason.ROBOTS_DECLINED: ("Stopped: you chose not to continue without robots.txt"),
    ScanTerminalReason.IMAGE_COUNT_LIMIT: "Stopped: reached the unique image limit",
    ScanTerminalReason.TOTAL_BYTES_LIMIT: "Stopped: reached the total download limit",
    ScanTerminalReason.TIMEOUT: "Stopped: reached the time limit",
    ScanTerminalReason.CANCELLED: "Stopped: cancelled",
}

STAGE_LABELS: Final = {
    ScanStage.ROBOTS: "Checking robots.txt",
    ScanStage.PAGE: "Fetching the page",
    ScanStage.DISCOVERY: "Reading the page for images",
    ScanStage.IMAGES: "Checking images",
    ScanStage.FINISHED: "Finishing",
}

ROBOTS_PROMPT: Final = (
    "This site's robots.txt could not be read, so its crawling rules are unknown. "
    "Continue only if you are authorized to fetch this page. The scan's time limit has "
    "continued running while this question was open."
)


def _kilobytes(value: int) -> str:
    return f"{value / 1024:.1f} KiB"


@dataclass(frozen=True, slots=True)
class OutcomeView:
    """One image result prepared for inert display."""

    image_url: str
    status: str
    is_verified: bool
    detail: str | None = None
    context_rows: tuple[tuple[str, str], ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportView:
    """One finished scan prepared for inert display."""

    page_url: str
    headline: str
    completion: str
    is_complete: bool
    summary_rows: tuple[tuple[str, str], ...]
    outcomes: tuple[OutcomeView, ...]
    robots_rows: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]
    page_failure_message: str | None = None

    @property
    def verified_count(self) -> int:
        """Registered matches found."""
        return sum(1 for outcome in self.outcomes if outcome.is_verified)

    @property
    def has_outcomes(self) -> bool:
        """True when at least one image was attempted."""
        return len(self.outcomes) > 0


def _context_rows(context: PageContext) -> tuple[tuple[str, str], ...]:
    """Present only the context fields that were actually found."""
    candidates = (
        ("Page title", context.title),
        ("Nearest heading", context.heading),
        ("Caption", context.figcaption),
        ("Alt text", context.alt),
    )
    return tuple((label, value) for label, value in candidates if value is not None)


def build_outcome_view(outcome: ImageOutcome) -> OutcomeView:
    """Prepare one image outcome for display."""
    # A live failure is named by its safe category, never by exception text.
    detail = outcome.failure_code.value if outcome.failure_code is not None else outcome.detail

    return OutcomeView(
        image_url=str(outcome.image_url),
        status=STATUS_LABELS.get(outcome.kind, outcome.kind.value),
        is_verified=outcome.is_verified,
        detail=detail,
        context_rows=_context_rows(outcome.context),
        evidence=outcome.context.ecommerce_evidence,
    )


def build_robots_rows(decision: RobotsDecision | None) -> tuple[tuple[str, str], ...]:
    """Describe what robots.txt said, including when it could not be read."""
    if decision is None:
        return ()

    rows: list[tuple[str, str]] = [("robots.txt", decision.robots_url)]
    match decision.verdict:
        case RobotsVerdict.ALLOWED:
            rows.append(("Rules", "This page is not disallowed"))
        case RobotsVerdict.DISALLOWED:
            rows.append(("Rules", "This page is disallowed for our user agent"))
        case RobotsVerdict.UNAVAILABLE:
            rows.append(("Rules", "Could not be read"))

    if decision.status is not None:
        rows.append(("Response status", str(decision.status)))
    if decision.detail is not None:
        rows.append(("Detail", decision.detail))
    return tuple(rows)


def _summary_rows(report: ScanReport) -> tuple[tuple[str, str], ...]:
    summary = report.summary
    rows: list[tuple[str, str]] = [
        ("Images discovered", str(summary.discovered)),
        ("Images checked", str(summary.attempted)),
        ("Registered matches", str(summary.verified)),
        ("Valid but unregistered", str(summary.unregistered)),
        ("No watermark", str(summary.no_watermark)),
        ("Marker did not verify", str(summary.corrupt)),
        ("Could not be checked", str(summary.failed)),
    ]
    if summary.cancelled:
        rows.append(("Cancelled mid-check", str(summary.cancelled)))
    if summary.skipped:
        rows.append(("Discovered but not checked", str(summary.skipped)))
    if report.capped:
        rows.append(("Beyond the unique image limit", str(report.capped)))
    rows.append(("Downloaded", _kilobytes(summary.total_response_bytes)))
    rows.append(("Elapsed", f"{summary.elapsed_seconds:.1f} s"))
    return tuple(rows)


def _notes(report: ScanReport) -> tuple[str, ...]:
    notes = [report.limitation, EVIDENCE_NOTE]
    if report.summary.verified:
        notes.append(VERIFIED_NOTE)
    return tuple(notes)


def build_report_view(report: ScanReport) -> ReportView:
    """Prepare one finished scan for display."""
    verified = report.summary.verified
    if verified:
        headline = f"{verified} registered match{'es' if verified != 1 else ''} found"
    elif report.summary.attempted:
        headline = "No registered matches found"
    else:
        headline = "No images were checked"

    return ReportView(
        page_url=str(report.page_url),
        headline=headline,
        completion=TERMINAL_LABELS.get(report.terminal_reason, report.terminal_reason.value),
        is_complete=report.is_complete,
        summary_rows=_summary_rows(report),
        outcomes=tuple(build_outcome_view(outcome) for outcome in report.outcomes),
        robots_rows=build_robots_rows(report.robots),
        notes=_notes(report),
        page_failure_message=(
            None if report.page_failure is None else message_for(report.page_failure)
        ),
    )


def build_progress_text(progress: ScanProgress | None) -> str:
    """One line describing what the scan is doing right now."""
    if progress is None:
        return "Starting"

    stage = STAGE_LABELS.get(progress.stage, progress.stage.value)
    if progress.stage is ScanStage.IMAGES and progress.discovered:
        return f"{stage}: {progress.completed} of {progress.discovered} checked"
    return stage


def build_progress_rows(progress: ScanProgress | None) -> tuple[tuple[str, str], ...]:
    """Live counters for the running scan."""
    if progress is None:
        return ()
    return (
        ("Discovered", str(progress.discovered)),
        ("Checked", str(progress.completed)),
        ("Downloaded", _kilobytes(progress.total_bytes)),
        ("Elapsed", f"{progress.elapsed_seconds:.0f} s"),
    )
