"""Closed failure vocabulary and typed result values.

Expected failures are returned as values so no layer depends on exception text and
no user-facing message carries a stack trace or sensitive detail.

Requirements: 1.6, 2.2, 2.4, 2.5, 5.5, 6.8, 6.11, 17.6, 18.2, 18.10, 19.8
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FailureCode(StrEnum):
    """Every expected failure the application can report."""

    # Upload and metadata validation
    EMPTY_FILE = "empty_file"
    BYTE_LIMIT = "byte_limit"
    UNSUPPORTED_FORMAT = "unsupported_format"
    INVALID_DIMENSIONS = "invalid_dimensions"
    PIXEL_LIMIT = "pixel_limit"
    DECODE_FAILURE = "decode_failure"
    INVALID_FIELD = "invalid_field"
    MISSING_FIELD = "missing_field"
    FIELD_TOO_LONG = "field_too_long"
    FIELD_TOO_SHORT = "field_too_short"
    FORBIDDEN_CHARACTER = "forbidden_character"

    # Watermark engine
    CAPACITY_EXCEEDED = "capacity_exceeded"
    NO_WATERMARK = "no_watermark"
    CORRUPT_WATERMARK = "corrupt_watermark"
    PNG_ROUNDTRIP_FAILED = "png_roundtrip_failed"

    # Registry
    CHECKS_PENDING = "checks_pending"
    CHECKS_FAILED = "checks_failed"
    IDENTITY_CONFLICT = "identity_conflict"
    CONSTRAINT = "constraint"
    BUSY = "busy"
    COMMIT_FAILED = "commit_failed"
    STALE_PREVIEW = "stale_preview"
    NOT_FOUND = "not_found"

    # URL and SSRF policy
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    PORT = "port"
    CREDENTIALS = "credentials"
    MALFORMED_HOST = "malformed_host"
    DNS_FAILED = "dns_failed"
    NONPUBLIC_ADDRESS = "nonpublic_address"
    PEER_MISMATCH = "peer_mismatch"

    # Robots and HTTP
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    HTTP_STATUS = "http_status"
    TLS = "tls"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    REDIRECT_LIMIT = "redirect_limit"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"

    # Resource limits
    HTML_LIMIT = "html_limit"
    IMAGE_BYTES_LIMIT = "image_bytes_limit"
    TOTAL_BYTES_LIMIT = "total_bytes_limit"
    SCAN_TIMEOUT = "scan_timeout"
    CANCELLED = "cancelled"

    # Infrastructure resolution
    DNS_NO_RECORDS = "dns_no_records"
    WHOIS_NO_DATA = "whois_no_data"
    WHOIS_MALFORMED = "whois_malformed"
    WHOIS_LIMIT = "whois_limit"
    DEPENDENCY_INCOMPATIBLE = "dependency_incompatible"

    # Confirmation and dispatch
    STALE_CONFIRMATION = "stale_confirmation"
    MISSING_ACKNOWLEDGEMENT = "missing_acknowledgement"
    MISSING_ATTESTATION = "missing_attestation"
    DRAFT_UNAVAILABLE = "draft_unavailable"
    DRAFT_CANCELLED = "draft_cancelled"
    OUTCOME_PENDING = "outcome_pending"

    # Runtime
    UI_COMPATIBILITY_FAILED = "ui_compatibility_failed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class FieldIssue:
    """One validation problem bound to a stable field key."""

    field_key: str
    code: FailureCode
    message: str


@dataclass(frozen=True, slots=True)
class Failure:
    """A safe, user-presentable description of an expected failure."""

    code: FailureCode
    operation: str
    fields: tuple[FieldIssue, ...] = ()
    safe_detail: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class Result[T]:
    """Either a value or a failure, never both."""

    value: T | None = None
    failure: Failure | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.failure is None):
            message = "Result must carry exactly one of value or failure"
            raise ValueError(message)

    @property
    def is_ok(self) -> bool:
        """True when the result carries a value."""
        return self.failure is None

    def unwrap(self) -> T:
        """Return the value, or raise when the result is a failure."""
        if self.value is None:
            message = f"cannot unwrap failed result: {self.failure}"
            raise ValueError(message)
        return self.value

    def unwrap_failure(self) -> Failure:
        """Return the failure, or raise when the result is successful."""
        if self.failure is None:
            message = "cannot unwrap failure from a successful result"
            raise ValueError(message)
        return self.failure


def ok[T](value: T) -> Result[T]:
    """Build a successful result."""
    return Result(value=value)


def failed[T](
    code: FailureCode,
    operation: str,
    *,
    fields: tuple[FieldIssue, ...] = (),
    safe_detail: str | None = None,
    retryable: bool = False,
) -> Result[T]:
    """Build a failed result."""
    return Result(
        failure=Failure(
            code=code,
            operation=operation,
            fields=fields,
            safe_detail=safe_detail,
            retryable=retryable,
        )
    )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Accumulated validation issues for one submission."""

    issues: tuple[FieldIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        """True when no issue was recorded."""
        return len(self.issues) == 0

    def merged_with(self, other: ValidationReport) -> ValidationReport:
        """Combine two reports, preserving issue order."""
        return ValidationReport(issues=self.issues + other.issues)

    def for_field(self, field_key: str) -> tuple[FieldIssue, ...]:
        """Return every issue recorded for one field key."""
        return tuple(issue for issue in self.issues if issue.field_key == field_key)
