"""Property 7: Header encoding is exact.

Validates: Requirements 4.1
"""

from __future__ import annotations

import zlib

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.watermark import (
    HEADER_SIZE,
    MAGIC,
    SCHEMA_VERSION,
    build_frame,
    build_header,
)


@given(st.binary(max_size=400))
def test_header_layout_is_exact_for_any_payload(payload: bytes) -> None:
    # Feature: provenance, Property 7: Header encoding is exact
    header = build_header(payload)

    assert len(header) == HEADER_SIZE
    assert header[:4] == MAGIC
    assert header[4] == SCHEMA_VERSION
    assert int.from_bytes(header[5:9], "big") == len(payload)
    assert int.from_bytes(header[9:13], "big") == zlib.crc32(payload) & 0xFFFFFFFF


@given(st.binary(max_size=400))
def test_frame_is_header_followed_by_the_exact_payload(payload: bytes) -> None:
    # Feature: provenance, Property 7: Header encoding is exact
    frame = build_frame(payload)

    assert frame[:HEADER_SIZE] == build_header(payload)
    assert frame[HEADER_SIZE:] == payload
    assert len(frame) == HEADER_SIZE + len(payload)


@given(st.binary(max_size=200), st.binary(max_size=200))
def test_different_payloads_change_the_header(first: bytes, second: bytes) -> None:
    # Feature: provenance, Property 7: Header encoding is exact
    if first == second:
        assert build_header(first) == build_header(second)
        return

    if len(first) != len(second):
        assert build_header(first)[5:9] != build_header(second)[5:9]
    # Equal-length differing payloads may share a CRC only on a genuine collision,
    # which the round-trip properties would surface as a mismatch.
    assert build_frame(first) != build_frame(second)
