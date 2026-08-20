"""Property 4: Canonical payload codec round trip.

Validates: Requirements 3.3, 3.4, 20.3, 20.4
"""

from __future__ import annotations

from hypothesis import given

from provenance.domain.models import WatermarkPayload
from provenance.domain.payload import parse_payload, serialize_payload
from tests.strategies import payloads


@given(payloads())
def test_parse_of_serialize_returns_the_original_fields(payload: WatermarkPayload) -> None:
    # Feature: provenance, Property 4: Canonical payload codec round trip
    serialized = serialize_payload(payload).unwrap()

    parsed = parse_payload(serialized).unwrap()

    assert parsed == payload
    assert parsed.asset_hash == payload.asset_hash
    assert parsed.creator_id == payload.creator_id
    assert parsed.created_at == payload.created_at


@given(payloads())
def test_serialize_of_parse_reproduces_the_same_bytes(payload: WatermarkPayload) -> None:
    # Feature: provenance, Property 4: Canonical payload codec round trip
    serialized = serialize_payload(payload).unwrap()

    reserialized = serialize_payload(parse_payload(serialized).unwrap()).unwrap()

    assert reserialized == serialized


@given(payloads())
def test_serialized_form_is_canonical_utf8_json(payload: WatermarkPayload) -> None:
    # Feature: provenance, Property 4: Canonical payload codec round trip
    serialized = serialize_payload(payload).unwrap()
    text = serialized.decode("utf-8")

    assert text.startswith('{"asset_hash":"')
    assert text.endswith('"}')
    assert '", "' not in text
    assert ": " not in text
    assert text.index('"asset_hash"') < text.index('"created_at"') < text.index('"creator_id"')
