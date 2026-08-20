"""Canonical payload serialization and strict parsing.

Requirements: 3.2-3.8, 20.3, 20.4, 20.10
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from provenance.domain.errors import FailureCode
from provenance.domain.models import AssetHash, CreatorId, WatermarkPayload
from provenance.domain.payload import (
    DETAIL_DUPLICATE_KEY,
    DETAIL_INVALID_ASSET_HASH,
    DETAIL_INVALID_CREATED_AT,
    DETAIL_INVALID_CREATOR_ID,
    DETAIL_INVALID_JSON,
    DETAIL_INVALID_UTF8,
    DETAIL_KEY_SET,
    DETAIL_NONCANONICAL,
    DETAIL_NOT_OBJECT,
    DETAIL_TRAILING_DATA,
    KEY_ASSET_HASH,
    KEY_CREATED_AT,
    KEY_CREATOR_ID,
    create_payload,
    parse_payload,
    serialize_fields,
    serialize_payload,
)
from provenance.domain.time import UtcTimestamp

pytestmark = pytest.mark.unit

VALID_HASH = "a" * 64
VALID_CREATOR = "studio.creator_1-x"
VALID_TIMESTAMP = "2026-02-03T04:05:06Z"

CANONICAL_BYTES = (
    f'{{"asset_hash":"{VALID_HASH}","created_at":"{VALID_TIMESTAMP}",'
    f'"creator_id":"{VALID_CREATOR}"}}'.encode()
)

# Structural variants used by the corruption tests.
BYTES_MISSING_CREATOR = f'{{"asset_hash":"{VALID_HASH}","created_at":"{VALID_TIMESTAMP}"}}'.encode()
BYTES_MISSING_HASH = f'{{"created_at":"{VALID_TIMESTAMP}","creator_id":"{VALID_CREATOR}"}}'.encode()
BYTES_EXTRA_FIELD = (
    f'{{"asset_hash":"{VALID_HASH}","created_at":"{VALID_TIMESTAMP}",'
    f'"creator_id":"{VALID_CREATOR}","extra":"x"}}'.encode()
)
BYTES_DUPLICATE_HASH = (
    f'{{"asset_hash":"{VALID_HASH}","asset_hash":"{VALID_HASH}",'
    f'"created_at":"{VALID_TIMESTAMP}","creator_id":"{VALID_CREATOR}"}}'.encode()
)
BYTES_SPACE_AFTER_COLON = (
    f'{{"asset_hash": "{VALID_HASH}","created_at":"{VALID_TIMESTAMP}",'
    f'"creator_id":"{VALID_CREATOR}"}}'.encode()
)
BYTES_KEYS_REORDERED = (
    f'{{"created_at":"{VALID_TIMESTAMP}","asset_hash":"{VALID_HASH}",'
    f'"creator_id":"{VALID_CREATOR}"}}'.encode()
)
BYTES_NEWLINE_INSIDE_OBJECT = (
    f'{{\n"asset_hash":"{VALID_HASH}","created_at":"{VALID_TIMESTAMP}",'
    f'"creator_id":"{VALID_CREATOR}"}}'.encode()
)
BYTES_NON_STRING_HASH = (
    f'{{"asset_hash":123,"created_at":"{VALID_TIMESTAMP}","creator_id":"{VALID_CREATOR}"}}'.encode()
)


class FixedClock:
    """Deterministic clock."""

    def __init__(self, value: datetime) -> None:
        self._value = value

    def utc_now(self) -> datetime:
        return self._value

    def monotonic(self) -> float:
        return 0.0


def _payload(**overrides: str) -> WatermarkPayload:
    values = {
        "asset_hash": VALID_HASH,
        "creator_id": VALID_CREATOR,
        "created_at": VALID_TIMESTAMP,
    }
    values.update(overrides)
    return WatermarkPayload(
        asset_hash=AssetHash(values["asset_hash"]),
        creator_id=CreatorId(values["creator_id"]),
        created_at=UtcTimestamp(values["created_at"]),
    )


def _bytes_with(asset_hash: str = VALID_HASH, creator_id: str = VALID_CREATOR) -> bytes:
    """Canonical layout with substituted values, used for field-level corruption."""
    return (
        f'{{"asset_hash":"{asset_hash}","created_at":"{VALID_TIMESTAMP}",'
        f'"creator_id":"{creator_id}"}}'.encode()
    )


def _bytes_with_timestamp(created_at: str) -> bytes:
    """Canonical layout with a substituted timestamp."""
    return (
        f'{{"asset_hash":"{VALID_HASH}","created_at":"{created_at}",'
        f'"creator_id":"{VALID_CREATOR}"}}'.encode()
    )


def test_create_payload_samples_the_clock_once_and_truncates() -> None:
    clock = FixedClock(datetime(2026, 2, 3, 4, 5, 6, 999_999, tzinfo=UTC))

    payload = create_payload(AssetHash(VALID_HASH), CreatorId(VALID_CREATOR), clock)

    assert payload.asset_hash == VALID_HASH
    assert payload.creator_id == VALID_CREATOR
    assert payload.created_at == VALID_TIMESTAMP


def test_serialization_is_canonical_bytes() -> None:
    assert serialize_payload(_payload()).unwrap() == CANONICAL_BYTES


def test_serialization_orders_keys_lexicographically_without_whitespace() -> None:
    serialized = serialize_payload(_payload()).unwrap().decode()

    assert serialized.index(KEY_ASSET_HASH) < serialized.index(KEY_CREATED_AT)
    assert serialized.index(KEY_CREATED_AT) < serialized.index(KEY_CREATOR_ID)
    assert " " not in serialized
    assert "\n" not in serialized


def test_round_trip_returns_the_same_fields() -> None:
    payload = _payload()

    parsed = parse_payload(serialize_payload(payload).unwrap()).unwrap()

    assert parsed == payload


def test_round_trip_reproduces_the_same_bytes() -> None:
    parsed = parse_payload(CANONICAL_BYTES).unwrap()

    assert serialize_payload(parsed).unwrap() == CANONICAL_BYTES


@pytest.mark.parametrize(
    "timestamp",
    ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "2024-02-29T23:59:59Z"],
)
def test_boundary_timestamps_round_trip(timestamp: str) -> None:
    payload = _payload(created_at=timestamp)

    assert parse_payload(serialize_payload(payload).unwrap()).unwrap() == payload


@pytest.mark.parametrize("creator_id", ["a", "a" * 64, "a.b_c-d", "0"])
def test_boundary_creator_ids_round_trip(creator_id: str) -> None:
    payload = _payload(creator_id=creator_id)

    assert parse_payload(serialize_payload(payload).unwrap()).unwrap() == payload


@pytest.mark.parametrize(
    ("data", "expected_detail"),
    [
        (b"\xff\xfe\x00", DETAIL_INVALID_UTF8),
        (b"not json", DETAIL_INVALID_JSON),
        (b"{", DETAIL_INVALID_JSON),
        (b'"a string"', DETAIL_NOT_OBJECT),
        (b"[]", DETAIL_NOT_OBJECT),
        (b"123", DETAIL_NOT_OBJECT),
        (b"null", DETAIL_NOT_OBJECT),
        (CANONICAL_BYTES + b" ", DETAIL_TRAILING_DATA),
        (CANONICAL_BYTES + b"\n", DETAIL_TRAILING_DATA),
        (CANONICAL_BYTES + b"{}", DETAIL_TRAILING_DATA),
        (b" " + CANONICAL_BYTES, DETAIL_INVALID_JSON),
    ],
)
def test_structurally_invalid_payloads_are_corrupt(data: bytes, expected_detail: str) -> None:
    failure = parse_payload(data).unwrap_failure()

    assert failure.code is FailureCode.CORRUPT_WATERMARK
    assert failure.safe_detail == expected_detail


def test_duplicate_member_names_are_corrupt() -> None:
    failure = parse_payload(BYTES_DUPLICATE_HASH).unwrap_failure()

    assert failure.safe_detail == DETAIL_DUPLICATE_KEY


@pytest.mark.parametrize(
    "data",
    [BYTES_MISSING_CREATOR, BYTES_MISSING_HASH, BYTES_EXTRA_FIELD],
)
def test_wrong_key_sets_are_corrupt(data: bytes) -> None:
    assert parse_payload(data).unwrap_failure().safe_detail == DETAIL_KEY_SET


@pytest.mark.parametrize("bad_hash", ["A" * 64, "a" * 63, "a" * 65, "z" * 64, ""])
def test_invalid_asset_hashes_are_corrupt(bad_hash: str) -> None:
    failure = parse_payload(_bytes_with(asset_hash=bad_hash)).unwrap_failure()

    assert failure.safe_detail == DETAIL_INVALID_ASSET_HASH


@pytest.mark.parametrize("bad_creator", ["", "a" * 65, "has space", "créator", "a/b"])
def test_invalid_creator_ids_are_corrupt(bad_creator: str) -> None:
    failure = parse_payload(_bytes_with(creator_id=bad_creator)).unwrap_failure()

    assert failure.safe_detail == DETAIL_INVALID_CREATOR_ID


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "2026-02-30T00:00:00Z",
        "2023-02-29T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:60:00Z",
        "2026-01-01T00:00:60Z",
        "2026-01-01T00:00:00",
        "2026-01-01T00:00:00.000Z",
        "2026-01-01 00:00:00Z",
    ],
)
def test_invalid_timestamps_are_corrupt(bad_timestamp: str) -> None:
    failure = parse_payload(_bytes_with_timestamp(bad_timestamp)).unwrap_failure()

    assert failure.safe_detail == DETAIL_INVALID_CREATED_AT


@pytest.mark.parametrize(
    "data",
    [BYTES_SPACE_AFTER_COLON, BYTES_KEYS_REORDERED, BYTES_NEWLINE_INSIDE_OBJECT],
)
def test_noncanonical_encodings_are_corrupt(data: bytes) -> None:
    assert parse_payload(data).unwrap_failure().safe_detail == DETAIL_NONCANONICAL


def test_non_string_values_are_corrupt() -> None:
    failure = parse_payload(BYTES_NON_STRING_HASH).unwrap_failure()

    assert failure.code is FailureCode.CORRUPT_WATERMARK


def test_corrupt_results_never_expose_identity_fields() -> None:
    result = parse_payload(b'{"asset_hash":"nope","created_at":"x","creator_id":"y"}')

    assert result.value is None
    assert result.unwrap_failure().fields == ()


def test_serializer_reports_every_invalid_field_and_emits_no_bytes() -> None:
    result = serialize_fields(
        {KEY_ASSET_HASH: "short", KEY_CREATED_AT: "not-a-time", KEY_CREATOR_ID: "bad id"}
    )
    failure = result.unwrap_failure()

    assert result.value is None
    assert failure.code is FailureCode.INVALID_FIELD
    assert {issue.field_key for issue in failure.fields} == {
        KEY_ASSET_HASH,
        KEY_CREATED_AT,
        KEY_CREATOR_ID,
    }


def test_serializer_reports_missing_and_unexpected_fields() -> None:
    failure = serialize_fields({KEY_ASSET_HASH: VALID_HASH, "extra": "value"}).unwrap_failure()

    reported = {(issue.field_key, issue.code) for issue in failure.fields}

    assert (KEY_CREATED_AT, FailureCode.MISSING_FIELD) in reported
    assert (KEY_CREATOR_ID, FailureCode.MISSING_FIELD) in reported
    assert ("extra", FailureCode.INVALID_FIELD) in reported


def test_serializer_rejects_non_string_values() -> None:
    failure = serialize_fields(
        {KEY_ASSET_HASH: VALID_HASH, KEY_CREATED_AT: 20260203, KEY_CREATOR_ID: VALID_CREATOR}
    ).unwrap_failure()

    assert [issue.field_key for issue in failure.fields] == [KEY_CREATED_AT]
