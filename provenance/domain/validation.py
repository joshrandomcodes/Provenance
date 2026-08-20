"""Pure, accumulating validation for Forge input.

Validation never hashes, decodes pixels, embeds, or mutates the Registry. It counts
Unicode code points, reports every applicable issue, and binds each issue to a
stable field key for the UI error summary.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 12.1, 12.8, 19.8
"""

from __future__ import annotations

import re
from typing import Final

from provenance.domain.errors import FailureCode, FieldIssue, ValidationReport
from provenance.domain.models import (
    MAX_CREATOR_ID_CODE_POINTS,
    MAX_DECODED_PIXELS,
    MAX_DISPLAY_NAME_CODE_POINTS,
    MAX_EMAIL_CODE_POINTS,
    MAX_POSTAL_ADDRESS_CODE_POINTS,
    MAX_RATIONALE_CODE_POINTS,
    MAX_RIGHTS_STATEMENT_CODE_POINTS,
    MAX_UPLOAD_BYTES,
    MIN_EMAIL_CODE_POINTS,
    CreatorMetadata,
    UploadMetadata,
)

CREATOR_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z", re.ASCII)
NUL: Final = "\x00"

FIELD_FILE: Final = "upload_file"
FIELD_CREATOR_ID: Final = "creator_id"
FIELD_DISPLAY_NAME: Final = "display_name"
FIELD_CONTACT_EMAIL: Final = "contact_email"
FIELD_POSTAL_ADDRESS: Final = "postal_address"
FIELD_RIGHTS_STATEMENT: Final = "rights_statement"
FIELD_RATIONALE: Final = "fair_use_rationale"


def _issue(field_key: str, code: FailureCode, message: str) -> FieldIssue:
    return FieldIssue(field_key=field_key, code=code, message=message)


def validate_creator_id(value: str, *, field_key: str = FIELD_CREATOR_ID) -> ValidationReport:
    """Validate a Creator_ID against the exact character set and length."""
    issues: list[FieldIssue] = []
    if NUL in value:
        issues.append(
            _issue(field_key, FailureCode.FORBIDDEN_CHARACTER, "Remove the NUL character.")
        )
    if value == "":
        issues.append(_issue(field_key, FailureCode.MISSING_FIELD, "Enter a creator ID."))
    elif len(value) > MAX_CREATOR_ID_CODE_POINTS:
        issues.append(
            _issue(
                field_key,
                FailureCode.FIELD_TOO_LONG,
                f"Use at most {MAX_CREATOR_ID_CODE_POINTS} characters.",
            )
        )
    elif CREATOR_ID_PATTERN.match(value) is None:
        issues.append(
            _issue(
                field_key,
                FailureCode.INVALID_FIELD,
                "Use only ASCII letters, digits, periods, underscores, or hyphens.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_display_name(value: str) -> ValidationReport:
    """Validate the creator display name length and characters."""
    issues: list[FieldIssue] = []
    if NUL in value:
        issues.append(
            _issue(FIELD_DISPLAY_NAME, FailureCode.FORBIDDEN_CHARACTER, "Remove the NUL character.")
        )
    if value == "":
        issues.append(
            _issue(FIELD_DISPLAY_NAME, FailureCode.MISSING_FIELD, "Enter a display name.")
        )
    elif len(value) > MAX_DISPLAY_NAME_CODE_POINTS:
        issues.append(
            _issue(
                FIELD_DISPLAY_NAME,
                FailureCode.FIELD_TOO_LONG,
                f"Use at most {MAX_DISPLAY_NAME_CODE_POINTS} characters.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_contact_email(value: str | None) -> ValidationReport:
    """Validate an optional contact email using the exact documented constraints."""
    if value is None or value == "":
        return ValidationReport()

    issues: list[FieldIssue] = []
    if NUL in value:
        issues.append(
            _issue(
                FIELD_CONTACT_EMAIL, FailureCode.FORBIDDEN_CHARACTER, "Remove the NUL character."
            )
        )
    if len(value) < MIN_EMAIL_CODE_POINTS or len(value) > MAX_EMAIL_CODE_POINTS:
        issues.append(
            _issue(
                FIELD_CONTACT_EMAIL,
                FailureCode.INVALID_FIELD,
                f"Use between {MIN_EMAIL_CODE_POINTS} and {MAX_EMAIL_CODE_POINTS} characters.",
            )
        )
    if not has_single_at_with_parts(value):
        issues.append(
            _issue(
                FIELD_CONTACT_EMAIL,
                FailureCode.INVALID_FIELD,
                "Use one @ separator with text before and after it.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def has_single_at_with_parts(value: str) -> bool:
    """True when the value has exactly one @ with non-empty local and domain parts."""
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return local != "" and domain != ""


def _validate_optional_text(
    value: str | None, field_key: str, maximum_code_points: int
) -> ValidationReport:
    if value is None or value == "":
        return ValidationReport()

    issues: list[FieldIssue] = []
    if NUL in value:
        issues.append(
            _issue(field_key, FailureCode.FORBIDDEN_CHARACTER, "Remove the NUL character.")
        )
    if len(value) > maximum_code_points:
        issues.append(
            _issue(
                field_key,
                FailureCode.FIELD_TOO_LONG,
                f"Use at most {maximum_code_points} characters.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_creator_metadata(metadata: CreatorMetadata) -> ValidationReport:
    """Validate every Creator_Metadata field and accumulate all issues."""
    report = validate_creator_id(metadata.creator_id)
    report = report.merged_with(validate_display_name(metadata.display_name))
    report = report.merged_with(validate_contact_email(metadata.contact_email))
    report = report.merged_with(
        _validate_optional_text(
            metadata.postal_address, FIELD_POSTAL_ADDRESS, MAX_POSTAL_ADDRESS_CODE_POINTS
        )
    )
    return report.merged_with(
        _validate_optional_text(
            metadata.rights_statement, FIELD_RIGHTS_STATEMENT, MAX_RIGHTS_STATEMENT_CODE_POINTS
        )
    )


def validate_upload_metadata(upload: UploadMetadata) -> ValidationReport:
    """Validate upload size limits before any decode is attempted."""
    issues: list[FieldIssue] = []
    if upload.byte_count <= 0:
        issues.append(_issue(FIELD_FILE, FailureCode.EMPTY_FILE, "Choose a file with content."))
    elif upload.byte_count > MAX_UPLOAD_BYTES:
        issues.append(
            _issue(
                FIELD_FILE,
                FailureCode.BYTE_LIMIT,
                f"Choose a file of at most {MAX_UPLOAD_BYTES} bytes.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_dimensions(width: int, height: int) -> ValidationReport:
    """Validate decoded dimensions and the decoded pixel ceiling."""
    issues: list[FieldIssue] = []
    if width < 1 or height < 1:
        issues.append(
            _issue(
                FIELD_FILE,
                FailureCode.INVALID_DIMENSIONS,
                "Choose an image at least one pixel wide and tall.",
            )
        )
    elif width * height > MAX_DECODED_PIXELS:
        issues.append(
            _issue(
                FIELD_FILE,
                FailureCode.PIXEL_LIMIT,
                f"Choose an image of at most {MAX_DECODED_PIXELS} pixels.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_fair_use_rationale(value: str) -> ValidationReport:
    """Validate a fair-use rationale length and characters."""
    issues: list[FieldIssue] = []
    if NUL in value:
        issues.append(
            _issue(FIELD_RATIONALE, FailureCode.FORBIDDEN_CHARACTER, "Remove the NUL character.")
        )
    if value == "":
        issues.append(_issue(FIELD_RATIONALE, FailureCode.MISSING_FIELD, "Enter a rationale."))
    elif len(value) > MAX_RATIONALE_CODE_POINTS:
        issues.append(
            _issue(
                FIELD_RATIONALE,
                FailureCode.FIELD_TOO_LONG,
                f"Use at most {MAX_RATIONALE_CODE_POINTS} characters.",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_forge_submission(
    upload: UploadMetadata, metadata: CreatorMetadata
) -> ValidationReport:
    """Validate an entire Forge submission before any hashing or embedding."""
    return validate_upload_metadata(upload).merged_with(validate_creator_metadata(metadata))
