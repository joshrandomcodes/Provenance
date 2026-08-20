"""Property 6: Invalid serializer input emits no bytes.

Validates: Requirements 3.8
"""

from __future__ import annotations

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.errors import FailureCode
from provenance.domain.models import WatermarkPayload
from provenance.domain.payload import (
    KEY_ASSET_HASH,
    KEY_CREATED_AT,
    KEY_CREATOR_ID,
    serialize_fields,
)
from tests.strategies import (
    INVALID_TIMESTAMPS,
    invalid_asset_hashes,
    invalid_creator_ids,
    payloads,
)

_MUTATIONS: Final = (
    "drop_field",
    "add_field",
    "non_string_value",
    "invalid_asset_hash",
    "invalid_creator_id",
    "invalid_created_at",
)
_EXTRA_KEYS: Final = ("extra", "x", "asset_hash_2", "createdAt")
_NON_STRINGS: Final[tuple[object, ...]] = (123, None, True, 1.5, (), {})


@st.composite
def invalid_field_maps(draw: st.DrawFn) -> tuple[dict[str, object], set[str]]:
    """Build a field map with at least one invalid field, plus the expected field keys."""
    payload = draw(payloads())
    fields: dict[str, object] = {
        KEY_ASSET_HASH: payload.asset_hash,
        KEY_CREATED_AT: payload.created_at,
        KEY_CREATOR_ID: payload.creator_id,
    }
    expected: set[str] = set()

    for mutation in draw(
        st.lists(st.sampled_from(_MUTATIONS), min_size=1, max_size=3, unique=True)
    ):
        if mutation == "drop_field":
            key = draw(st.sampled_from([KEY_ASSET_HASH, KEY_CREATED_AT, KEY_CREATOR_ID]))
            fields.pop(key, None)
            expected.add(key)
        elif mutation == "add_field":
            key = draw(st.sampled_from(_EXTRA_KEYS))
            fields[key] = "value"
            expected.add(key)
        elif mutation == "non_string_value":
            key = draw(st.sampled_from([KEY_ASSET_HASH, KEY_CREATED_AT, KEY_CREATOR_ID]))
            if key in fields:
                fields[key] = draw(st.sampled_from(_NON_STRINGS))
                expected.add(key)
        elif mutation == "invalid_asset_hash" and KEY_ASSET_HASH in fields:
            fields[KEY_ASSET_HASH] = draw(invalid_asset_hashes())
            expected.add(KEY_ASSET_HASH)
        elif mutation == "invalid_creator_id" and KEY_CREATOR_ID in fields:
            fields[KEY_CREATOR_ID] = draw(invalid_creator_ids())
            expected.add(KEY_CREATOR_ID)
        elif mutation == "invalid_created_at" and KEY_CREATED_AT in fields:
            fields[KEY_CREATED_AT] = draw(st.sampled_from(INVALID_TIMESTAMPS))
            expected.add(KEY_CREATED_AT)

    if not expected:
        fields.pop(KEY_ASSET_HASH, None)
        expected.add(KEY_ASSET_HASH)
    return fields, expected


@given(invalid_field_maps())
def test_invalid_input_reports_every_field_and_emits_no_bytes(
    case: tuple[dict[str, object], set[str]],
) -> None:
    # Feature: provenance, Property 6: Invalid serializer input emits no bytes
    fields, expected_field_keys = case

    result = serialize_fields(fields)
    failure = result.unwrap_failure()

    assert result.value is None
    assert failure.code is FailureCode.INVALID_FIELD
    assert {issue.field_key for issue in failure.fields} == expected_field_keys


@given(invalid_field_maps())
def test_reported_issues_are_deterministic(case: tuple[dict[str, object], set[str]]) -> None:
    # Feature: provenance, Property 6: Invalid serializer input emits no bytes
    fields, _ = case

    first = serialize_fields(fields).unwrap_failure()
    second = serialize_fields(fields).unwrap_failure()

    assert first.fields == second.fields


@given(payloads())
def test_valid_field_maps_are_accepted(payload: WatermarkPayload) -> None:
    # Feature: provenance, Property 6: Invalid serializer input emits no bytes
    result = serialize_fields(
        {
            KEY_ASSET_HASH: payload.asset_hash,
            KEY_CREATED_AT: payload.created_at,
            KEY_CREATOR_ID: payload.creator_id,
        }
    )

    assert result.failure is None
    assert result.unwrap().startswith(b'{"asset_hash":"')
