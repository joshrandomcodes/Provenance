"""Incident triage: evidence review and confirmed fair-use decisions.

Triage is where evidence becomes a decision, so every mutation here is a confirmed
material action. It is previewed first, bound to a fingerprint of exactly what was shown,
committed with one audit event, and idempotent if the creator clicks twice.

Two rules carry the weight.

* Nothing changes without a matching preview. ``preview`` reports the current status, the
  proposed status, every incident in scope, and the whitelist effect, then fingerprints
  them. ``confirm`` recomputes that fingerprint from live state *inside the writing
  transaction* and refuses a stale confirmation, so a decision can never be applied to a
  world that moved after it was shown.
* Fair-use scope is exact. One entry covers one Asset_Hash and one byte-exact Page_URL,
  and the repository applies it by equality alone, so a neighbouring page, a differently
  cased path, or a different query string is never suppressed.

Reads never commit. ``load`` and ``evidence`` open a transaction only to read consistent
lists and let it roll back on exit.

Strike authorization and credit requests are deliberately absent. Those belong to
requirements 13 and 15, which are not implemented, and a button that flipped a status
without the workflow behind it would misrepresent what this tool does.

Requirements: 11.1-11.9, 12.1-12.8, 18.6-18.8, 21.4, 21.6
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from provenance.application.operations import (
    MaterialActionRunner,
    OperationEffect,
    OperationRequest,
)
from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    Incident,
    IncidentStatus,
    IncidentTransition,
    MarkFairUse,
    NormalizedUrl,
    OperationKey,
    RegisteredAsset,
    RemoveFairUse,
    TransitionSet,
    WhitelistEntry,
)
from provenance.domain.time import Clock, UtcTimestamp, now_timestamp
from provenance.domain.validation import validate_fair_use_rationale
from provenance.ports.registry import RegistryPort, UnitOfWork

LOAD_OPERATION: Final = "load_triage"
EVIDENCE_OPERATION: Final = "load_incident_evidence"
PREVIEW_OPERATION: Final = "preview_incident_action"
MARK_FAIR_USE_OPERATION: Final = "mark_fair_use"
REMOVE_FAIR_USE_OPERATION: Final = "remove_fair_use"

DETAIL_INCIDENT_MISSING: Final = "incident_not_found"
DETAIL_WHITELIST_MISSING: Final = "whitelist_entry_not_found"
DETAIL_PREVIEW_CHANGED: Final = "preview_changed_before_confirmation"

# Neither representation can be shown, and each has its own reason. The registry stores
# identity rather than pixels, and scraped bytes are never written to disk at all.
DETAIL_SOURCE_NOT_STORED: Final = "registry_stores_no_image_bytes"
DETAIL_TARGET_NOT_STORED: Final = "scraped_bytes_never_persisted"


class TriageAction(StrEnum):
    """The decisions this service can commit."""

    MARK_FAIR_USE = "mark_fair_use"
    REMOVE_FAIR_USE = "remove_fair_use"


class WhitelistEffect(StrEnum):
    """What a confirmed action would do to the exact-scope whitelist."""

    CREATED = "whitelist_created"
    UPDATED = "whitelist_updated"
    DELETED = "whitelist_deleted"


@dataclass(frozen=True, slots=True)
class TriageSnapshot:
    """The two incident views, read in one transaction so they agree with each other."""

    active: tuple[Incident, ...]
    fair_use: tuple[Incident, ...]

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to triage."""
        return not self.active and not self.fair_use


@dataclass(frozen=True, slots=True)
class IncidentEvidence:
    """Everything recorded about one incident, for review before any decision."""

    incident: Incident
    asset: RegisteredAsset | None
    whitelist: WhitelistEntry | None
    scope: tuple[Incident, ...]
    source_unavailable_detail: str = DETAIL_SOURCE_NOT_STORED
    target_unavailable_detail: str = DETAIL_TARGET_NOT_STORED

    @property
    def is_fair_use(self) -> bool:
        """True when an exact-scope whitelist entry currently suppresses this incident."""
        return self.whitelist is not None

    @property
    def available_actions(self) -> tuple[TriageAction, ...]:
        """The actions this incident can accept right now."""
        if self.is_fair_use:
            return (TriageAction.REMOVE_FAIR_USE,)
        return (TriageAction.MARK_FAIR_USE,)


@dataclass(frozen=True, slots=True)
class ActionPreview:
    """Exactly what a confirmed action would change, and a fingerprint binding it."""

    action: TriageAction
    incident_id: int
    asset_hash: AssetHash
    page_url: NormalizedUrl
    current_status: IncidentStatus
    proposed_status: IncidentStatus
    affected_incident_ids: tuple[int, ...]
    scope_incident_ids: tuple[int, ...]
    whitelist_effect: WhitelistEffect
    audit_event_type: AuditEventType
    rationale: str
    fingerprint: str

    @property
    def changes_nothing(self) -> bool:
        """True when no incident status would move, as on a rationale-only update."""
        return not self.affected_incident_ids


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """The committed result of one confirmed action."""

    action: TriageAction
    incident_id: int
    replayed: bool
    committed_at: UtcTimestamp
    transitions: tuple[IncidentTransition, ...] = ()
    audit_event_id: int | None = None


def _operation_for(action: TriageAction) -> str:
    """The operation name recorded on the audit event and the idempotency receipt."""
    if action is TriageAction.MARK_FAIR_USE:
        return MARK_FAIR_USE_OPERATION
    return REMOVE_FAIR_USE_OPERATION


def _fingerprint(material: Mapping[str, str]) -> str:
    """Hash the displayed decision so any later change makes the confirmation stale."""
    canonical = json.dumps(
        dict(material), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TriageService:
    """Reads incident evidence and commits confirmed fair-use decisions."""

    __slots__ = ("_registry", "_runner", "_clock")

    def __init__(self, registry: RegistryPort, runner: MaterialActionRunner, clock: Clock) -> None:
        self._registry = registry
        self._runner = runner
        self._clock = clock

    # Reads -------------------------------------------------------------------------

    def load(self) -> Result[TriageSnapshot]:
        """Read the active and fair-use views in one consistent transaction."""
        begun = self._registry.begin(LOAD_OPERATION)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        with begun.unwrap() as uow:
            # No commit: leaving the block rolls back a read-only transaction.
            return ok(
                TriageSnapshot(
                    active=tuple(uow.incidents.list_active()),
                    fair_use=tuple(uow.incidents.list_fair_use()),
                )
            )

    def evidence(self, incident_id: int) -> Result[IncidentEvidence]:
        """Read one incident with its registration, whitelist entry, and exact scope."""
        begun = self._registry.begin(EVIDENCE_OPERATION)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        with begun.unwrap() as uow:
            return self._evidence_within(uow, incident_id, EVIDENCE_OPERATION)

    def preview(
        self, incident_id: int, action: TriageAction, rationale: str = ""
    ) -> Result[ActionPreview]:
        """Compute what one action would change, without changing anything."""
        begun = self._registry.begin(PREVIEW_OPERATION)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        with begun.unwrap() as uow:
            evidence = self._evidence_within(uow, incident_id, PREVIEW_OPERATION)
            if evidence.failure is not None:
                return Result(failure=evidence.failure)
            return self._preview_from(evidence.unwrap(), action, rationale)

    # Writes ------------------------------------------------------------------------

    def confirm(self, preview: ActionPreview) -> Result[ActionOutcome]:
        """Commit one confirmed action, or refuse it because the preview went stale.

        The freshness check runs inside the writing transaction, so there is no window
        between checking and committing.
        """
        operation = _operation_for(preview.action)
        at = now_timestamp(self._clock)
        request = OperationRequest(
            operation_type=operation,
            target_ids={
                "incident_id": str(preview.incident_id),
                "asset_hash": preview.asset_hash,
                "page_url": preview.page_url,
            },
            requested_values={
                "proposed_status": preview.proposed_status.value,
                "rationale": preview.rationale,
                "fingerprint": preview.fingerprint,
            },
        )

        def action(uow: UnitOfWork) -> Result[OperationEffect[TransitionSet]]:
            checked = self._verify_still_current(uow, preview, operation)
            if checked.failure is not None:
                return Result(failure=checked.failure)
            return self._apply(uow, preview, at=at, operation_key=request.key)

        ran = self._runner.run(request, action)
        if ran.failure is not None:
            return Result(failure=ran.failure)

        result = ran.unwrap()
        applied = result.value
        return ok(
            ActionOutcome(
                action=preview.action,
                incident_id=preview.incident_id,
                replayed=result.replayed,
                committed_at=result.receipt.committed_at,
                transitions=() if applied is None else applied.transitions,
                audit_event_id=result.receipt.audit_event_id,
            )
        )

    # Internals ---------------------------------------------------------------------

    def _evidence_within(
        self, uow: UnitOfWork, incident_id: int, operation: str
    ) -> Result[IncidentEvidence]:
        incident = uow.incidents.get(incident_id)
        if incident is None:
            return failed(FailureCode.NOT_FOUND, operation, safe_detail=DETAIL_INCIDENT_MISSING)
        return ok(
            IncidentEvidence(
                incident=incident,
                asset=uow.assets.get(incident.asset_hash),
                whitelist=uow.whitelist.exact(incident.asset_hash, incident.page_url),
                scope=self._scope_members(uow, incident),
            )
        )

    @staticmethod
    def _scope_members(uow: UnitOfWork, incident: Incident) -> tuple[Incident, ...]:
        """Every incident sharing this incident's exact Asset_Hash and Page_URL.

        The two views partition the whole incident table between them, so their union
        needs no additional query: an incident is either suppressed by a whitelist entry
        or marked Fair Use, which puts it in the fair-use view, or it is active.
        """
        found: dict[int, Incident] = {incident.id: incident}
        for candidate in (*uow.incidents.list_active(), *uow.incidents.list_fair_use()):
            if (
                candidate.asset_hash == incident.asset_hash
                and candidate.page_url == incident.page_url
            ):
                found[candidate.id] = candidate
        return tuple(found[key] for key in sorted(found))

    def _preview_from(
        self, evidence: IncidentEvidence, action: TriageAction, rationale: str
    ) -> Result[ActionPreview]:
        if action is TriageAction.MARK_FAIR_USE:
            return self._preview_mark(evidence, rationale)
        return self._preview_remove(evidence)

    def _preview_mark(self, evidence: IncidentEvidence, rationale: str) -> Result[ActionPreview]:
        report = validate_fair_use_rationale(rationale)
        if not report.is_valid:
            # Requirement 12.8: name the problem, keep confirmation unavailable, and
            # leave the Registry untouched. No transaction has written anything here.
            return failed(report.issues[0].code, MARK_FAIR_USE_OPERATION, fields=report.issues)

        affected = tuple(
            member.id for member in evidence.scope if member.status is not IncidentStatus.FAIR_USE
        )
        effect = WhitelistEffect.UPDATED if evidence.is_fair_use else WhitelistEffect.CREATED
        return ok(
            self._assemble(
                evidence,
                action=TriageAction.MARK_FAIR_USE,
                proposed_status=IncidentStatus.FAIR_USE,
                affected=affected,
                effect=effect,
                audit_event_type=AuditEventType.FAIR_USE_MARKED,
                rationale=rationale,
            )
        )

    def _preview_remove(self, evidence: IncidentEvidence) -> Result[ActionPreview]:
        if evidence.whitelist is None:
            return failed(
                FailureCode.NOT_FOUND,
                REMOVE_FAIR_USE_OPERATION,
                safe_detail=DETAIL_WHITELIST_MISSING,
            )

        # Removal reopens only unresolved Fair Use incidents, so nothing else moves.
        affected = tuple(
            member.id for member in evidence.scope if member.status is IncidentStatus.FAIR_USE
        )
        return ok(
            self._assemble(
                evidence,
                action=TriageAction.REMOVE_FAIR_USE,
                proposed_status=IncidentStatus.DETECTED,
                affected=affected,
                effect=WhitelistEffect.DELETED,
                audit_event_type=AuditEventType.FAIR_USE_REMOVED,
                rationale=evidence.whitelist.rationale,
            )
        )

    @staticmethod
    def _assemble(
        evidence: IncidentEvidence,
        *,
        action: TriageAction,
        proposed_status: IncidentStatus,
        affected: tuple[int, ...],
        effect: WhitelistEffect,
        audit_event_type: AuditEventType,
        rationale: str,
    ) -> ActionPreview:
        incident = evidence.incident
        scope_ids = tuple(member.id for member in evidence.scope)
        return ActionPreview(
            action=action,
            incident_id=incident.id,
            asset_hash=incident.asset_hash,
            page_url=incident.page_url,
            current_status=incident.status,
            proposed_status=proposed_status,
            affected_incident_ids=affected,
            scope_incident_ids=scope_ids,
            whitelist_effect=effect,
            audit_event_type=audit_event_type,
            rationale=rationale,
            fingerprint=_fingerprint(
                {
                    "action": action.value,
                    "incident_id": str(incident.id),
                    "asset_hash": incident.asset_hash,
                    "page_url": incident.page_url,
                    "current_status": incident.status.value,
                    "proposed_status": proposed_status.value,
                    "affected": ",".join(str(value) for value in affected),
                    "scope": ",".join(str(value) for value in scope_ids),
                    "whitelist_effect": effect.value,
                    "rationale": rationale,
                }
            ),
        )

    def _verify_still_current(
        self, uow: UnitOfWork, preview: ActionPreview, operation: str
    ) -> Result[ActionPreview]:
        """Rebuild the preview from live state and require an identical fingerprint."""
        evidence = self._evidence_within(uow, preview.incident_id, operation)
        if evidence.failure is not None:
            return Result(failure=evidence.failure)

        fresh = self._preview_from(evidence.unwrap(), preview.action, preview.rationale)
        if fresh.failure is not None:
            return Result(failure=fresh.failure)

        current = fresh.unwrap()
        if current.fingerprint != preview.fingerprint:
            return failed(
                FailureCode.STALE_CONFIRMATION, operation, safe_detail=DETAIL_PREVIEW_CHANGED
            )
        return ok(current)

    @staticmethod
    def _apply(
        uow: UnitOfWork, preview: ActionPreview, *, at: UtcTimestamp, operation_key: OperationKey
    ) -> Result[OperationEffect[TransitionSet]]:
        """Run the repository command for this action inside the open transaction."""
        if preview.action is TriageAction.MARK_FAIR_USE:
            applied = uow.whitelist.upsert_and_mark_fair_use(
                MarkFairUse(
                    asset_hash=preview.asset_hash,
                    page_url=preview.page_url,
                    rationale=preview.rationale,
                    at=at,
                    operation_key=operation_key,
                    related_incident_id=preview.incident_id,
                )
            )
        else:
            applied = uow.whitelist.remove_and_reopen(
                RemoveFairUse(
                    asset_hash=preview.asset_hash,
                    page_url=preview.page_url,
                    at=at,
                    operation_key=operation_key,
                )
            )

        if applied.failure is not None:
            return Result(failure=applied.failure)

        transitions = applied.unwrap()
        return ok(
            OperationEffect(
                value=transitions,
                outcome={
                    "status": preview.proposed_status.value,
                    "whitelist_effect": preview.whitelist_effect.value,
                    "transitions": str(len(transitions.transitions)),
                },
                audit_event_id=transitions.audit.id,
            )
        )
