"""View models for the Incident Triage tab.

Requirements: 11.2-11.6, 12.3, 19.1, 19.3, 21.4
"""

from __future__ import annotations

import pytest

from provenance.application.triage import (
    ActionOutcome,
    ActionPreview,
    IncidentEvidence,
    TriageAction,
    WhitelistEffect,
)
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CreatorId,
    CreatorMetadata,
    Incident,
    IncidentStatus,
    IncidentTransition,
    MediaType,
    NormalizedUrl,
    PageContext,
    RegisteredAsset,
    WhitelistEntry,
)
from provenance.domain.time import UtcTimestamp
from provenance.ui.triage_presenter import (
    EFFECT_LABELS,
    REPRESENTATION_REASONS,
    build_evidence_view,
    build_incident_options,
    build_outcome_view,
    build_preview_view,
)

pytestmark = pytest.mark.unit

HASH = AssetHash("c" * 64)
CREATOR = CreatorId("studio.one")
PAGE = NormalizedUrl("https://shop.example.com/prints/Sunrise")
IMAGE = NormalizedUrl("https://cdn.example.com/a.png")
AT = UtcTimestamp("2026-05-06T07:08:09Z")
LATER = UtcTimestamp("2026-08-20T21:22:23Z")

HOSTILE_TITLE = "<script>alert(1)</script>"


def _incident(
    incident_id: int = 1,
    *,
    status: IncidentStatus = IncidentStatus.DETECTED,
    context: PageContext | None = None,
) -> Incident:
    return Incident(
        id=incident_id,
        asset_hash=HASH,
        page_url=PAGE,
        image_url=IMAGE,
        creator_id_evidence=CREATOR,
        payload_created_at=AT,
        extraction_crc32=0x1234ABCD,
        context=PageContext(title="Aperture Print Shop") if context is None else context,
        first_seen_at=AT,
        last_seen_at=LATER,
        status=status,
    )


def _asset() -> RegisteredAsset:
    return RegisteredAsset(
        asset_hash=HASH,
        creator_id=CREATOR,
        registered_at=AT,
        width=1200,
        height=800,
        source_media_type=MediaType.PNG,
        metadata=CreatorMetadata(creator_id=CREATOR, display_name="Studio One"),
    )


def _entry() -> WhitelistEntry:
    return WhitelistEntry(
        id=7,
        asset_hash=HASH,
        page_url=PAGE,
        rationale="Editorial use with credit.",
        created_at=AT,
        modified_at=LATER,
        related_incident_id=1,
    )


def _evidence(
    *,
    with_asset: bool = True,
    entry: WhitelistEntry | None = None,
    scope: tuple[Incident, ...] = (),
    context: PageContext | None = None,
) -> IncidentEvidence:
    incident = _incident(context=context)
    return IncidentEvidence(
        incident=incident,
        asset=_asset() if with_asset else None,
        whitelist=entry,
        scope=scope or (incident,),
    )


def _preview(
    *,
    action: TriageAction = TriageAction.MARK_FAIR_USE,
    affected: tuple[int, ...] = (1,),
    effect: WhitelistEffect = WhitelistEffect.CREATED,
    rationale: str = "Editorial use with credit.",
) -> ActionPreview:
    return ActionPreview(
        action=action,
        incident_id=1,
        asset_hash=HASH,
        page_url=PAGE,
        current_status=IncidentStatus.DETECTED,
        proposed_status=IncidentStatus.FAIR_USE,
        affected_incident_ids=affected,
        scope_incident_ids=(1,),
        whitelist_effect=effect,
        audit_event_type=AuditEventType.FAIR_USE_MARKED,
        rationale=rationale,
        fingerprint="f" * 64,
    )


def _rendered(rows: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"{label}={value}" for label, value in rows)


def test_options_identify_each_incident_by_id_status_page_and_recency() -> None:
    options = build_incident_options([_incident(3)])

    assert options[0].incident_id == 3
    assert "#3" in options[0].label
    assert "Detected" in options[0].label
    assert PAGE in options[0].label
    assert LATER in options[0].label


def test_evidence_shows_every_required_field() -> None:
    view = build_evidence_view(_evidence())

    everything = " ".join(
        (
            _rendered(view.identity_rows),
            _rendered(view.location_rows),
            _rendered(view.context_rows),
            _rendered(view.timing_rows),
        )
    )

    assert HASH in everything
    assert CREATOR in everything
    assert PAGE in everything
    assert IMAGE in everything
    assert AT in everything
    assert LATER in everything
    assert "Detected" in everything
    assert "0x1234abcd" in everything


def test_both_image_representations_are_labelled_placeholders() -> None:
    view = build_evidence_view(_evidence())

    labels = [label for label, _ in view.placeholders]
    reasons = [reason for _, reason in view.placeholders]

    assert labels == ["Your registered image", "Image found on the page"]
    assert set(reasons) == set(REPRESENTATION_REASONS.values())


def test_missing_registration_is_reported_rather_than_guessed() -> None:
    view = build_evidence_view(_evidence(with_asset=False))

    assert ("Creator ID on record", "(not recorded)") in view.identity_rows
    assert ("Registered", "(not recorded)") in view.timing_rows


def test_absent_page_context_is_reported_as_not_recorded() -> None:
    view = build_evidence_view(_evidence(context=PageContext()))

    assert all(value == "(not recorded)" for _, value in view.context_rows)
    assert view.has_ecommerce_evidence is False


def test_retrieved_values_are_passed_through_without_alteration() -> None:
    context = PageContext(title=HOSTILE_TITLE, ecommerce_evidence=("Price: $250.00",))

    view = build_evidence_view(_evidence(context=context))

    # The presenter never escapes or rewrites evidence. Inertness is the renderer's job,
    # so the exact retrieved bytes must survive to be shown verbatim.
    assert ("Page title", HOSTILE_TITLE) in view.context_rows
    assert view.ecommerce_evidence == ("Price: $250.00",)
    assert view.has_ecommerce_evidence is True


def test_a_shared_scope_is_announced_in_the_headline() -> None:
    incident = _incident()
    view = build_evidence_view(_evidence(scope=(incident, _incident(2))))

    assert "2 incidents share this image and page" in view.headline


def test_a_single_incident_headline_stays_plain() -> None:
    view = build_evidence_view(_evidence())

    assert view.headline == "Incident #1"


def test_an_existing_fair_use_entry_is_shown_with_its_rationale() -> None:
    view = build_evidence_view(_evidence(entry=_entry()))

    assert ("Rationale", "Editorial use with credit.") in view.fair_use_rows
    assert ("Marked", AT) in view.fair_use_rows
    assert ("Last updated", LATER) in view.fair_use_rows


def test_no_fair_use_rows_without_an_entry() -> None:
    assert build_evidence_view(_evidence()).fair_use_rows == ()


def test_evidence_is_labelled_as_evidence_not_a_conclusion() -> None:
    view = build_evidence_view(_evidence())

    assert "does not determine ownership, infringement, or fair use" in view.evidence_note
    assert "exact page address only" in view.scope_note


def test_a_preview_states_the_before_the_after_and_every_effect() -> None:
    view = build_preview_view(_preview())
    rendered = _rendered(view.rows)

    assert "Mark fair use" in view.action_label
    assert "Current status=Detected" in rendered
    assert "Proposed status=Fair use" in rendered
    assert "#1" in rendered
    assert EFFECT_LABELS[WhitelistEffect.CREATED] in rendered
    assert "fair_use_marked" in rendered
    assert view.changes_nothing is False


def test_a_preview_that_moves_no_status_says_so() -> None:
    view = build_preview_view(_preview(affected=(), effect=WhitelistEffect.UPDATED))

    assert view.changes_nothing is True
    assert "No incident status changes" in _rendered(view.rows)


def test_a_preview_prompt_explains_that_nothing_is_written_yet() -> None:
    view = build_preview_view(_preview())

    assert "Nothing is written until you confirm" in view.prompt


def test_a_removal_preview_is_labelled_as_removal() -> None:
    view = build_preview_view(
        _preview(action=TriageAction.REMOVE_FAIR_USE, effect=WhitelistEffect.DELETED)
    )

    assert view.action_label == "Remove fair use"
    assert EFFECT_LABELS[WhitelistEffect.DELETED] in _rendered(view.rows)


def test_an_outcome_reports_the_status_changes_it_made() -> None:
    outcome = ActionOutcome(
        action=TriageAction.MARK_FAIR_USE,
        incident_id=1,
        replayed=False,
        committed_at=LATER,
        transitions=(
            IncidentTransition(
                incident_id=1,
                previous_status=IncidentStatus.DETECTED,
                new_status=IncidentStatus.FAIR_USE,
            ),
        ),
        audit_event_id=12,
    )

    view = build_outcome_view(outcome)
    rendered = _rendered(view.rows)

    assert view.headline == "Mark fair use recorded"
    assert view.replayed is False
    assert "#1 Detected to Fair use" in rendered
    assert "#12" in rendered
    assert LATER in rendered


def test_a_replayed_outcome_says_nothing_changed_again() -> None:
    outcome = ActionOutcome(
        action=TriageAction.MARK_FAIR_USE,
        incident_id=1,
        replayed=True,
        committed_at=LATER,
    )

    view = build_outcome_view(outcome)

    assert view.replayed is True
    assert view.headline == "Already recorded, so nothing changed again"
    assert ("Status changes", "None") in view.rows
    assert ("Audit record", "(not recorded)") in view.rows
