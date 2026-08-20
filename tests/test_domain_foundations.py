"""Unit tests for typed results, UTC timestamps, cancellation, and validation.

Requirements: 2.1-2.5, 3.7, 6.12, 12.1, 12.8, 17.6, 19.8
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from provenance.domain.cancellation import CooperativeCancellationToken, NeverCancelled
from provenance.domain.errors import (
    FailureCode,
    Result,
    ValidationReport,
    failed,
    ok,
)
from provenance.domain.models import (
    MAX_UPLOAD_BYTES,
    CreatorId,
    CreatorMetadata,
    UploadMetadata,
)
from provenance.domain.time import (
    format_utc_timestamp,
    is_valid_utc_timestamp,
    now_timestamp,
    parse_utc_timestamp,
)
from provenance.domain.validation import (
    FIELD_CONTACT_EMAIL,
    FIELD_CREATOR_ID,
    FIELD_DISPLAY_NAME,
    FIELD_FILE,
    FIELD_POSTAL_ADDRESS,
    FIELD_RIGHTS_STATEMENT,
    validate_creator_id,
    validate_dimensions,
    validate_fair_use_rationale,
    validate_forge_submission,
    validate_upload_metadata,
)

pytestmark = pytest.mark.unit


class FixedClock:
    """Deterministic clock for timestamp tests."""

    def __init__(self, value: datetime, monotonic: float = 0.0) -> None:
        self._value = value
        self._monotonic = monotonic

    def utc_now(self) -> datetime:
        return self._value

    def monotonic(self) -> float:
        return self._monotonic


def _metadata(**overrides: object) -> CreatorMetadata:
    base: dict[str, object] = {
        "creator_id": CreatorId("valid.creator_id-1"),
        "display_name": "Valid Creator",
        "contact_email": "creator@example.com",
        "postal_address": None,
        "rights_statement": None,
    }
    base.update(overrides)
    return CreatorMetadata(**base)  # type: ignore[arg-type]


def test_result_requires_exactly_one_of_value_or_failure() -> None:
    assert ok(3).unwrap() == 3
    assert failed(FailureCode.BUSY, "op").unwrap_failure().code is FailureCode.BUSY

    with pytest.raises(ValueError, match="exactly one"):
        Result[int]()
    with pytest.raises(ValueError, match="exactly one"):
        Result(value=1, failure=failed(FailureCode.BUSY, "op").failure)


def test_unwrapping_the_wrong_side_raises() -> None:
    with pytest.raises(ValueError, match="cannot unwrap failed result"):
        failed(FailureCode.BUSY, "op").unwrap()
    with pytest.raises(ValueError, match="cannot unwrap failure"):
        ok("value").unwrap_failure()


def test_validation_report_merges_and_indexes_issues() -> None:
    report = validate_creator_id("").merged_with(validate_fair_use_rationale(""))

    assert not report.is_valid
    assert len(report.issues) == 2
    assert report.for_field(FIELD_CREATOR_ID)[0].code is FailureCode.MISSING_FIELD
    assert ValidationReport().is_valid


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02T03:04:05Z", True),
        ("0001-01-01T00:00:00Z", True),
        ("9999-12-31T23:59:59Z", True),
        ("2024-02-29T12:00:00Z", True),
        ("2023-02-29T12:00:00Z", False),
        ("2026-13-01T00:00:00Z", False),
        ("2026-01-01T24:00:00Z", False),
        ("2026-01-01T00:60:00Z", False),
        ("2026-01-01T00:00:60Z", False),
        ("2026-01-01T00:00:00", False),
        ("2026-01-01T00:00:00z", False),
        ("2026-1-01T00:00:00Z", False),
        ("2026-01-01T00:00:00.000Z", False),
        (" 2026-01-01T00:00:00Z", False),
        ("2026-01-01T00:00:00+00:00", False),
    ],
)
def test_utc_timestamp_validation_is_exact(value: str, expected: bool) -> None:
    assert is_valid_utc_timestamp(value) is expected


def test_timestamp_formatting_truncates_and_converts_to_utc() -> None:
    value = datetime(2026, 3, 4, 5, 6, 7, 987_654, tzinfo=timezone(timedelta(hours=2)))

    formatted = format_utc_timestamp(value)

    assert formatted == "2026-03-04T03:06:07Z"
    assert parse_utc_timestamp(formatted) == datetime(2026, 3, 4, 3, 6, 7, tzinfo=UTC)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone aware"):
        format_utc_timestamp(datetime(2026, 1, 1, 0, 0, 0))  # noqa: DTZ001


def test_now_timestamp_samples_the_clock_once() -> None:
    clock = FixedClock(datetime(2026, 5, 6, 7, 8, 9, 123, tzinfo=UTC))

    assert now_timestamp(clock) == "2026-05-06T07:08:09Z"


def test_cancellation_token_is_sticky() -> None:
    token = CooperativeCancellationToken()
    before_cancel = token.is_cancelled

    token.cancel()
    token.cancel()
    after_repeated_cancel = token.is_cancelled

    assert before_cancel is False
    assert after_repeated_cancel is True
    assert NeverCancelled().is_cancelled is False


@pytest.mark.parametrize(
    "value",
    ["a", "A9", "creator.id", "creator_id", "creator-id", "a" * 64],
)
def test_valid_creator_ids_are_accepted(value: str) -> None:
    assert validate_creator_id(value).is_valid


@pytest.mark.parametrize(
    "value",
    ["", "a" * 65, "creator id", "créator", "creator/id", "creator\x00id", "creator@id"],
)
def test_invalid_creator_ids_are_rejected(value: str) -> None:
    assert not validate_creator_id(value).is_valid


@pytest.mark.parametrize(
    ("byte_count", "expected_code"),
    [
        (0, FailureCode.EMPTY_FILE),
        (-1, FailureCode.EMPTY_FILE),
        (MAX_UPLOAD_BYTES + 1, FailureCode.BYTE_LIMIT),
    ],
)
def test_upload_size_boundaries(byte_count: int, expected_code: FailureCode) -> None:
    report = validate_upload_metadata(UploadMetadata(file_name="art.png", byte_count=byte_count))

    assert [issue.code for issue in report.issues] == [expected_code]
    assert report.issues[0].field_key == FIELD_FILE


@pytest.mark.parametrize("byte_count", [1, MAX_UPLOAD_BYTES])
def test_upload_size_accepts_inclusive_bounds(byte_count: int) -> None:
    assert validate_upload_metadata(
        UploadMetadata(file_name="art.png", byte_count=byte_count)
    ).is_valid


@pytest.mark.parametrize(
    ("width", "height", "expected_code"),
    [
        (0, 10, FailureCode.INVALID_DIMENSIONS),
        (10, 0, FailureCode.INVALID_DIMENSIONS),
        (10_000, 4_001, FailureCode.PIXEL_LIMIT),
    ],
)
def test_dimension_boundaries(width: int, height: int, expected_code: FailureCode) -> None:
    report = validate_dimensions(width, height)

    assert [issue.code for issue in report.issues] == [expected_code]


def test_dimension_limit_is_inclusive() -> None:
    assert validate_dimensions(10_000, 4_000).is_valid


@pytest.mark.parametrize(
    "email",
    ["a@b", "creator@example.com", "a" * 200 + "@example.com"],
)
def test_valid_contact_emails_are_accepted(email: str) -> None:
    assert validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=10),
        _metadata(contact_email=email),
    ).is_valid


@pytest.mark.parametrize(
    "email",
    ["a@", "@b", "ab", "a@b@c", "no-at-sign", "a" * 250 + "@example.com", "a\x00@b"],
)
def test_invalid_contact_emails_are_rejected(email: str) -> None:
    report = validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=10),
        _metadata(contact_email=email),
    )

    assert not report.is_valid
    assert report.for_field(FIELD_CONTACT_EMAIL)


def test_absent_contact_email_is_allowed() -> None:
    assert validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=10),
        _metadata(contact_email=None),
    ).is_valid


def test_optional_text_limits_and_nul_are_enforced() -> None:
    report = validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=10),
        _metadata(postal_address="x" * 501, rights_statement="fine\x00print"),
    )

    assert {issue.field_key for issue in report.issues} == {
        FIELD_POSTAL_ADDRESS,
        FIELD_RIGHTS_STATEMENT,
    }
    assert report.for_field(FIELD_POSTAL_ADDRESS)[0].code is FailureCode.FIELD_TOO_LONG
    assert report.for_field(FIELD_RIGHTS_STATEMENT)[0].code is FailureCode.FORBIDDEN_CHARACTER


def test_optional_text_accepts_maximum_length() -> None:
    assert validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=10),
        _metadata(postal_address="x" * 500, rights_statement="y" * 500),
    ).is_valid


def test_forge_validation_accumulates_every_issue() -> None:
    report = validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=0),
        CreatorMetadata(
            creator_id=CreatorId("bad id"),
            display_name="",
            contact_email="nope",
            postal_address="x" * 501,
            rights_statement="y" * 501,
        ),
    )

    assert {issue.field_key for issue in report.issues} == {
        FIELD_FILE,
        FIELD_CREATOR_ID,
        FIELD_DISPLAY_NAME,
        FIELD_CONTACT_EMAIL,
        FIELD_POSTAL_ADDRESS,
        FIELD_RIGHTS_STATEMENT,
    }


def test_display_name_boundaries() -> None:
    assert validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=1),
        _metadata(display_name="d" * 200),
    ).is_valid
    assert not validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=1),
        _metadata(display_name="d" * 201),
    ).is_valid


@pytest.mark.parametrize(
    ("rationale", "valid"),
    [("teaching commentary", True), ("r" * 500, True), ("", False), ("r" * 501, False)],
)
def test_fair_use_rationale_boundaries(rationale: str, valid: bool) -> None:
    assert validate_fair_use_rationale(rationale).is_valid is valid


def test_validation_messages_carry_no_submitted_values() -> None:
    private_contact = "creator@private.example"
    report = validate_forge_submission(
        UploadMetadata(file_name="a.png", byte_count=1),
        _metadata(contact_email=f"{private_contact}@extra"),
    )

    assert not report.is_valid
    assert all(private_contact not in issue.message for issue in report.issues)
