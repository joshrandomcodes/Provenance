"""View models for the Incident Triage tab.

Streamlit-free, so the wording of a decision can be tested directly. Values that came
from a scanned page pass through unchanged and are rendered inertly by the view; nothing
is marked up here.

The wording follows two rules. Findings are described as evidence and never as a legal
conclusion, and a preview always states the current status, the proposed status, and every
record the confirmation would touch, so a creator is never asked to confirm an effect they
were not shown.

Requirements: 11.2-11.6, 12.3, 19.1, 19.3, 21.4, 21.5
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from provenance.application.triage import (
    DETAIL_SOURCE_NOT_STORED,
    DETAIL_TARGET_NOT_STORED,
    ActionOutcome,
    ActionPreview,
    IncidentEvidence,
    TriageAction,
    WhitelistEffect,
)
from provenance.domain.models import Incident, IncidentStatus

STATUS_LABELS: Final = {
    IncidentStatus.DETECTED: "Detected",
    IncidentStatus.STRIKE_AUTHORIZED: "Strike authorized",
    IncidentStatus.CREDIT_REQUESTED: "Credit requested",
    IncidentStatus.FAIR_USE: "Fair use",
}

ACTION_LABELS: Final = {
    TriageAction.MARK_FAIR_USE: "Mark fair use",
    TriageAction.REMOVE_FAIR_USE: "Remove fair use",
}

EFFECT_LABELS: Final = {
    WhitelistEffect.CREATED: "A new fair-use entry is created for this exact page",
    WhitelistEffect.UPDATED: "The existing fair-use entry for this exact page is updated",
    WhitelistEffect.DELETED: "The fair-use entry for this exact page is deleted",
}

REPRESENTATION_REASONS: Final = {
    DETAIL_SOURCE_NOT_STORED: (
        "Your registered image is not shown because the registry stores its identity, "
        "never its pixels."
    ),
    DETAIL_TARGET_NOT_STORED: (
        "The image found on the page is not shown because scraped image bytes are never "
        "written to disk."
    ),
}

EVIDENCE_NOTE: Final = (
    "Everything below is evidence for your review. On its own or together it does not "
    "determine ownership, infringement, or fair use."
)

SCOPE_NOTE: Final = (
    "Fair use applies to this exact image and this exact page address only. Another page, "
    "or the same page with a different path or query, is unaffected."
)

PENDING_ACTIONS_NOTE: Final = (
    "Strike authorization and credit requests are not implemented in this build. Fair use "
    "is the only decision Provenance can record today."
)

EMPTY_ACTIVE_NOTE: Final = (
    "No active incidents. Run a scan from Web Radar to check a page for your registered images."
)

EMPTY_FAIR_USE_NOTE: Final = "No incidents are marked fair use."

RATIONALE_PROMPT: Final = (
    "Record why this use is acceptable to you. This note is stored locally with the fair-use entry."
)

NOT_RECORDED: Final = "(not recorded)"


def _status(status: IncidentStatus) -> str:
    return STATUS_LABELS.get(status, status.value)


@dataclass(frozen=True, slots=True)
class IncidentOption:
    """One selectable incident in a list view."""

    incident_id: int
    label: str


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """One incident's complete recorded evidence, prepared for inert display."""

    incident_id: int
    headline: str
    status: str
    identity_rows: tuple[tuple[str, str], ...]
    location_rows: tuple[tuple[str, str], ...]
    context_rows: tuple[tuple[str, str], ...]
    timing_rows: tuple[tuple[str, str], ...]
    ecommerce_evidence: tuple[str, ...]
    placeholders: tuple[tuple[str, str], ...]
    fair_use_rows: tuple[tuple[str, str], ...]
    scope_note: str = SCOPE_NOTE
    evidence_note: str = EVIDENCE_NOTE

    @property
    def has_ecommerce_evidence(self) -> bool:
        """True when commerce wording was captured next to the image."""
        return bool(self.ecommerce_evidence)


@dataclass(frozen=True, slots=True)
class PreviewView:
    """Exactly what a confirmation would change."""

    headline: str
    action_label: str
    rows: tuple[tuple[str, str], ...]
    prompt: str
    changes_nothing: bool


@dataclass(frozen=True, slots=True)
class OutcomeView:
    """What a committed decision did."""

    headline: str
    rows: tuple[tuple[str, str], ...]
    replayed: bool


def build_incident_options(incidents: Sequence[Incident]) -> tuple[IncidentOption, ...]:
    """Label each incident so it can be told apart without reading its evidence."""
    return tuple(
        IncidentOption(
            incident_id=incident.id,
            label=(
                f"#{incident.id} - {_status(incident.status)} - "
                f"{incident.page_url} - last seen {incident.last_seen_at}"
            ),
        )
        for incident in incidents
    )


def build_evidence_view(evidence: IncidentEvidence) -> EvidenceView:
    """Prepare one incident's evidence for display."""
    incident = evidence.incident
    context = incident.context
    asset = evidence.asset

    identity_rows = (
        ("Asset hash", incident.asset_hash),
        ("Creator ID in watermark", incident.creator_id_evidence),
        ("Creator ID on record", NOT_RECORDED if asset is None else asset.creator_id),
        ("Watermark CRC-32", f"{incident.extraction_crc32:#010x}"),
    )

    location_rows = (
        ("Page address", incident.page_url),
        ("Image address", incident.image_url),
    )

    context_rows = (
        ("Page title", context.title or NOT_RECORDED),
        ("Nearest heading", context.heading or NOT_RECORDED),
        ("Caption", context.figcaption or NOT_RECORDED),
        ("Alt text", context.alt or NOT_RECORDED),
    )

    timing_rows = (
        ("Registered", NOT_RECORDED if asset is None else asset.registered_at),
        ("Watermark created", incident.payload_created_at),
        ("First seen", incident.first_seen_at),
        ("Last seen", incident.last_seen_at),
        ("Status", _status(incident.status)),
    )

    placeholders = (
        ("Your registered image", REPRESENTATION_REASONS[evidence.source_unavailable_detail]),
        ("Image found on the page", REPRESENTATION_REASONS[evidence.target_unavailable_detail]),
    )

    entry = evidence.whitelist
    fair_use_rows: tuple[tuple[str, str], ...] = ()
    if entry is not None:
        fair_use_rows = (
            ("Rationale", entry.rationale),
            ("Marked", entry.created_at),
            ("Last updated", entry.modified_at),
        )

    scope_count = len(evidence.scope)
    headline = f"Incident #{incident.id}"
    if scope_count > 1:
        headline = f"{headline} ({scope_count} incidents share this image and page)"

    return EvidenceView(
        incident_id=incident.id,
        headline=headline,
        status=_status(incident.status),
        identity_rows=identity_rows,
        location_rows=location_rows,
        context_rows=context_rows,
        timing_rows=timing_rows,
        ecommerce_evidence=context.ecommerce_evidence,
        placeholders=placeholders,
        fair_use_rows=fair_use_rows,
    )


def build_preview_view(preview: ActionPreview) -> PreviewView:
    """State the before, the after, and every record a confirmation would touch."""
    affected = preview.affected_incident_ids
    if affected:
        affected_text = ", ".join(f"#{value}" for value in affected)
    else:
        affected_text = "No incident status changes"

    rows = (
        ("Action", ACTION_LABELS[preview.action]),
        ("This incident", f"#{preview.incident_id}"),
        ("Current status", _status(preview.current_status)),
        ("Proposed status", _status(preview.proposed_status)),
        ("Incidents whose status changes", affected_text),
        ("Fair-use entry", EFFECT_LABELS[preview.whitelist_effect]),
        ("Audit record", f"One {preview.audit_event_type.value} event is written"),
        ("Rationale stored", preview.rationale or NOT_RECORDED),
    )

    prompt = (
        "Confirm to apply exactly what is listed above. Nothing is written until you "
        "confirm, and any change to this incident or your rationale requires a fresh review."
    )

    return PreviewView(
        headline=f"Review: {ACTION_LABELS[preview.action]}",
        action_label=ACTION_LABELS[preview.action],
        rows=rows,
        prompt=prompt,
        changes_nothing=preview.changes_nothing,
    )


def build_outcome_view(outcome: ActionOutcome) -> OutcomeView:
    """Report what a committed decision changed."""
    moved = ", ".join(
        f"#{transition.incident_id} {_status(transition.previous_status)}"
        f" to {_status(transition.new_status)}"
        for transition in outcome.transitions
    )
    rows = (
        ("Action", ACTION_LABELS[outcome.action]),
        ("Incident", f"#{outcome.incident_id}"),
        ("Recorded at", outcome.committed_at),
        ("Status changes", moved or "None"),
        (
            "Audit record",
            NOT_RECORDED if outcome.audit_event_id is None else f"#{outcome.audit_event_id}",
        ),
    )

    headline = (
        "Already recorded, so nothing changed again"
        if outcome.replayed
        else f"{ACTION_LABELS[outcome.action]} recorded"
    )
    return OutcomeView(headline=headline, rows=rows, replayed=outcome.replayed)
