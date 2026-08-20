"""Deterministic mapping between Registry rows and domain models.

JSON columns are written only by these encoders, using sorted keys and no
insignificant whitespace, so stored evidence is byte-stable across writes.

Requirements: 6.12, 9.3, 10.5, 11.3, 18.7
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Final

from provenance.domain.models import (
    AssetHash,
    AuditEvent,
    AuditEventType,
    CommittedOperation,
    ContentHash,
    CreatorId,
    CreatorMetadata,
    Incident,
    IncidentStatus,
    MediaType,
    NormalizedUrl,
    OperationKey,
    PageContext,
    RegisteredAsset,
    WhitelistEntry,
)
from provenance.domain.time import UtcTimestamp

_CONTEXT_TITLE: Final = "title"
_CONTEXT_HEADING: Final = "heading"
_CONTEXT_FIGCAPTION: Final = "figcaption"
_CONTEXT_ALT: Final = "alt"
_CONTEXT_ECOMMERCE: Final = "ecommerce_evidence"


def _dumps(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def encode_context(context: PageContext) -> str:
    """Serialize page context deterministically."""
    return _dumps(
        {
            _CONTEXT_TITLE: context.title,
            _CONTEXT_HEADING: context.heading,
            _CONTEXT_FIGCAPTION: context.figcaption,
            _CONTEXT_ALT: context.alt,
            _CONTEXT_ECOMMERCE: list(context.ecommerce_evidence),
        }
    )


def decode_context(raw: str) -> PageContext:
    """Rebuild page context, tolerating older rows with missing members."""
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        return PageContext()
    evidence = decoded.get(_CONTEXT_ECOMMERCE) or []
    return PageContext(
        title=_optional_str(decoded.get(_CONTEXT_TITLE)),
        heading=_optional_str(decoded.get(_CONTEXT_HEADING)),
        figcaption=_optional_str(decoded.get(_CONTEXT_FIGCAPTION)),
        alt=_optional_str(decoded.get(_CONTEXT_ALT)),
        ecommerce_evidence=tuple(str(item) for item in evidence if isinstance(item, str)),
    )


def encode_statuses(statuses: Mapping[int, IncidentStatus]) -> str:
    """Serialize an incident-id to status map."""
    return _dumps({str(key): value.value for key, value in sorted(statuses.items())})


def decode_statuses(raw: str) -> dict[int, IncidentStatus]:
    """Rebuild an incident-id to status map."""
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        return {}
    return {int(key): IncidentStatus(value) for key, value in decoded.items()}


def encode_target_ids(target_ids: Mapping[str, str]) -> str:
    """Serialize the identifiers an operation acted on."""
    return _dumps(dict(target_ids))


def decode_mapping(raw: str) -> dict[str, str]:
    """Rebuild a flat string mapping."""
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        return {}
    return {str(key): str(value) for key, value in decoded.items()}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def asset_from_row(row: sqlite3.Row) -> RegisteredAsset:
    """Map a registered_assets row."""
    return RegisteredAsset(
        asset_hash=AssetHash(str(row["asset_hash"])),
        creator_id=CreatorId(str(row["creator_id"])),
        registered_at=UtcTimestamp(str(row["registered_at"])),
        width=int(row["width"]),
        height=int(row["height"]),
        source_media_type=MediaType(str(row["source_media_type"])),
        metadata=CreatorMetadata(
            creator_id=CreatorId(str(row["creator_id"])),
            display_name=str(row["display_name"]),
            contact_email=_optional_str(row["contact_email"]),
            postal_address=_optional_str(row["postal_address"]),
            rights_statement=_optional_str(row["rights_statement"]),
        ),
    )


def incident_from_row(row: sqlite3.Row) -> Incident:
    """Map an incidents row."""
    return Incident(
        id=int(row["id"]),
        asset_hash=AssetHash(str(row["asset_hash"])),
        page_url=NormalizedUrl(str(row["page_url"])),
        image_url=NormalizedUrl(str(row["image_url"])),
        creator_id_evidence=CreatorId(str(row["creator_id_evidence"])),
        payload_created_at=UtcTimestamp(str(row["payload_created_at"])),
        extraction_crc32=int(row["extraction_crc32"]),
        context=decode_context(str(row["context_json"])),
        first_seen_at=UtcTimestamp(str(row["first_seen_at"])),
        last_seen_at=UtcTimestamp(str(row["last_seen_at"])),
        status=IncidentStatus(str(row["status"])),
    )


def whitelist_from_row(row: sqlite3.Row) -> WhitelistEntry:
    """Map a whitelist_entries row."""
    related = row["related_incident_id"]
    return WhitelistEntry(
        id=int(row["id"]),
        asset_hash=AssetHash(str(row["asset_hash"])),
        page_url=NormalizedUrl(str(row["page_url"])),
        rationale=str(row["rationale"]),
        created_at=UtcTimestamp(str(row["created_at"])),
        modified_at=UtcTimestamp(str(row["modified_at"])),
        related_incident_id=None if related is None else int(related),
    )


def audit_from_row(row: sqlite3.Row) -> AuditEvent:
    """Map an audit_events row."""
    tombstone = row["asset_hash_tombstone"]
    incident_id = row["incident_id"]
    whitelist_id = row["whitelist_id"]
    content_hash = row["content_hash"]
    return AuditEvent(
        id=int(row["id"]),
        event_type=AuditEventType(str(row["event_type"])),
        occurred_at=UtcTimestamp(str(row["occurred_at"])),
        operation_key=OperationKey(str(row["operation_key"])),
        asset_hash_tombstone=None if tombstone is None else AssetHash(str(tombstone)),
        incident_id=None if incident_id is None else int(incident_id),
        whitelist_id=None if whitelist_id is None else int(whitelist_id),
        previous_statuses=decode_statuses(str(row["previous_statuses_json"])),
        new_statuses=decode_statuses(str(row["new_statuses_json"])),
        content_hash=None if content_hash is None else ContentHash(str(content_hash)),
        recipient=_optional_str(row["recipient"]),
    )


def receipt_from_row(row: sqlite3.Row) -> CommittedOperation:
    """Map an operation_receipts row."""
    audit_event_id = row["audit_event_id"]
    return CommittedOperation(
        operation_key=OperationKey(str(row["operation_key"])),
        operation_type=str(row["operation_type"]),
        target_ids=decode_mapping(str(row["target_ids_json"])),
        requested_values_hash=ContentHash(str(row["requested_values_hash"])),
        outcome=decode_mapping(str(row["outcome_json"])),
        committed_at=UtcTimestamp(str(row["committed_at"])),
        audit_event_id=None if audit_event_id is None else int(audit_event_id),
    )
