"""Web Radar view models: honest counts, named limits, and evidence framing.

Requirements: 9.6, 9.8, 18.9, 19.1, 19.3, 21.4, 21.5
"""

from __future__ import annotations

import pytest

from provenance.application.scan import (
    STATIC_HTML_LIMITATION,
    ImageOutcome,
    ScanProgress,
    ScanReport,
    ScanStage,
)
from provenance.domain.errors import Failure, FailureCode
from provenance.domain.models import (
    ExtractionKind,
    NormalizedUrl,
    PageContext,
    ScanSummary,
    ScanTerminalReason,
)
from provenance.infrastructure.network.robots import RobotsDecision, RobotsVerdict
from provenance.ui.radar_presenter import (
    EVIDENCE_NOTE,
    STATUS_LABELS,
    build_outcome_view,
    build_progress_rows,
    build_progress_text,
    build_report_view,
    build_robots_rows,
)

pytestmark = pytest.mark.unit

PAGE = NormalizedUrl("https://shop.example/product")
IMAGE = NormalizedUrl("https://cdn.example/a.png")


def _summary(
    *,
    discovered: int = 1,
    attempted: int = 1,
    verified: int = 0,
    no_watermark: int = 0,
    corrupt: int = 0,
    unregistered: int = 0,
    failed: int = 0,
    cancelled: int = 0,
    skipped: int = 0,
    total_bytes: int = 2_048,
    elapsed: float = 3.25,
    reason: ScanTerminalReason = ScanTerminalReason.COMPLETED,
) -> ScanSummary:
    return ScanSummary(
        discovered=discovered,
        attempted=attempted,
        verified=verified,
        no_watermark=no_watermark,
        corrupt=corrupt,
        unregistered=unregistered,
        failed=failed,
        cancelled=cancelled,
        skipped=skipped,
        total_response_bytes=total_bytes,
        elapsed_seconds=elapsed,
        terminal_reason=reason,
    )


def _outcome(
    kind: ExtractionKind = ExtractionKind.VERIFIED,
    *,
    incident_id: int | None = 7,
    context: PageContext | None = None,
    failure_code: FailureCode | None = None,
    detail: str | None = None,
) -> ImageOutcome:
    return ImageOutcome(
        image_url=IMAGE,
        kind=kind,
        context=context or PageContext(title="Shop"),
        incident_id=incident_id,
        failure_code=failure_code,
        detail=detail,
    )


def _report(
    *,
    summary: ScanSummary | None = None,
    outcomes: tuple[ImageOutcome, ...] = (),
    robots: RobotsDecision | None = None,
    page_failure: Failure | None = None,
    capped: int = 0,
) -> ScanReport:
    return ScanReport(
        page_url=PAGE,
        summary=summary or _summary(),
        outcomes=outcomes,
        robots=robots,
        page_failure=page_failure,
        capped=capped,
    )


def test_every_extraction_kind_has_a_readable_label() -> None:
    for kind in ExtractionKind:
        assert kind in STATUS_LABELS
        assert STATUS_LABELS[kind] != ""


def test_every_terminal_reason_is_named_in_the_completion_text() -> None:
    for reason in ScanTerminalReason:
        view = build_report_view(_report(summary=_summary(reason=reason)))

        assert view.completion != ""
        assert view.completion != reason.value


def test_a_verified_outcome_is_marked_verified() -> None:
    view = build_outcome_view(_outcome())

    assert view.is_verified is True
    assert view.status == STATUS_LABELS[ExtractionKind.VERIFIED]
    assert view.image_url == str(IMAGE)


def test_a_verified_kind_without_an_incident_is_not_marked_verified() -> None:
    view = build_outcome_view(_outcome(incident_id=None))

    assert view.is_verified is False


def test_a_failure_detail_uses_the_safe_category_not_exception_text() -> None:
    view = build_outcome_view(
        _outcome(
            ExtractionKind.FAILED,
            incident_id=None,
            failure_code=FailureCode.READ_TIMEOUT,
            detail="Traceback: socket exploded at line 42",
        )
    )

    assert view.detail == FailureCode.READ_TIMEOUT.value
    assert "Traceback" not in str(view.detail)


def test_only_context_fields_that_were_found_are_presented() -> None:
    context = PageContext(title="Shop", heading=None, figcaption="Oil on canvas", alt=None)

    view = build_outcome_view(_outcome(context=context))

    assert view.context_rows == (("Page title", "Shop"), ("Caption", "Oil on canvas"))


def test_commerce_evidence_is_carried_through_unchanged() -> None:
    context = PageContext(title="Shop", ecommerce_evidence=("Price: $250.00", "Add to cart"))

    view = build_outcome_view(_outcome(context=context))

    assert view.evidence == ("Price: $250.00", "Add to cart")


def test_a_scan_with_matches_leads_with_the_count() -> None:
    view = build_report_view(
        _report(summary=_summary(verified=2, attempted=3), outcomes=(_outcome(), _outcome()))
    )

    assert view.headline == "2 registered matches found"
    assert view.verified_count == 2


def test_a_single_match_is_described_in_the_singular() -> None:
    view = build_report_view(_report(summary=_summary(verified=1)))

    assert view.headline == "1 registered match found"


def test_a_clean_scan_says_so_plainly() -> None:
    view = build_report_view(_report(summary=_summary(no_watermark=1)))

    assert view.headline == "No registered matches found"


def test_a_scan_that_checked_nothing_says_so() -> None:
    view = build_report_view(_report(summary=_summary(discovered=0, attempted=0)))

    assert view.headline == "No images were checked"
    assert view.has_outcomes is False


def test_the_summary_always_discloses_the_static_html_limitation() -> None:
    view = build_report_view(_report())

    assert STATIC_HTML_LIMITATION in view.notes


def test_the_summary_always_frames_findings_as_evidence() -> None:
    view = build_report_view(_report())

    assert EVIDENCE_NOTE in view.notes


def test_a_match_adds_a_review_before_acting_note() -> None:
    with_match = build_report_view(_report(summary=_summary(verified=1)))
    without = build_report_view(_report(summary=_summary(verified=0)))

    assert len(with_match.notes) == len(without.notes) + 1


def test_an_incomplete_scan_is_flagged_as_partial() -> None:
    view = build_report_view(_report(summary=_summary(reason=ScanTerminalReason.TIMEOUT)))

    assert view.is_complete is False
    assert "time limit" in view.completion


def test_skipped_images_appear_in_the_summary_rows() -> None:
    view = build_report_view(_report(summary=_summary(discovered=5, attempted=2, skipped=3)))
    labels = dict(view.summary_rows)

    assert labels["Discovered but not checked"] == "3"


def test_capped_candidates_appear_in_the_summary_rows() -> None:
    view = build_report_view(_report(capped=4))
    labels = dict(view.summary_rows)

    assert labels["Beyond the unique image limit"] == "4"


def test_clean_scans_omit_the_zero_rows_that_would_only_add_noise() -> None:
    view = build_report_view(_report())
    labels = dict(view.summary_rows)

    assert "Discovered but not checked" not in labels
    assert "Beyond the unique image limit" not in labels
    assert "Cancelled mid-check" not in labels


def test_the_summary_reports_bytes_and_elapsed_time() -> None:
    view = build_report_view(_report(summary=_summary(total_bytes=2_048, elapsed=3.25)))
    labels = dict(view.summary_rows)

    assert labels["Downloaded"] == "2.0 KiB"
    assert labels["Elapsed"] == "3.2 s"


def test_large_downloads_are_reported_in_mebibytes() -> None:
    view = build_report_view(_report(summary=_summary(total_bytes=10_075_000)))

    assert dict(view.summary_rows)["Downloaded"] == "9.61 MiB"


def test_a_page_failure_is_reported_with_a_safe_message() -> None:
    failure = Failure(code=FailureCode.CONNECT_TIMEOUT, operation="run_scan")

    view = build_report_view(
        _report(summary=_summary(reason=ScanTerminalReason.PAGE_FAILURE), page_failure=failure)
    )

    assert view.page_failure_message is not None
    assert "did not answer" in view.page_failure_message


def test_robots_rows_describe_an_allowed_page() -> None:
    decision = RobotsDecision(
        verdict=RobotsVerdict.ALLOWED, robots_url="https://shop.example/robots.txt", status=200
    )

    rows = dict(build_robots_rows(decision))

    assert rows["Rules"] == "This page is not disallowed"
    assert rows["Response status"] == "200"


def test_robots_rows_describe_a_disallowed_page() -> None:
    decision = RobotsDecision(
        verdict=RobotsVerdict.DISALLOWED, robots_url="https://shop.example/robots.txt", status=200
    )

    assert dict(build_robots_rows(decision))["Rules"].startswith("This page is disallowed")


def test_robots_rows_describe_an_unreadable_file() -> None:
    decision = RobotsDecision(
        verdict=RobotsVerdict.UNAVAILABLE,
        robots_url="https://shop.example/robots.txt",
        status=503,
        detail="server_error",
    )

    rows = dict(build_robots_rows(decision))

    assert rows["Rules"] == "Could not be read"
    assert rows["Detail"] == "server_error"


def test_no_robots_decision_yields_no_rows() -> None:
    assert build_robots_rows(None) == ()


def test_progress_text_names_the_current_stage() -> None:
    assert build_progress_text(ScanProgress(stage=ScanStage.ROBOTS)) == "Checking robots.txt"
    assert build_progress_text(ScanProgress(stage=ScanStage.PAGE)) == "Fetching the page"


def test_progress_text_counts_images_once_discovery_has_run() -> None:
    text = build_progress_text(ScanProgress(stage=ScanStage.IMAGES, discovered=10, completed=3))

    assert text == "Checking images: 3 of 10 checked"


def test_progress_text_before_the_first_snapshot_is_stable() -> None:
    assert build_progress_text(None) == "Starting"


def test_progress_rows_report_live_counters() -> None:
    rows = dict(
        build_progress_rows(
            ScanProgress(
                stage=ScanStage.IMAGES,
                discovered=4,
                completed=2,
                total_bytes=1_024,
                elapsed_seconds=12.0,
            )
        )
    )

    assert rows["Discovered"] == "4"
    assert rows["Checked"] == "2"
    assert rows["Downloaded"] == "1.0 KiB"
    assert rows["Elapsed"] == "12 s"


def test_progress_rows_are_empty_before_the_first_snapshot() -> None:
    assert build_progress_rows(None) == ()
