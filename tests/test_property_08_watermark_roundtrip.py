"""Property 8: Watermark embedding and extraction round trip.

Validates: Requirements 4.2, 4.9, 20.5
"""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import RgbArray, WatermarkPayload
from provenance.domain.payload import serialize_payload
from provenance.domain.watermark import embed, embed_payload, extract, payload_capacity
from tests.strategies import canvases, payloads


@st.composite
def payload_and_canvas(draw: st.DrawFn) -> tuple[WatermarkPayload, RgbArray]:
    payload = draw(payloads())
    serialized = serialize_payload(payload).unwrap()
    canvas = draw(canvases(minimum_payload_bytes=len(serialized)))
    return payload, canvas


@given(payload_and_canvas())
def test_extract_of_embed_returns_the_same_payload(
    case: tuple[WatermarkPayload, RgbArray],
) -> None:
    # Feature: provenance, Property 8: Watermark embedding and extraction round trip
    payload, canvas = case
    serialized = serialize_payload(payload).unwrap()

    embedded = embed(canvas, None, serialized).unwrap()
    extracted = extract(embedded.rgb).unwrap()

    assert extracted == payload
    assert extracted.asset_hash == payload.asset_hash
    assert extracted.creator_id == payload.creator_id
    assert extracted.created_at == payload.created_at


@given(payload_and_canvas())
def test_typed_embedding_round_trips(case: tuple[WatermarkPayload, RgbArray]) -> None:
    # Feature: provenance, Property 8: Watermark embedding and extraction round trip
    payload, canvas = case

    embedded = embed_payload(canvas, None, payload).unwrap()

    assert extract(embedded.rgb).unwrap() == payload
    assert embedded.capacity_bytes == payload_capacity(embedded.width, embedded.height)


@given(payload_and_canvas())
def test_round_trip_survives_a_strided_view(case: tuple[WatermarkPayload, RgbArray]) -> None:
    # Feature: provenance, Property 8: Watermark embedding and extraction round trip
    payload, canvas = case

    embedded = embed_payload(canvas, None, payload).unwrap()
    padded = np.zeros((embedded.height, embedded.width * 2, 3), dtype=np.uint8)
    padded[:, ::2, :] = embedded.rgb

    assert extract(padded[:, ::2, :]).unwrap() == payload


@given(payload_and_canvas())
def test_embedding_is_deterministic(case: tuple[WatermarkPayload, RgbArray]) -> None:
    # Feature: provenance, Property 8: Watermark embedding and extraction round trip
    payload, canvas = case

    first = embed_payload(canvas, None, payload).unwrap()
    second = embed_payload(canvas, None, payload).unwrap()

    assert np.array_equal(first.rgb, second.rgb)
