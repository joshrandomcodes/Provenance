"""Immutable domain models, value types, and command shapes.

Every persistent and transient state named by the requirements is represented here.
Modules in this package must not import Streamlit, requests, sqlite3, python-whois,
or Pillow file APIs. NumPy arrays are accepted as plain pixel containers.

Requirements: 2.1, 3.2, 5.2, 6.1-6.6, 9.3, 10.1-10.7, 11.3, 12.1, 13.1, 14.3-14.5,
16.6, 17.7, 18.3, 18.4, 18.7
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NewType

import numpy as np
from numpy.typing import NDArray

from provenance.domain.time import UtcTimestamp

AssetHash = NewType("AssetHash", str)
CreatorId = NewType("CreatorId", str)
NormalizedUrl = NewType("NormalizedUrl", str)
OperationKey = NewType("OperationKey", str)
ContentHash = NewType("ContentHash", str)

RgbArray = NDArray[np.uint8]
AlphaArray = NDArray[np.uint8]

MAX_UPLOAD_BYTES: Final = 26_214_400
MAX_DECODED_PIXELS: Final = 40_000_000
MAX_DISPLAY_NAME_CODE_POINTS: Final = 200
MAX_CREATOR_ID_CODE_POINTS: Final = 64
MIN_EMAIL_CODE_POINTS: Final = 3
MAX_EMAIL_CODE_POINTS: Final = 254
MAX_POSTAL_ADDRESS_CODE_POINTS: Final = 500
MAX_RIGHTS_STATEMENT_CODE_POINTS: Final = 500
MAX_RATIONALE_CODE_POINTS: Final = 500


class MediaType(StrEnum):
    """Source media types Provenance accepts."""

    PNG = "image/png"
    JPEG = "image/jpeg"


class IncidentStatus(StrEnum):
    """Lifecycle state of one incident."""

    DETECTED = "Detected"
    STRIKE_AUTHORIZED = "Strike Authorized"
    FAIR_USE = "Fair Use"
    CREDIT_REQUESTED = "Credit Requested"


ACTIVE_INCIDENT_STATUSES: Final = (
    IncidentStatus.DETECTED,
    IncidentStatus.STRIKE_AUTHORIZED,
    IncidentStatus.CREDIT_REQUESTED,
)


class ExtractionKind(StrEnum):
    """Terminal classification of one watermark extraction attempt."""

    VERIFIED = "verified"
    NO_WATERMARK = "no_watermark"
    CORRUPT_WATERMARK = "corrupt_watermark"
    UNREGISTERED = "valid_unregistered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScanTerminalReason(StrEnum):
    """Why a scan stopped."""

    COMPLETED = "completed"
    PAGE_FAILURE = "page_failure"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_DECLINED = "robots_declined"
    IMAGE_COUNT_LIMIT = "image_count_limit"
    TOTAL_BYTES_LIMIT = "total_bytes_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class LookupSource(StrEnum):
    """Origin of one piece of infrastructure evidence."""

    DNS = "dns"
    WHOIS = "whois"


class LookupState(StrEnum):
    """Independent outcome of one live infrastructure lookup."""

    RETURNED = "returned"
    NO_RECORDS = "no_records"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AuditEventType(StrEnum):
    """Material user actions recorded in the local audit trail."""

    ASSET_REGISTERED = "asset_registered"
    INCIDENT_DETECTED = "incident_detected"
    FAIR_USE_MARKED = "fair_use_marked"
    FAIR_USE_REMOVED = "fair_use_removed"
    CREDIT_REQUESTED = "credit_requested"
    STRIKE_AUTHORIZED = "strike_authorized"
    DISPATCH_SENT = "dispatch_sent"
    ASSET_DELETED = "asset_deleted"


class DispatchOutcome(StrEnum):
    """User-selected result of one local draft-opening attempt."""

    SENT = "Sent"
    NOT_SENT = "Not Sent"
    CANCELLED = "Cancel"


@dataclass(frozen=True, slots=True)
class CreatorMetadata:
    """Creator-supplied registration metadata."""

    creator_id: CreatorId
    display_name: str
    contact_email: str | None = None
    postal_address: str | None = None
    rights_statement: str | None = None


@dataclass(frozen=True, slots=True)
class UploadMetadata:
    """Facts about an uploaded file known before decoding."""

    file_name: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class ImageFacts:
    """Container facts read from an image header before any full-frame allocation.

    Exists so a caller can enforce a pixel budget before a decode allocates memory.
    """

    media_type: MediaType
    width: int
    height: int

    @property
    def pixel_count(self) -> int:
        """Pixels a full decode would allocate."""
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class DecodedSource:
    """A fully decoded source image in eight-bit RGB with optional alpha."""

    width: int
    height: int
    media_type: MediaType
    rgb: RgbArray
    alpha: AlphaArray | None = None

    @property
    def pixel_count(self) -> int:
        """Total decoded pixels."""
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class WatermarkPayload:
    """The exact identity payload embedded in a watermarked image."""

    asset_hash: AssetHash
    creator_id: CreatorId
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class RegisteredAsset:
    """One committed Registry registration."""

    asset_hash: AssetHash
    creator_id: CreatorId
    registered_at: UtcTimestamp
    width: int
    height: int
    source_media_type: MediaType
    metadata: CreatorMetadata


@dataclass(frozen=True, slots=True)
class RegisterAsset:
    """Command requesting creation or reuse of one Registered_Asset."""

    asset_hash: AssetHash
    creator_id: CreatorId
    registered_at: UtcTimestamp
    width: int
    height: int
    source_media_type: MediaType
    metadata: CreatorMetadata


@dataclass(frozen=True, slots=True)
class RegistrationOutcome:
    """Result of a registration attempt."""

    asset: RegisteredAsset
    created: bool


@dataclass(frozen=True, slots=True)
class PageContext:
    """Static page evidence associated with one image element."""

    title: str | None = None
    heading: str | None = None
    figcaption: str | None = None
    alt: str | None = None
    ecommerce_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionEvidence:
    """What extraction produced for one analyzed image."""

    kind: ExtractionKind
    payload: WatermarkPayload | None = None
    crc32: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedDetection:
    """A registry-validated match discovered during a scan."""

    asset_hash: AssetHash
    creator_id: CreatorId
    page_url: NormalizedUrl
    image_url: NormalizedUrl
    payload_created_at: UtcTimestamp
    extraction_crc32: int
    context: PageContext
    discovered_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Incident:
    """One persisted incident linking an asset to a page and image URL."""

    id: int
    asset_hash: AssetHash
    page_url: NormalizedUrl
    image_url: NormalizedUrl
    creator_id_evidence: CreatorId
    payload_created_at: UtcTimestamp
    extraction_crc32: int
    context: PageContext
    first_seen_at: UtcTimestamp
    last_seen_at: UtcTimestamp
    status: IncidentStatus


@dataclass(frozen=True, slots=True)
class WhitelistEntry:
    """One exact-scope fair-use exception."""

    id: int
    asset_hash: AssetHash
    page_url: NormalizedUrl
    rationale: str
    created_at: UtcTimestamp
    modified_at: UtcTimestamp
    related_incident_id: int | None = None


@dataclass(frozen=True, slots=True)
class MarkFairUse:
    """Command creating or updating one exact-scope whitelist entry."""

    asset_hash: AssetHash
    page_url: NormalizedUrl
    rationale: str
    at: UtcTimestamp
    operation_key: OperationKey
    related_incident_id: int | None = None


@dataclass(frozen=True, slots=True)
class RemoveFairUse:
    """Command deleting one exact-scope whitelist entry."""

    asset_hash: AssetHash
    page_url: NormalizedUrl
    at: UtcTimestamp
    operation_key: OperationKey


@dataclass(frozen=True, slots=True)
class IncidentTransition:
    """One incident status change."""

    incident_id: int
    previous_status: IncidentStatus
    new_status: IncidentStatus


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    """An audit event awaiting commit inside the current transaction."""

    event_type: AuditEventType
    occurred_at: UtcTimestamp
    operation_key: OperationKey
    asset_hash_tombstone: AssetHash | None = None
    incident_id: int | None = None
    whitelist_id: int | None = None
    previous_statuses: Mapping[int, IncidentStatus] = field(default_factory=dict)
    new_statuses: Mapping[int, IncidentStatus] = field(default_factory=dict)
    content_hash: ContentHash | None = None
    recipient: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A committed audit event."""

    id: int
    event_type: AuditEventType
    occurred_at: UtcTimestamp
    operation_key: OperationKey
    asset_hash_tombstone: AssetHash | None = None
    incident_id: int | None = None
    whitelist_id: int | None = None
    previous_statuses: Mapping[int, IncidentStatus] = field(default_factory=dict)
    new_statuses: Mapping[int, IncidentStatus] = field(default_factory=dict)
    content_hash: ContentHash | None = None
    recipient: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentTransitionPlan:
    """A pure plan describing one confirmed material action."""

    transitions: tuple[IncidentTransition, ...]
    audit: NewAuditEvent


@dataclass(frozen=True, slots=True)
class TransitionSet:
    """Applied transitions and the audit event committed with them."""

    transitions: tuple[IncidentTransition, ...]
    audit: AuditEvent


@dataclass(frozen=True, slots=True)
class CommittedOperation:
    """Receipt proving one idempotent operation already committed."""

    operation_key: OperationKey
    operation_type: str
    target_ids: Mapping[str, str]
    requested_values_hash: ContentHash
    outcome: Mapping[str, str]
    committed_at: UtcTimestamp
    audit_event_id: int | None = None


@dataclass(frozen=True, slots=True)
class DeletionCounts:
    """Exact dependency counts shown in a deletion preview."""

    incidents: int
    whitelist_entries: int
    audit_events: int


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    """Compare-and-swap preview binding a deletion confirmation."""

    asset_hash: AssetHash
    counts: DeletionCounts
    fingerprint: str


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    """Result of a confirmed asset deletion."""

    deleted: bool
    counts: DeletionCounts
    refreshed_preview: DeletionPreview | None = None


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Terminal accounting for one scan."""

    discovered: int
    attempted: int
    verified: int
    no_watermark: int
    corrupt: int
    unregistered: int
    failed: int
    cancelled: int
    skipped: int
    total_response_bytes: int
    elapsed_seconds: float
    terminal_reason: ScanTerminalReason

    @property
    def is_complete(self) -> bool:
        """True only when the scan finished all discovered work."""
        return self.terminal_reason is ScanTerminalReason.COMPLETED


@dataclass(frozen=True, slots=True)
class InfrastructureValue:
    """One live infrastructure value with its provenance."""

    value: str
    source: LookupSource
    observed_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class InfrastructureLookup:
    """Independent outcome of one live DNS or WHOIS lookup."""

    source: LookupSource
    state: LookupState
    observed_at: UtcTimestamp
    addresses: tuple[str, ...] = ()
    canonical_names: tuple[str, ...] = ()
    registrars: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    omitted_values: int = 0
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class InfrastructureEvidence:
    """Combined DNS and WHOIS evidence for one page host."""

    host: str
    dns: InfrastructureLookup
    whois: InfrastructureLookup

    @property
    def has_any_data(self) -> bool:
        """True when at least one lookup returned data."""
        return LookupState.RETURNED in (self.dns.state, self.whois.state)
