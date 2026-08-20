"""Property 5: Invalid payloads reveal no identity.

Validates: Requirements 3.5, 3.6, 3.7, 20.10
"""

from __future__ import annotations

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.errors import FailureCode
from provenance.domain.models import WatermarkPayload
from provenance.domain.payload import (
    DETAIL_DUPLICATE_KEY,
    DETAIL_INVALID_ASSET_HASH,
    DETAIL_INVALID_CREATED_AT,
    DETAIL_INVALID_CREATOR_ID,
    DETAIL_INVALID_JSON,
    DETAIL_INVALID_UTF8,
    DETAIL_KEY_SET,
    DETAIL_NON_STRING,
    DETAIL_NONCANONICAL,
    DETAIL_NOT_OBJECT,
    DETAIL_TRAILING_DATA,
    parse_payload,
    serialize_payload,
)
from tests.strategies import (
    INVALID_TIMESTAMPS,
    invalid_asset_hashes,
    invalid_creator_ids,
    payloads,
)

KNOWN_DETAILS: Final = frozenset(
    {
        DETAIL_INVALID_UTF8,
        DETAIL_INVALID_JSON,
        DETAIL_TRAILING_DATA,
        DETAIL_NOT_OBJECT,
        DETAIL_DUPLICATE_KEY,
        DETAIL_KEY_SET,
        DETAIL_NON_STRING,
        DETAIL_INVALID_ASSET_HASH,
        DETAIL_INVALID_CREATOR_ID,
        DETAIL_INVALID_CREATED_AT,
        DETAIL_NONCANONICAL,
    }
)

_VARIANTS: Final = (
    "invalid_utf8",
    "leading_space",
    "trailing_data",
    "not_object",
    "duplicate_key",
    "extra_key",
    "missing_key",
    "non_string_value",
    "space_after_colon",
    "reordered_keys",
    "bad_asset_hash",
    "bad_creator_id",
    "bad_created_at",
)


def _object(asset_hash: str, created_at: str, creator_id: str) -> bytes:
    return (
        f'{{"asset_hash":"{asset_hash}","created_at":"{created_at}",'
        f'"creator_id":"{creator_id}"}}'.encode()
    )


@st.composite
def corrupt_payload_bytes(draw: st.DrawFn) -> bytes:
    """Build byte strings that must never parse as a valid payload."""
    payload: WatermarkPayload = draw(payloads())
    canonical = serialize_payload(payload).unwrap()
    asset_hash = payload.asset_hash
    created_at = payload.created_at
    creator_id = payload.creator_id
    variant = draw(st.sampled_from(_VARIANTS))

    if variant == "invalid_utf8":
        return b"\xff" + canonical
    if variant == "leading_space":
        return b" " + canonical
    if variant == "trailing_data":
        return canonical + draw(st.sampled_from([b" ", b"\n", b"\t", b"{}", b"0", b"null"]))
    if variant == "not_object":
        return draw(st.sampled_from([b"[]", b'"text"', b"1", b"null", b"true", b"{", b""]))
    if variant == "duplicate_key":
        return (
            f'{{"asset_hash":"{asset_hash}","asset_hash":"{asset_hash}",'
            f'"created_at":"{created_at}","creator_id":"{creator_id}"}}'.encode()
        )
    if variant == "extra_key":
        return (
            f'{{"asset_hash":"{asset_hash}","created_at":"{created_at}",'
            f'"creator_id":"{creator_id}","extra":"value"}}'.encode()
        )
    if variant == "missing_key":
        dropped = draw(st.sampled_from(["asset_hash", "created_at", "creator_id"]))
        members = {
            "asset_hash": f'"asset_hash":"{asset_hash}"',
            "created_at": f'"created_at":"{created_at}"',
            "creator_id": f'"creator_id":"{creator_id}"',
        }
        del members[dropped]
        return ("{" + ",".join(members.values()) + "}").encode()
    if variant == "non_string_value":
        return (
            f'{{"asset_hash":"{asset_hash}","created_at":"{created_at}",'
            f'"creator_id":{draw(st.sampled_from(["123", "null", "true", "[]", "{}"]))}}}'
        ).encode()
    if variant == "space_after_colon":
        return (
            f'{{"asset_hash": "{asset_hash}","created_at":"{created_at}",'
            f'"creator_id":"{creator_id}"}}'.encode()
        )
    if variant == "reordered_keys":
        return (
            f'{{"creator_id":"{creator_id}","asset_hash":"{asset_hash}",'
            f'"created_at":"{created_at}"}}'.encode()
        )
    if variant == "bad_asset_hash":
        return _object(draw(invalid_asset_hashes()), created_at, creator_id)
    if variant == "bad_creator_id":
        return _object(asset_hash, created_at, draw(invalid_creator_ids()))
    return _object(asset_hash, draw(st.sampled_from(INVALID_TIMESTAMPS)), creator_id)


@given(corrupt_payload_bytes())
def test_constructed_corruption_is_always_rejected(data: bytes) -> None:
    # Feature: provenance, Property 5: Invalid payloads reveal no identity
    result = parse_payload(data)
    failure = result.unwrap_failure()

    assert result.value is None
    assert failure.code is FailureCode.CORRUPT_WATERMARK
    assert failure.fields == ()
    assert failure.safe_detail in KNOWN_DETAILS


@given(st.binary(max_size=140))
def test_arbitrary_bytes_either_parse_canonically_or_reveal_nothing(data: bytes) -> None:
    # Feature: provenance, Property 5: Invalid payloads reveal no identity
    result = parse_payload(data)

    if result.failure is None:
        # A success is only possible for exactly canonical bytes.
        assert serialize_payload(result.unwrap()).unwrap() == data
        return

    failure = result.unwrap_failure()
    assert result.value is None
    assert failure.code is FailureCode.CORRUPT_WATERMARK
    assert failure.fields == ()
    assert failure.safe_detail in KNOWN_DETAILS


@given(payloads(), st.integers(min_value=0, max_value=255))
def test_single_byte_mutations_stay_canonical_or_reveal_nothing(
    payload: WatermarkPayload, replacement: int
) -> None:
    # Feature: provenance, Property 5: Invalid payloads reveal no identity
    canonical = serialize_payload(payload).unwrap()
    index = replacement % len(canonical)
    mutated = canonical[:index] + bytes([replacement]) + canonical[index + 1 :]

    result = parse_payload(mutated)

    if result.failure is None:
        # Some mutations, such as changing one hex digit of the hash, still describe a
        # well-formed identity. The codec's guarantee is that any accepted bytes are
        # exactly canonical for the identity they carry. Detecting tampering against an
        # embedded original is the CRC-32 check covered by Property 11.
        parsed = result.unwrap()
        assert serialize_payload(parsed).unwrap() == mutated
        if mutated == canonical:
            assert parsed == payload
    else:
        assert result.value is None
        assert result.unwrap_failure().fields == ()
