"""SQLite repositories.

Repositories never begin, commit, or roll back. The unit of work owns the
transaction, so a confirmed action and its single audit event commit together or not
at all.

Requirements: 5.2-5.5, 6.1-6.8, 10.5-10.7, 12.1-12.7, 17.7-17.11, 18.6-18.8
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    ACTIVE_INCIDENT_STATUSES,
    AssetHash,
    AuditEvent,
    AuditEventType,
    CommittedOperation,
    DeleteOutcome,
    DeletionCounts,
    DeletionPreview,
    Incident,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionPlan,
    MarkFairUse,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
    RegisterAsset,
    RegisteredAsset,
    RegistrationOutcome,
    RemoveFairUse,
    TransitionSet,
    VerifiedDetection,
    WhitelistEntry,
)
from provenance.domain.time import UtcTimestamp
from provenance.infrastructure.sqlite.rows import (
    asset_from_row,
    audit_from_row,
    encode_context,
    encode_statuses,
    encode_target_ids,
    incident_from_row,
    receipt_from_row,
    whitelist_from_row,
)

_ASSET_COLUMNS: Final = (
    "asset_hash, creator_id, registered_at, width, height, source_media_type, "
    "display_name, contact_email, postal_address, rights_statement"
)
_INCIDENT_COLUMNS: Final = (
    "id, asset_hash, page_url, image_url, creator_id_evidence, payload_created_at, "
    "extraction_crc32, context_json, first_seen_at, last_seen_at, status"
)
_WHITELIST_COLUMNS: Final = (
    "id, asset_hash, page_url, rationale, created_at, modified_at, related_incident_id"
)
_AUDIT_COLUMNS: Final = (
    "id, event_type, occurred_at, operation_key, asset_hash_tombstone, incident_id, "
    "whitelist_id, previous_statuses_json, new_statuses_json, content_hash, recipient"
)
_RECEIPT_COLUMNS: Final = (
    "operation_key, operation_type, target_ids_json, requested_values_hash, outcome_json, "
    "committed_at, audit_event_id"
)

_ACTIVE_STATUS_VALUES: Final = tuple(status.value for status in ACTIVE_INCIDENT_STATUSES)

DETAIL_ASSET_MISSING: Final = "asset_not_found"
DETAIL_CREATOR_MISMATCH: Final = "creator_id_mismatch"
DETAIL_INCIDENT_MISSING: Final = "incident_not_found"
DETAIL_WHITELIST_MISSING: Final = "whitelist_entry_not_found"
DETAIL_STATUS_CHANGED: Final = "incident_status_changed"
DETAIL_DUPLICATE_OPERATION: Final = "operation_key_already_used"


def deletion_fingerprint(asset_hash: AssetHash, counts: DeletionCounts) -> str:
    """Bind a deletion confirmation to the exact counts shown to the user."""
    material = f"{asset_hash}|{counts.incidents}|{counts.whitelist_entries}|{counts.audit_events}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class SqliteAuditRepository:
    """Append-only audit trail inside an open transaction."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, event: NewAuditEvent) -> Result[AuditEvent]:
        """Append exactly one audit event."""
        try:
            cursor = self._connection.execute(
                "INSERT INTO audit_events (event_type, occurred_at, operation_key, "
                "asset_hash_tombstone, incident_id, whitelist_id, previous_statuses_json, "
                "new_statuses_json, content_hash, recipient) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_type.value,
                    event.occurred_at,
                    event.operation_key,
                    event.asset_hash_tombstone,
                    event.incident_id,
                    event.whitelist_id,
                    encode_statuses(event.previous_statuses),
                    encode_statuses(event.new_statuses),
                    event.content_hash,
                    event.recipient,
                ),
            )
        except sqlite3.IntegrityError:
            return failed(
                FailureCode.CONSTRAINT, "append_audit", safe_detail=DETAIL_DUPLICATE_OPERATION
            )

        row = self._connection.execute(
            f"SELECT {_AUDIT_COLUMNS} FROM audit_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        if row is None:  # pragma: no cover - insert succeeded, row must exist
            return failed(FailureCode.COMMIT_FAILED, "append_audit")
        return ok(audit_from_row(row))

    def by_operation_key(self, key: OperationKey) -> AuditEvent | None:
        """Return the audit event recorded for one operation key."""
        row = self._connection.execute(
            f"SELECT {_AUDIT_COLUMNS} FROM audit_events WHERE operation_key = ?", (key,)
        ).fetchone()
        return None if row is None else audit_from_row(row)


class SqliteAssetRepository:
    """Registered asset reads and writes inside an open transaction."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, asset_hash: AssetHash) -> RegisteredAsset | None:
        """Return the asset, or None when it is absent."""
        row = self._connection.execute(
            f"SELECT {_ASSET_COLUMNS} FROM registered_assets WHERE asset_hash = ?",
            (asset_hash,),
        ).fetchone()
        return None if row is None else asset_from_row(row)

    def register_or_reuse(self, command: RegisterAsset) -> Result[RegistrationOutcome]:
        """Create one asset, or return the existing one for the same creator."""
        existing = self.get(command.asset_hash)
        if existing is not None:
            if existing.creator_id != command.creator_id:
                return failed(
                    FailureCode.IDENTITY_CONFLICT,
                    "register_asset",
                    safe_detail=DETAIL_CREATOR_MISMATCH,
                )
            return ok(RegistrationOutcome(asset=existing, created=False))

        metadata = command.metadata
        try:
            self._connection.execute(
                "INSERT INTO registered_assets (asset_hash, creator_id, registered_at, width, "
                "height, source_media_type, display_name, contact_email, postal_address, "
                "rights_statement) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    command.asset_hash,
                    command.creator_id,
                    command.registered_at,
                    command.width,
                    command.height,
                    command.source_media_type.value,
                    metadata.display_name,
                    metadata.contact_email,
                    metadata.postal_address,
                    metadata.rights_statement,
                ),
            )
        except sqlite3.IntegrityError:
            return failed(FailureCode.CONSTRAINT, "register_asset")

        created = self.get(command.asset_hash)
        if created is None:  # pragma: no cover - insert succeeded, row must exist
            return failed(FailureCode.COMMIT_FAILED, "register_asset")
        return ok(RegistrationOutcome(asset=created, created=True))

    def deletion_counts(self, asset_hash: AssetHash) -> DeletionCounts:
        """Count dependants and related audit events for a deletion preview."""
        return DeletionCounts(
            incidents=self._scalar(
                "SELECT count(*) FROM incidents WHERE asset_hash = ?", (asset_hash,)
            ),
            whitelist_entries=self._scalar(
                "SELECT count(*) FROM whitelist_entries WHERE asset_hash = ?", (asset_hash,)
            ),
            audit_events=self._scalar(
                "SELECT count(*) FROM audit_events WHERE asset_hash_tombstone = ?", (asset_hash,)
            ),
        )

    def deletion_preview(self, asset_hash: AssetHash) -> Result[DeletionPreview]:
        """Build a compare-and-swap preview for one asset."""
        if self.get(asset_hash) is None:
            return failed(
                FailureCode.NOT_FOUND, "preview_delete_asset", safe_detail=DETAIL_ASSET_MISSING
            )
        counts = self.deletion_counts(asset_hash)
        return ok(
            DeletionPreview(
                asset_hash=asset_hash,
                counts=counts,
                fingerprint=deletion_fingerprint(asset_hash, counts),
            )
        )

    def delete_if_preview_matches(self, preview: DeletionPreview) -> Result[DeleteOutcome]:
        """Delete only when the asset and its dependant counts still match the preview."""
        current = self.deletion_preview(preview.asset_hash)
        if current.failure is not None:
            return Result(failure=current.failure)

        refreshed = current.unwrap()
        if refreshed.fingerprint != preview.fingerprint:
            return ok(
                DeleteOutcome(deleted=False, counts=refreshed.counts, refreshed_preview=refreshed)
            )

        try:
            self._connection.execute(
                "DELETE FROM registered_assets WHERE asset_hash = ?", (preview.asset_hash,)
            )
        except sqlite3.IntegrityError:
            return failed(FailureCode.CONSTRAINT, "delete_asset")
        return ok(DeleteOutcome(deleted=True, counts=refreshed.counts))

    def _scalar(self, sql: str, parameters: tuple[object, ...]) -> int:
        row = self._connection.execute(sql, parameters).fetchone()
        return 0 if row is None else int(row[0])


class SqliteIncidentRepository:
    """Incident reads and writes inside an open transaction."""

    __slots__ = ("_connection", "_audits")

    def __init__(self, connection: sqlite3.Connection, audits: SqliteAuditRepository) -> None:
        self._connection = connection
        self._audits = audits

    def get(self, incident_id: int) -> Incident | None:
        """Return one incident by identifier."""
        row = self._connection.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        return None if row is None else incident_from_row(row)

    def by_key(
        self, asset_hash: AssetHash, page_url: NormalizedUrl, image_url: NormalizedUrl
    ) -> Incident | None:
        """Return the incident for one exact deduplication key."""
        row = self._connection.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents "
            "WHERE asset_hash = ? AND page_url = ? AND image_url = ?",
            (asset_hash, page_url, image_url),
        ).fetchone()
        return None if row is None else incident_from_row(row)

    def by_scope(self, asset_hash: AssetHash, page_url: NormalizedUrl) -> Sequence[Incident]:
        """Every incident in one exact whitelist scope."""
        rows = self._connection.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents "
            "WHERE asset_hash = ? AND page_url = ? ORDER BY id",
            (asset_hash, page_url),
        ).fetchall()
        return [incident_from_row(row) for row in rows]

    def upsert_detection(self, detection: VerifiedDetection) -> Result[Incident]:
        """Create a Detected incident, or refresh the existing one for the same key."""
        asset = self._connection.execute(
            "SELECT creator_id FROM registered_assets WHERE asset_hash = ?",
            (detection.asset_hash,),
        ).fetchone()
        if asset is None:
            return failed(
                FailureCode.NOT_FOUND, "upsert_detection", safe_detail=DETAIL_ASSET_MISSING
            )
        if str(asset["creator_id"]) != detection.creator_id:
            return failed(
                FailureCode.IDENTITY_CONFLICT,
                "upsert_detection",
                safe_detail=DETAIL_CREATOR_MISMATCH,
            )

        whitelisted = (
            self._connection.execute(
                "SELECT 1 FROM whitelist_entries WHERE asset_hash = ? AND page_url = ?",
                (detection.asset_hash, detection.page_url),
            ).fetchone()
            is not None
        )
        existing = self.by_key(detection.asset_hash, detection.page_url, detection.image_url)
        context_json = encode_context(detection.context)

        if existing is None:
            status = IncidentStatus.FAIR_USE if whitelisted else IncidentStatus.DETECTED
            try:
                self._connection.execute(
                    "INSERT INTO incidents (asset_hash, page_url, image_url, "
                    "creator_id_evidence, payload_created_at, extraction_crc32, context_json, "
                    "first_seen_at, last_seen_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        detection.asset_hash,
                        detection.page_url,
                        detection.image_url,
                        detection.creator_id,
                        detection.payload_created_at,
                        detection.extraction_crc32,
                        context_json,
                        detection.discovered_at,
                        detection.discovered_at,
                        status.value,
                    ),
                )
            except sqlite3.IntegrityError:
                return failed(FailureCode.CONSTRAINT, "upsert_detection")
        else:
            # Rediscovery refreshes recency and evidence only. First-seen never moves.
            status = IncidentStatus.FAIR_USE if whitelisted else existing.status
            self._connection.execute(
                "UPDATE incidents SET last_seen_at = ?, context_json = ?, extraction_crc32 = ?, "
                "payload_created_at = ?, status = ? WHERE id = ?",
                (
                    detection.discovered_at,
                    context_json,
                    detection.extraction_crc32,
                    detection.payload_created_at,
                    status.value,
                    existing.id,
                ),
            )

        refreshed = self.by_key(detection.asset_hash, detection.page_url, detection.image_url)
        if refreshed is None:  # pragma: no cover - write succeeded, row must exist
            return failed(FailureCode.COMMIT_FAILED, "upsert_detection")
        return ok(refreshed)

    def list_active(self) -> Sequence[Incident]:
        """Active incidents, excluding every exact whitelist scope."""
        placeholders = ", ".join("?" for _ in _ACTIVE_STATUS_VALUES)
        rows = self._connection.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents AS i "
            f"WHERE i.status IN ({placeholders}) AND NOT EXISTS ("
            "  SELECT 1 FROM whitelist_entries AS w"
            "  WHERE w.asset_hash = i.asset_hash AND w.page_url = i.page_url"
            ") ORDER BY i.last_seen_at DESC, i.id DESC",
            _ACTIVE_STATUS_VALUES,
        ).fetchall()
        return [incident_from_row(row) for row in rows]

    def list_fair_use(self) -> Sequence[Incident]:
        """Incidents marked Fair Use or suppressed by an exact whitelist scope."""
        rows = self._connection.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM incidents AS i "
            "WHERE i.status = ? OR EXISTS ("
            "  SELECT 1 FROM whitelist_entries AS w"
            "  WHERE w.asset_hash = i.asset_hash AND w.page_url = i.page_url"
            ") ORDER BY i.last_seen_at DESC, i.id DESC",
            (IncidentStatus.FAIR_USE.value,),
        ).fetchall()
        return [incident_from_row(row) for row in rows]

    def apply_status_plan(self, plan: IncidentTransitionPlan) -> Result[TransitionSet]:
        """Apply planned status changes and append the plan's single audit event."""
        for transition in plan.transitions:
            current = self.get(transition.incident_id)
            if current is None:
                return failed(
                    FailureCode.NOT_FOUND,
                    "apply_status_plan",
                    safe_detail=DETAIL_INCIDENT_MISSING,
                )
            if current.status is not transition.previous_status:
                return failed(
                    FailureCode.STALE_PREVIEW,
                    "apply_status_plan",
                    safe_detail=DETAIL_STATUS_CHANGED,
                )

        for transition in plan.transitions:
            self._connection.execute(
                "UPDATE incidents SET status = ? WHERE id = ?",
                (transition.new_status.value, transition.incident_id),
            )

        audit = self._audits.append(plan.audit)
        if audit.failure is not None:
            return Result(failure=audit.failure)
        return ok(TransitionSet(transitions=plan.transitions, audit=audit.unwrap()))


class SqliteWhitelistRepository:
    """Exact-scope fair-use reads and writes inside an open transaction."""

    __slots__ = ("_connection", "_incidents", "_audits")

    def __init__(
        self,
        connection: sqlite3.Connection,
        incidents: SqliteIncidentRepository,
        audits: SqliteAuditRepository,
    ) -> None:
        self._connection = connection
        self._incidents = incidents
        self._audits = audits

    def exact(self, asset_hash: AssetHash, page_url: NormalizedUrl) -> WhitelistEntry | None:
        """Return the whitelist entry for one exact scope."""
        row = self._connection.execute(
            f"SELECT {_WHITELIST_COLUMNS} FROM whitelist_entries "
            "WHERE asset_hash = ? AND page_url = ?",
            (asset_hash, page_url),
        ).fetchone()
        return None if row is None else whitelist_from_row(row)

    def upsert_and_mark_fair_use(self, command: MarkFairUse) -> Result[TransitionSet]:
        """Create or update one entry and set every incident in scope to Fair Use."""
        existing = self.exact(command.asset_hash, command.page_url)
        try:
            if existing is None:
                self._connection.execute(
                    "INSERT INTO whitelist_entries (asset_hash, page_url, rationale, created_at, "
                    "modified_at, related_incident_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        command.asset_hash,
                        command.page_url,
                        command.rationale,
                        command.at,
                        command.at,
                        command.related_incident_id,
                    ),
                )
            else:
                self._connection.execute(
                    "UPDATE whitelist_entries SET rationale = ?, modified_at = ? WHERE id = ?",
                    (command.rationale, command.at, existing.id),
                )
        except sqlite3.IntegrityError:
            return failed(FailureCode.CONSTRAINT, "mark_fair_use")

        entry = self.exact(command.asset_hash, command.page_url)
        if entry is None:  # pragma: no cover - write succeeded, row must exist
            return failed(FailureCode.COMMIT_FAILED, "mark_fair_use")

        transitions = self._set_scope_status(
            command.asset_hash,
            command.page_url,
            new_status=IncidentStatus.FAIR_USE,
            only_from=None,
        )
        return self._record(
            AuditEventType.FAIR_USE_MARKED,
            command.operation_key,
            command.at,
            command.asset_hash,
            transitions,
            whitelist_id=entry.id,
            incident_id=command.related_incident_id,
        )

    def remove_and_reopen(self, command: RemoveFairUse) -> Result[TransitionSet]:
        """Delete one exact entry and reopen only its unresolved Fair Use incidents."""
        existing = self.exact(command.asset_hash, command.page_url)
        if existing is None:
            return failed(
                FailureCode.NOT_FOUND,
                "remove_fair_use",
                safe_detail=DETAIL_WHITELIST_MISSING,
            )

        self._connection.execute("DELETE FROM whitelist_entries WHERE id = ?", (existing.id,))
        transitions = self._set_scope_status(
            command.asset_hash,
            command.page_url,
            new_status=IncidentStatus.DETECTED,
            only_from=IncidentStatus.FAIR_USE,
        )
        return self._record(
            AuditEventType.FAIR_USE_REMOVED,
            command.operation_key,
            command.at,
            command.asset_hash,
            transitions,
            whitelist_id=existing.id,
            incident_id=None,
        )

    def _record(
        self,
        event_type: AuditEventType,
        operation_key: OperationKey,
        at: UtcTimestamp,
        asset_hash: AssetHash,
        transitions: tuple[IncidentTransition, ...],
        *,
        whitelist_id: int | None,
        incident_id: int | None,
    ) -> Result[TransitionSet]:
        audit = self._audits.append(
            NewAuditEvent(
                event_type=event_type,
                occurred_at=at,
                operation_key=operation_key,
                asset_hash_tombstone=asset_hash,
                incident_id=incident_id,
                whitelist_id=whitelist_id,
                previous_statuses={item.incident_id: item.previous_status for item in transitions},
                new_statuses={item.incident_id: item.new_status for item in transitions},
            )
        )
        if audit.failure is not None:
            return Result(failure=audit.failure)
        return ok(TransitionSet(transitions=transitions, audit=audit.unwrap()))

    def _set_scope_status(
        self,
        asset_hash: AssetHash,
        page_url: NormalizedUrl,
        *,
        new_status: IncidentStatus,
        only_from: IncidentStatus | None,
    ) -> tuple[IncidentTransition, ...]:
        transitions: list[IncidentTransition] = []
        for incident in self._incidents.by_scope(asset_hash, page_url):
            if only_from is not None and incident.status is not only_from:
                continue
            if incident.status is new_status:
                continue
            self._connection.execute(
                "UPDATE incidents SET status = ? WHERE id = ?",
                (new_status.value, incident.id),
            )
            transitions.append(
                IncidentTransition(
                    incident_id=incident.id,
                    previous_status=incident.status,
                    new_status=new_status,
                )
            )
        return tuple(transitions)


class SqliteOperationRepository:
    """Idempotency receipts inside an open transaction."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def committed(self, key: OperationKey) -> CommittedOperation | None:
        """Return the receipt for a previously committed operation."""
        row = self._connection.execute(
            f"SELECT {_RECEIPT_COLUMNS} FROM operation_receipts WHERE operation_key = ?", (key,)
        ).fetchone()
        return None if row is None else receipt_from_row(row)

    def record(self, receipt: CommittedOperation) -> Result[CommittedOperation]:
        """Record one receipt so an identical retry returns the same outcome."""
        try:
            self._connection.execute(
                "INSERT INTO operation_receipts (operation_key, operation_type, target_ids_json, "
                "requested_values_hash, outcome_json, committed_at, audit_event_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.operation_key,
                    receipt.operation_type,
                    encode_target_ids(receipt.target_ids),
                    receipt.requested_values_hash,
                    encode_target_ids(receipt.outcome),
                    receipt.committed_at,
                    receipt.audit_event_id,
                ),
            )
        except sqlite3.IntegrityError:
            return failed(
                FailureCode.CONSTRAINT,
                "record_operation",
                safe_detail=DETAIL_DUPLICATE_OPERATION,
            )
        recorded = self.committed(receipt.operation_key)
        if recorded is None:  # pragma: no cover - insert succeeded, row must exist
            return failed(FailureCode.COMMIT_FAILED, "record_operation")
        return ok(recorded)
