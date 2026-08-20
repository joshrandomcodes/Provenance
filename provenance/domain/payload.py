"""Canonical Watermark_Payload serializer and strict parser.

The canonical form is UTF-8 JSON containing exactly ``asset_hash``, ``created_at``,
and ``creator_id``, with member names in lexicographic order and no insignificant
whitespace::

    {"asset_hash":"<64 hex>","created_at":"<YYYY-MM-DDTHH:MM:SSZ>","creator_id":"<id>"}

Parsing is byte-exact: anything that is not the canonical representation of valid
fields is Corrupt_Watermark, and no identity field is ever returned for it.

Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 20.3, 20.4, 20.10
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from provenance.domain.canonical_image import is_valid_asset_hash
from provenance.domain.errors import FailureCode, FieldIssue, Result, failed, ok
from provenance.domain.models import AssetHash, CreatorId, WatermarkPayload
from provenance.domain.time import Clock, UtcTimestamp, is_valid_utc_timestamp, now_timestamp
from provenance.domain.validation import CREATOR_ID_PATTERN

KEY_ASSET_HASH: Final = "asset_hash"
KEY_CREATED_AT: Final = "created_at"
KEY_CREATOR_ID: Final = "creator_id"
PAYLOAD_KEYS: Final = frozenset({KEY_ASSET_HASH, KEY_CREATED_AT, KEY_CREATOR_ID})

SERIALIZE_OPERATION: Final = "serialize_payload"
PARSE_OPERATION: Final = "parse_payload"

# Stable, non-sensitive reasons recorded for diagnostics.
DETAIL_INVALID_UTF8: Final = "invalid_utf8"
DETAIL_INVALID_JSON: Final = "invalid_json"
DETAIL_TRAILING_DATA: Final = "trailing_data"
DETAIL_NOT_OBJECT: Final = "not_object"
DETAIL_DUPLICATE_KEY: Final = "duplicate_key"
DETAIL_KEY_SET: Final = "unexpected_key_set"
DETAIL_NON_STRING: Final = "non_string_value"
DETAIL_INVALID_ASSET_HASH: Final = "invalid_asset_hash"
DETAIL_INVALID_CREATOR_ID: Final = "invalid_creator_id"
DETAIL_INVALID_CREATED_AT: Final = "invalid_created_at"
DETAIL_NONCANONICAL: Final = "noncanonical_encoding"


class _DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKeyError(DETAIL_DUPLICATE_KEY)
        seen[key] = value
    return seen


_DECODER: Final = json.JSONDecoder(object_pairs_hook=_reject_duplicate_keys)


def create_payload(asset_hash: AssetHash, creator_id: CreatorId, clock: Clock) -> WatermarkPayload:
    """Build a payload, sampling the clock exactly once."""
    return WatermarkPayload(
        asset_hash=asset_hash,
        creator_id=creator_id,
        created_at=now_timestamp(clock),
    )


def _field_issues(fields: Mapping[str, object]) -> tuple[FieldIssue, ...]:
    issues: list[FieldIssue] = []

    missing = sorted(PAYLOAD_KEYS - set(fields))
    unexpected = sorted(set(fields) - PAYLOAD_KEYS)
    for key in missing:
        issues.append(FieldIssue(key, FailureCode.MISSING_FIELD, "Field is required."))
    for key in unexpected:
        issues.append(FieldIssue(key, FailureCode.INVALID_FIELD, "Field is not permitted."))

    for key in sorted(PAYLOAD_KEYS & set(fields)):
        value = fields[key]
        if not isinstance(value, str):
            issues.append(FieldIssue(key, FailureCode.INVALID_FIELD, "Field must be a string."))
            continue
        if key == KEY_ASSET_HASH and not is_valid_asset_hash(value):
            issues.append(
                FieldIssue(key, FailureCode.INVALID_FIELD, "Expected 64 lowercase hex characters.")
            )
        elif key == KEY_CREATOR_ID and CREATOR_ID_PATTERN.match(value) is None:
            issues.append(
                FieldIssue(key, FailureCode.INVALID_FIELD, "Creator ID does not match the format.")
            )
        elif key == KEY_CREATED_AT and not is_valid_utc_timestamp(value):
            issues.append(
                FieldIssue(key, FailureCode.INVALID_FIELD, "Expected YYYY-MM-DDTHH:MM:SSZ.")
            )

    return tuple(issues)


def _encode_canonical(fields: Mapping[str, str]) -> bytes:
    return json.dumps(
        dict(fields),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def serialize_fields(fields: Mapping[str, object]) -> Result[bytes]:
    """Validate an untyped field map and emit canonical payload bytes.

    Every invalid field is reported, and no bytes are produced for invalid input.
    """
    issues = _field_issues(fields)
    if issues:
        return failed(
            FailureCode.INVALID_FIELD,
            SERIALIZE_OPERATION,
            fields=issues,
        )
    validated = {key: value for key, value in fields.items() if isinstance(value, str)}
    return ok(_encode_canonical(validated))


def serialize_payload(payload: WatermarkPayload) -> Result[bytes]:
    """Serialize a typed payload to canonical bytes."""
    return serialize_fields(
        {
            KEY_ASSET_HASH: payload.asset_hash,
            KEY_CREATED_AT: payload.created_at,
            KEY_CREATOR_ID: payload.creator_id,
        }
    )


def _corrupt(detail: str) -> Result[WatermarkPayload]:
    return failed(FailureCode.CORRUPT_WATERMARK, PARSE_OPERATION, safe_detail=detail)


def parse_payload(data: bytes) -> Result[WatermarkPayload]:
    """Parse canonical payload bytes, or report Corrupt_Watermark.

    A failure never carries an identity or timestamp field.
    """
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _corrupt(DETAIL_INVALID_UTF8)

    try:
        decoded, end_index = _DECODER.raw_decode(text)
    except _DuplicateKeyError:
        return _corrupt(DETAIL_DUPLICATE_KEY)
    except json.JSONDecodeError:
        return _corrupt(DETAIL_INVALID_JSON)

    if end_index != len(text):
        return _corrupt(DETAIL_TRAILING_DATA)
    if not isinstance(decoded, dict):
        return _corrupt(DETAIL_NOT_OBJECT)

    fields: Mapping[str, object] = decoded
    if set(fields) != PAYLOAD_KEYS:
        return _corrupt(DETAIL_KEY_SET)

    asset_hash = fields[KEY_ASSET_HASH]
    created_at = fields[KEY_CREATED_AT]
    creator_id = fields[KEY_CREATOR_ID]
    if not (
        isinstance(asset_hash, str) and isinstance(created_at, str) and isinstance(creator_id, str)
    ):
        return _corrupt(DETAIL_NON_STRING)
    if not is_valid_asset_hash(asset_hash):
        return _corrupt(DETAIL_INVALID_ASSET_HASH)
    if CREATOR_ID_PATTERN.match(creator_id) is None:
        return _corrupt(DETAIL_INVALID_CREATOR_ID)
    if not is_valid_utc_timestamp(created_at):
        return _corrupt(DETAIL_INVALID_CREATED_AT)

    canonical = _encode_canonical(
        {
            KEY_ASSET_HASH: asset_hash,
            KEY_CREATED_AT: created_at,
            KEY_CREATOR_ID: creator_id,
        }
    )
    if canonical != data:
        return _corrupt(DETAIL_NONCANONICAL)

    return ok(
        WatermarkPayload(
            asset_hash=AssetHash(asset_hash),
            creator_id=CreatorId(creator_id),
            created_at=UtcTimestamp(created_at),
        )
    )
