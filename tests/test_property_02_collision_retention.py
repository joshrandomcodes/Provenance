"""Property 2: Canonical source difference collision retention.

This test does not assert that SHA-256 is injective. It asserts that any generated
pair of differing canonical sources yields differing Asset_Hash values, and if a
collision were ever generated Hypothesis reports and retains that exact pair.

Validates: Requirements 20.2
"""

from __future__ import annotations

from hypothesis import assume, given

from provenance.domain.canonical_image import canonical_source_bytes, compute_asset_hash
from tests.strategies import SourceImage, source_images


@given(source_images(), source_images())
def test_differing_canonical_sources_do_not_collide(
    first: SourceImage, second: SourceImage
) -> None:
    # Feature: provenance, Property 2: Canonical source difference collision retention
    first_bytes = canonical_source_bytes(first.width, first.height, first.rgb)
    second_bytes = canonical_source_bytes(second.width, second.height, second.rgb)
    assume(first_bytes != second_bytes)

    first_hash = compute_asset_hash(first.width, first.height, first.rgb)
    second_hash = compute_asset_hash(second.width, second.height, second.rgb)

    assert first_hash != second_hash, (
        "Asset_Hash collision retained for review: "
        f"{first_bytes.hex()} and {second_bytes.hex()} both hash to {first_hash}"
    )


@given(source_images())
def test_equal_canonical_sources_always_agree(source: SourceImage) -> None:
    # Feature: provenance, Property 2: Canonical source difference collision retention
    repeated = compute_asset_hash(source.width, source.height, source.rgb)

    assert repeated == compute_asset_hash(source.width, source.height, source.rgb)
