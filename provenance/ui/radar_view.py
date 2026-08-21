"""The Web Radar tab.

Scanning is user-initiated, bounded, and cancellable. The work runs on a session-owned
worker thread and this module only reads snapshots, so no Streamlit call happens off the
main thread.

Live progress uses a fragment that reruns on a timer. That is what makes the Cancel button
real: the rest of the page stays interactive while the scan runs, so the click is delivered
to the running scan rather than queued behind a blocked script.

Every value taken from the scanned page is written through ``safe_render``, so retrieved
titles, captions, alt text, and commerce evidence are inert text and can never become
markup, a link, or a remote image request.

Requirements: 1.1, 8.3, 8.10, 8.11, 9.6, 9.8, 18.3, 19.1-19.4, 19.7-19.9, 21.1, 21.4-21.6
"""

from __future__ import annotations

from typing import Final

import streamlit as st

from provenance.application.scan import ScanRequest
from provenance.application.scan_session import ScanRunner, ScanSession, SessionSnapshot
from provenance.domain.time import Clock
from provenance.domain.urls import parse_page_input
from provenance.ui import safe_render
from provenance.ui.messages import message_for
from provenance.ui.radar_presenter import (
    ROBOTS_PROMPT,
    OutcomeView,
    ReportView,
    build_progress_rows,
    build_progress_text,
    build_report_view,
    build_robots_rows,
)

SESSION_KEY: Final = "radar_session"
ACK_KEY: Final = "radar_acknowledged"
URL_KEY: Final = "radar_page_url"
ERROR_KEY: Final = "radar_input_error"

POLL_SECONDS: Final = 1.0

INTRO: Final = (
    "Check one public page for your registered images. Provenance fetches the page and its "
    "images directly from this computer, reads only static HTML, and stores no image data."
)

ACK_LABEL: Final = (
    "I am authorized to request this page, and I accept responsibility for this scan."
)
URL_LABEL: Final = "Page address"
START_LABEL: Final = "Scan this page"
CANCEL_LABEL: Final = "Cancel scan"
CONTINUE_LABEL: Final = "Continue without robots.txt"
ABANDON_LABEL: Final = "Stop this scan"
CLEAR_LABEL: Final = "Clear results"

BOUNDS_NOTE: Final = (
    "Each scan is limited to 2 MiB of HTML, 100 unique images, 10 MiB per image, "
    "50 MiB in total, and 120 seconds."
)


def render_radar_tab(runner: ScanRunner, clock: Clock, *, writes_enabled: bool) -> None:
    """Render the Web Radar tab for one browser session."""
    st.header("Web Radar")
    safe_render.caption(INTRO)
    safe_render.caption(BOUNDS_NOTE)

    if not writes_enabled:
        st.error(
            "Scanning is disabled because the local registry did not pass its startup "
            "checks, so findings could not be recorded. See the local status panel."
        )
        return

    session = _session(runner, clock)
    snapshot = session.snapshot()

    if snapshot.is_running:
        _render_live(session)
        return

    if snapshot.awaiting_robots_decision:
        _render_robots_decision(session, snapshot)
        return

    _render_form(session)
    _render_outcome(session, snapshot)


def _session(runner: ScanRunner, clock: Clock) -> ScanSession:
    """One scan session per browser session, held in Streamlit session state."""
    existing = st.session_state.get(SESSION_KEY)
    if isinstance(existing, ScanSession):
        return existing
    created = ScanSession(runner, clock)
    st.session_state[SESSION_KEY] = created
    return created


def _render_form(session: ScanSession) -> None:
    """The acknowledgement gate and the address form."""
    acknowledged = st.checkbox(ACK_LABEL, key=ACK_KEY)
    page_url = st.text_input(
        URL_LABEL,
        key=URL_KEY,
        max_chars=2_000,
        placeholder="https://example.com/page",
        help="Only https:// and http:// addresses on the standard web ports are accepted.",
    )

    if st.button(START_LABEL, disabled=not acknowledged):
        _start(session, page_url, acknowledged=acknowledged)

    stored_error = st.session_state.get(ERROR_KEY)
    if isinstance(stored_error, str) and stored_error != "":
        st.warning(stored_error)

    if not acknowledged:
        safe_render.caption(
            "Confirm the authorization statement above to enable scanning. "
            "Provenance never starts a scan on its own."
        )


def _start(session: ScanSession, raw_url: str, *, acknowledged: bool) -> None:
    """Validate the address and hand one scan to the worker."""
    parsed = parse_page_input(raw_url)
    if parsed.failure is not None:
        st.session_state[ERROR_KEY] = message_for(parsed.unwrap_failure())
        return

    session.reset()
    failure = session.start(ScanRequest(page_url=parsed.unwrap(), acknowledged=acknowledged))
    if failure is not None:
        st.session_state[ERROR_KEY] = message_for(failure)
        return

    st.session_state[ERROR_KEY] = ""
    st.rerun()


def _render_live(session: ScanSession) -> None:
    """Live progress and a working Cancel control."""
    st.subheader("Scanning")

    @st.fragment(run_every=POLL_SECONDS)
    def live_panel() -> None:
        snapshot = session.snapshot()
        st.text(build_progress_text(snapshot.progress))
        safe_render.detail_rows(build_progress_rows(snapshot.progress))

        if st.button(CANCEL_LABEL, key="radar_cancel"):
            session.cancel()
            st.text("Cancelling. Results already collected are kept.")

        if not snapshot.is_running:
            # The worker finished; leave the fragment and redraw the whole tab.
            st.rerun(scope="app")

    live_panel()
    safe_render.caption(
        "Cancelling stops further requests. Images already checked keep their results."
    )


def _render_robots_decision(session: ScanSession, snapshot: SessionSnapshot) -> None:
    """The explicit continue-or-stop choice when robots.txt could not be read."""
    st.subheader("robots.txt could not be read")
    st.warning(ROBOTS_PROMPT)
    safe_render.detail_rows(build_robots_rows(snapshot.robots))

    continue_column, stop_column = st.columns(2)
    with continue_column:
        if st.button(CONTINUE_LABEL, key="radar_robots_continue"):
            session.resume()
            st.rerun()
    with stop_column:
        if st.button(ABANDON_LABEL, key="radar_robots_stop"):
            session.reset()
            st.rerun()


def _render_outcome(session: ScanSession, snapshot: SessionSnapshot) -> None:
    """Whatever the last scan produced: a report, a failure, or nothing yet."""
    if snapshot.failure is not None:
        st.subheader("Scan did not run")
        safe_render.text(message_for(snapshot.failure))
        _clear_button(session)
        return

    if snapshot.report is None:
        return

    _render_report(build_report_view(snapshot.report))
    _clear_button(session)


def _render_report(view: ReportView) -> None:
    """Render one finished scan's findings as inert text."""
    st.subheader(view.headline)
    safe_render.labelled("Page", view.page_url)
    safe_render.labelled("Outcome", view.completion)

    if not view.is_complete:
        st.info(f"This scan did not finish, so the findings below are partial. {view.completion}.")

    if view.page_failure_message is not None:
        st.warning(view.page_failure_message)

    if view.robots_rows:
        st.divider()
        st.text("robots.txt")
        safe_render.detail_rows(view.robots_rows)

    st.divider()
    st.text("Summary")
    safe_render.detail_rows(view.summary_rows)

    for note in view.notes:
        safe_render.caption(note)

    if not view.has_outcomes:
        return

    st.divider()
    st.text(f"Images checked ({len(view.outcomes)})")
    for index, outcome in enumerate(view.outcomes):
        _render_outcome_row(outcome, index)


def _render_outcome_row(outcome: OutcomeView, index: int) -> None:
    """One image's result, with its evidence kept inert."""
    heading = f"{index + 1}. {outcome.status}"
    with st.expander(heading, expanded=outcome.is_verified):
        safe_render.labelled("Image address", outcome.image_url)
        safe_render.labelled("Result", outcome.status)
        if outcome.detail is not None:
            safe_render.labelled("Detail", outcome.detail)

        if outcome.context_rows:
            st.text("Page context found next to this image:")
            safe_render.detail_rows(outcome.context_rows)

        if outcome.evidence:
            st.text("Commerce indicators found near this image, shown verbatim:")
            for item in outcome.evidence:
                safe_render.evidence_block(item)
            safe_render.caption(
                "Commerce wording is evidence of how the page presents the image. "
                "It does not establish infringement."
            )


def _clear_button(session: ScanSession) -> None:
    if st.button(CLEAR_LABEL, key="radar_clear"):
        session.reset()
        st.session_state[ERROR_KEY] = ""
        st.rerun()
