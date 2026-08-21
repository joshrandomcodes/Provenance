"""The Incident Triage tab.

Reviewing evidence is free; changing anything is not. Every mutation goes through the same
three steps: read the evidence, review a preview of exactly what would change, then
confirm. Cancelling at any point leaves the incident and its evidence untouched, and a
failure keeps the evidence on screen with the action still available to retry.

The preview is bound to a fingerprint, so if the incident or the rationale changes after a
review the confirmation is refused and the creator is asked to look again.

Every value taken from a scanned page is written through ``safe_render``, so titles,
captions, alt text, and commerce wording stay inert text.

Requirements: 1.1, 11.1-11.9, 12.1-12.8, 19.1, 19.3, 19.8, 21.4, 21.6
"""

from __future__ import annotations

from typing import Final

import streamlit as st

from provenance.application.triage import (
    ActionOutcome,
    ActionPreview,
    IncidentEvidence,
    TriageAction,
    TriageService,
)
from provenance.domain.errors import Failure, FailureCode
from provenance.ui import safe_render
from provenance.ui.messages import label_for, message_for
from provenance.ui.triage_presenter import (
    EMPTY_ACTIVE_NOTE,
    EMPTY_FAIR_USE_NOTE,
    EVIDENCE_NOTE,
    PENDING_ACTIONS_NOTE,
    RATIONALE_PROMPT,
    EvidenceView,
    build_evidence_view,
    build_incident_options,
    build_outcome_view,
    build_preview_view,
)

ACTIVE_VIEW: Final = "Active incidents"
FAIR_USE_VIEW: Final = "Fair use"
VIEW_OPTIONS: Final = (ACTIVE_VIEW, FAIR_USE_VIEW)

VIEW_KEY: Final = "triage_view"
SELECT_KEY: Final = "triage_selected"
RATIONALE_KEY: Final = "triage_rationale"
PREVIEW_KEY: Final = "triage_preview"
OUTCOME_KEY: Final = "triage_outcome"
FAILURE_KEY: Final = "triage_failure"

VIEW_LABEL: Final = "Which incidents"
SELECT_LABEL: Final = "Incident"
RATIONALE_LABEL: Final = "Fair use rationale"
REVIEW_MARK_LABEL: Final = "Review marking this fair use"
REVIEW_REMOVE_LABEL: Final = "Review removing fair use"
CONFIRM_LABEL: Final = "Confirm"
CANCEL_LABEL: Final = "Cancel"

REMOVAL_NOTE: Final = (
    "Removing the entry returns every unresolved fair-use incident on this exact page to "
    "Detected. It authorizes nothing."
)

RATIONALE_ONLY_NOTE: Final = "No incident status changes. Only the stored rationale is updated."

DISABLED_NOTE: Final = (
    "Triage is disabled because the local registry did not pass its startup checks. "
    "See the local status panel."
)


def _forget(key: str) -> None:
    """Drop one session value if it is present."""
    if key in st.session_state:
        del st.session_state[key]


def render_triage_tab(service: TriageService, *, writes_enabled: bool) -> None:
    """Render the Incident Triage tab for one browser session."""
    st.header("Incident Triage")
    safe_render.caption(EVIDENCE_NOTE)
    safe_render.caption(PENDING_ACTIONS_NOTE)

    if not writes_enabled:
        st.error(DISABLED_NOTE)
        return

    loaded = service.load()
    if loaded.failure is not None:
        st.error(message_for(loaded.unwrap_failure()))
        return
    snapshot = loaded.unwrap()

    _render_outcome()

    chosen = st.radio(VIEW_LABEL, VIEW_OPTIONS, horizontal=True, key=VIEW_KEY)
    showing_active = chosen != FAIR_USE_VIEW
    incidents = snapshot.active if showing_active else snapshot.fair_use
    if not incidents:
        st.text(EMPTY_ACTIVE_NOTE if showing_active else EMPTY_FAIR_USE_NOTE)
        return

    options = build_incident_options(incidents)
    labels = {option.incident_id: option.label for option in options}
    # `index` keeps its default of 0, so a selection always exists while the list is
    # non-empty and the returned identifier is never None.
    selected_id = st.selectbox(
        SELECT_LABEL,
        tuple(labels),
        format_func=lambda value: labels[value],
        key=SELECT_KEY,
    )

    evidence = service.evidence(selected_id)
    if evidence.failure is not None:
        st.warning(message_for(evidence.unwrap_failure()))
        return

    found = evidence.unwrap()
    _render_evidence(build_evidence_view(found))
    _render_failure()
    _render_actions(service, found)


def _render_evidence(view: EvidenceView) -> None:
    """Show one incident's recorded evidence as inert text."""
    st.subheader(view.headline)
    safe_render.labelled("Status", view.status)

    st.divider()
    st.text("Where it was found")
    safe_render.detail_rows(view.location_rows)

    st.text("Identity")
    safe_render.detail_rows(view.identity_rows)

    st.text("Timing")
    safe_render.detail_rows(view.timing_rows)

    st.divider()
    st.text("Page context found next to this image")
    safe_render.detail_rows(view.context_rows)

    if view.has_ecommerce_evidence:
        st.text("Commerce indicators found near this image, shown verbatim:")
        for item in view.ecommerce_evidence:
            safe_render.evidence_block(item)

    st.divider()
    st.text("Image comparison")
    safe_render.detail_rows(view.placeholders)

    if view.fair_use_rows:
        st.divider()
        st.text("Fair-use entry for this exact page")
        safe_render.detail_rows(view.fair_use_rows)

    safe_render.caption(view.scope_note)
    safe_render.caption(view.evidence_note)


def _render_actions(service: TriageService, evidence: IncidentEvidence) -> None:
    """Offer the available decisions, or the pending confirmation for this incident."""
    pending = st.session_state.get(PREVIEW_KEY)
    if isinstance(pending, ActionPreview) and pending.incident_id == evidence.incident.id:
        _render_confirmation(service, pending)
        return

    st.divider()
    st.text("Record a decision")

    for action in evidence.available_actions:
        if action is TriageAction.MARK_FAIR_USE:
            _render_mark_control(service, evidence)
        else:
            _render_remove_control(service, evidence)


def _render_mark_control(service: TriageService, evidence: IncidentEvidence) -> None:
    """The rationale field and the review button for marking fair use."""
    safe_render.caption(RATIONALE_PROMPT)
    rationale = st.text_area(RATIONALE_LABEL, max_chars=500, key=RATIONALE_KEY)
    if st.button(REVIEW_MARK_LABEL, key="triage_review_mark"):
        _start_review(service, evidence.incident.id, TriageAction.MARK_FAIR_USE, rationale.strip())


def _render_remove_control(service: TriageService, evidence: IncidentEvidence) -> None:
    """The review button for removing an existing fair-use entry."""
    safe_render.caption(REMOVAL_NOTE)
    if st.button(REVIEW_REMOVE_LABEL, key="triage_review_remove"):
        _start_review(service, evidence.incident.id, TriageAction.REMOVE_FAIR_USE, "")


def _start_review(
    service: TriageService, incident_id: int, action: TriageAction, rationale: str
) -> None:
    """Build a preview and hold it for confirmation, or report why it cannot be built."""
    preview = service.preview(incident_id, action, rationale)
    if preview.failure is not None:
        # Requirement 12.8: the problem is named and confirmation stays unavailable.
        st.session_state[FAILURE_KEY] = preview.unwrap_failure()
        _forget(PREVIEW_KEY)
    else:
        st.session_state[PREVIEW_KEY] = preview.unwrap()
        _forget(FAILURE_KEY)
        _forget(OUTCOME_KEY)
    st.rerun()


def _render_confirmation(service: TriageService, preview: ActionPreview) -> None:
    """Show exactly what would change, then take the confirmation or the cancellation."""
    view = build_preview_view(preview)
    st.divider()
    st.subheader(view.headline)
    safe_render.detail_rows(view.rows)
    if view.changes_nothing:
        st.info(RATIONALE_ONLY_NOTE)
    st.warning(view.prompt)

    confirm_column, cancel_column = st.columns(2)
    with confirm_column:
        if st.button(f"{CONFIRM_LABEL}: {view.action_label}", key="triage_confirm"):
            _confirm(service, preview)
    with cancel_column:
        if st.button(CANCEL_LABEL, key="triage_cancel"):
            _cancel()


def _confirm(service: TriageService, preview: ActionPreview) -> None:
    """Commit the confirmed action, keeping the review available if it fails."""
    outcome = service.confirm(preview)
    if outcome.failure is not None:
        failure = outcome.unwrap_failure()
        st.session_state[FAILURE_KEY] = failure
        # A stale confirmation has to be reviewed again. Every other failure leaves the
        # review in place so the creator can retry without rebuilding the decision.
        if failure.code is FailureCode.STALE_CONFIRMATION:
            _forget(PREVIEW_KEY)
    else:
        st.session_state[OUTCOME_KEY] = outcome.unwrap()
        _forget(PREVIEW_KEY)
        _forget(FAILURE_KEY)
    st.rerun()


def _cancel() -> None:
    """Discard the pending review. Requirement 11.7: nothing was written."""
    _forget(PREVIEW_KEY)
    _forget(FAILURE_KEY)
    st.rerun()


def _render_outcome() -> None:
    """Show the result of the most recent committed decision."""
    outcome = st.session_state.get(OUTCOME_KEY)
    if not isinstance(outcome, ActionOutcome):
        return
    view = build_outcome_view(outcome)
    st.success(view.headline)
    safe_render.detail_rows(view.rows)


def _render_failure() -> None:
    """Show why the last attempt did not proceed, without losing the evidence."""
    failure = st.session_state.get(FAILURE_KEY)
    if not isinstance(failure, Failure):
        return

    st.warning(message_for(failure))
    for issue in failure.fields:
        safe_render.labelled(label_for(issue.field_key), issue.message)
    if failure.safe_detail is not None:
        safe_render.labelled("Technical detail", failure.safe_detail)
