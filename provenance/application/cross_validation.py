"""Registry cross-validation of one extraction outcome.

A watermark frame is a claim, not a match. This module is the only place that decides a
claim is real, and it requires two independent facts to agree: the Asset_Hash carried in
the payload must name a registered asset, and the Creator_ID in the payload must equal the
Creator_ID that asset was registered under. Either one alone is insufficient.

Three consequences follow, and each is tested:

* An extraction with no payload never reaches the Registry at all. No watermark, corrupt
  watermark, and analysis failure return unchanged, so random pixels cannot produce an
  incident even in principle.
* A valid payload naming an unregistered asset, or naming a different creator than the one
  on record, stays ``UNREGISTERED``. It is real evidence of *something*, but it is not this
  creator's registered work.
* Only a full agreement commits, and it commits the incident and exactly one audit event in
  one transaction.

Exact-scope whitelisting is applied by the repository, so a page the creator has already
marked fair use produces a Fair Use incident rather than an active one.

Requirements: 9.5, 10.1-10.7, 18.2, 18.8, 18.9, 20.20
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from provenance.application.operations import operation_key
from provenance.domain.errors import Result, ok
from provenance.domain.models import (
    AuditEventType,
    ExtractionEvidence,
    ExtractionKind,
    Incident,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
    PageContext,
    VerifiedDetection,
    WatermarkPayload,
)
from provenance.domain.time import Clock, now_timestamp
from provenance.ports.registry import RegistryPort, UnitOfWork

CROSS_VALIDATE_OPERATION: Final = "cross_validate_detection"
DETECTION_OPERATION_TYPE: Final = "incident_detected"

DETAIL_NOT_REGISTERED: Final = "asset_hash_not_registered"
DETAIL_CREATOR_MISMATCH: Final = "creator_id_not_registered_owner"


@dataclass(frozen=True, slots=True)
class DetectionOutcome:
    """The terminal classification of one analyzed image after the Registry check."""

    kind: ExtractionKind
    incident: Incident | None = None
    payload: WatermarkPayload | None = None
    detail: str | None = None

    @property
    def is_verified(self) -> bool:
        """True only when the Registry confirmed both identity facts."""
        return self.kind is ExtractionKind.VERIFIED and self.incident is not None


def detection_operation_key(detection: VerifiedDetection) -> OperationKey:
    """Stable audit key for one detection of one image on one page at one second.

    Including the discovery second means a later rediscovery records its own audit event,
    while a repeated detection within the same second is recognized and not duplicated.
    """
    return operation_key(
        DETECTION_OPERATION_TYPE,
        {
            "asset_hash": detection.asset_hash,
            "page_url": detection.page_url,
            "image_url": detection.image_url,
        },
        {"discovered_at": detection.discovered_at},
    )


class DetectionCrossValidator:
    """Decides whether one extraction outcome is a registered match, and records it."""

    __slots__ = ("_registry", "_clock")

    def __init__(self, registry: RegistryPort, clock: Clock) -> None:
        self._registry = registry
        self._clock = clock

    def cross_validate(
        self,
        evidence: ExtractionEvidence,
        *,
        page_url: NormalizedUrl,
        image_url: NormalizedUrl,
        context: PageContext,
    ) -> Result[DetectionOutcome]:
        """Cross-check one extraction against the Registry, committing a match only."""
        payload = evidence.payload
        if payload is None or evidence.crc32 is None:
            # Nothing to look up, so the Registry is never touched and no incident can
            # be created. This is the guarantee that property 25 checks.
            return ok(DetectionOutcome(kind=evidence.kind, detail=evidence.detail))

        begun = self._registry.begin(CROSS_VALIDATE_OPERATION)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        with begun.unwrap() as uow:
            asset = uow.assets.get(payload.asset_hash)
            if asset is None:
                # Leaving without committing rolls back, so no write occurred.
                return ok(
                    DetectionOutcome(
                        kind=ExtractionKind.UNREGISTERED,
                        payload=payload,
                        detail=DETAIL_NOT_REGISTERED,
                    )
                )
            if asset.creator_id != payload.creator_id:
                return ok(
                    DetectionOutcome(
                        kind=ExtractionKind.UNREGISTERED,
                        payload=payload,
                        detail=DETAIL_CREATOR_MISMATCH,
                    )
                )

            detection = VerifiedDetection(
                asset_hash=payload.asset_hash,
                creator_id=payload.creator_id,
                page_url=page_url,
                image_url=image_url,
                payload_created_at=payload.created_at,
                extraction_crc32=evidence.crc32,
                context=context,
                discovered_at=now_timestamp(self._clock),
            )

            recorded = uow.incidents.upsert_detection(detection)
            if recorded.failure is not None:
                return Result(failure=recorded.failure)
            incident = recorded.unwrap()

            audited = self._record_audit(uow, incident, detection)
            if audited.failure is not None:
                return Result(failure=audited.failure)

            uow.commit()
            return ok(
                DetectionOutcome(kind=ExtractionKind.VERIFIED, incident=incident, payload=payload)
            )

    def _record_audit(
        self, uow: UnitOfWork, incident: Incident, detection: VerifiedDetection
    ) -> Result[bool]:
        """Append exactly one audit event for this detection, or none if already present."""
        key = detection_operation_key(detection)
        if uow.audits.by_operation_key(key) is not None:
            return ok(False)

        appended = uow.audits.append(
            NewAuditEvent(
                event_type=AuditEventType.INCIDENT_DETECTED,
                occurred_at=detection.discovered_at,
                operation_key=key,
                asset_hash_tombstone=detection.asset_hash,
                incident_id=incident.id,
                new_statuses={incident.id: incident.status},
            )
        )
        if appended.failure is not None:
            return Result(failure=appended.failure)
        return ok(True)
