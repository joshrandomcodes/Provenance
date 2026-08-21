"""Text normalization, srcset tokenizing, and commerce indicator matching.

Requirements: 9.1, 9.3, 9.6, 21.4, 21.5
"""

from __future__ import annotations

import pytest

from provenance.domain.html_evidence import (
    MAX_CONTEXT_LENGTH,
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_LENGTH,
    bounded_values,
    find_ecommerce_evidence,
    normalize_text,
    parse_srcset,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sunset", "Sunset"),
        ("  Sunset  ", "Sunset"),
        ("Sunset\n\tover   the bay", "Sunset over the bay"),
        ("\u00a0Sunset\u2009", "Sunset"),
        ("", None),
        ("   ", None),
        ("\n\t", None),
        (None, None),
    ],
)
def test_text_normalization_is_deterministic(raw: str | None, expected: str | None) -> None:
    assert normalize_text(raw) == expected


def test_control_characters_are_removed_but_whitespace_collapses() -> None:
    assert normalize_text("Sun\x00set\x07 over\u200b the bay") == "Sunset over the bay"


def test_normalized_text_is_length_bounded() -> None:
    normalized = normalize_text("x" * (MAX_CONTEXT_LENGTH + 500))

    assert normalized is not None
    assert len(normalized) == MAX_CONTEXT_LENGTH


def test_a_custom_limit_is_honored() -> None:
    assert normalize_text("abcdef", limit=3) == "abc"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ()),
        ("   ", ()),
        (",", ()),
        ("a.png", ("a.png",)),
        ("a.png 1x", ("a.png",)),
        ("a.png 1x, b.png 2x", ("a.png", "b.png")),
        ("a.png 1x,b.png 2x", ("a.png", "b.png")),
        ("a.png, b.png", ("a.png", "b.png")),
        ("a.png 400w, b.png 800w, c.png 1200w", ("a.png", "b.png", "c.png")),
        ("  a.png   1x  ,   b.png   2x  ", ("a.png", "b.png")),
        ("a.png,,, b.png", ("a.png", "b.png")),
    ],
)
def test_srcset_tokenizing_follows_the_specification(value: str, expected: tuple[str, ...]) -> None:
    assert parse_srcset(value) == expected


def test_a_comma_inside_a_url_is_preserved() -> None:
    """A naive comma split would corrupt this URL, which is why the tokenizer exists.

    Per the HTML specification, only a *trailing* comma delimits a candidate, so an
    unspaced comma run is part of the URL itself.
    """
    assert parse_srcset("https://cdn.example/w_100,h_200/a.png 1x") == (
        "https://cdn.example/w_100,h_200/a.png",
    )
    assert parse_srcset("a.png,b.png") == ("a.png,b.png",)


def test_descriptors_containing_commas_in_parentheses_do_not_split() -> None:
    assert parse_srcset("a.png (min-width: 10px, 20px) 1x, b.png 2x") == ("a.png", "b.png")


def test_srcset_order_is_left_to_right() -> None:
    assert parse_srcset("first.png 1x, second.png 2x, third.png 3x") == (
        "first.png",
        "second.png",
        "third.png",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Price: 40",
        "PRICE",
        "Add to cart",
        "ADD TO CART",
        "Buy now",
        "$40",
        "$ 40.00",
        "40 \u20ac",
        "\u00a31,299.99",
        "Total 250 USD",
        "https://schema.org/Product",
        "http://schema.org/Offer",
        '"@type": "Product"',
    ],
)
def test_recognized_commerce_indicators_produce_evidence(text: str) -> None:
    assert find_ecommerce_evidence((text,)) != ()


@pytest.mark.parametrize(
    "text",
    [
        "Sunset over the bay",
        "A quiet study in oil",
        "priceless heirloom",
        "ART",
        "40",
        "https://schema.org/Person",
        "gallery opening tonight",
    ],
)
def test_unrelated_text_produces_no_evidence(text: str) -> None:
    assert find_ecommerce_evidence((text,)) == ()


def test_a_bare_three_letter_word_is_not_a_currency_code() -> None:
    assert find_ecommerce_evidence(("OIL ART CAT",)) == ()


def test_absent_and_empty_fields_are_skipped() -> None:
    assert find_ecommerce_evidence((None, "", None)) == ()


def test_evidence_includes_surrounding_context_for_readability() -> None:
    evidence = find_ecommerce_evidence(("Limited print. Price: $250.00. Ships worldwide.",))

    assert len(evidence) == 1
    assert "Price" in evidence[0]
    assert "$250.00" in evidence[0]


def test_overlapping_matches_collapse_to_one_snippet() -> None:
    """A price label and the amount beside it share one window, so one entry is kept."""
    assert len(find_ecommerce_evidence(("Price: $10",))) == 1


def test_distinct_matches_in_one_field_are_reported_separately() -> None:
    padding = " " * 200
    evidence = find_ecommerce_evidence((f"Buy now{padding}Total 99 EUR",))

    assert len(evidence) == 2


def test_fields_are_scanned_in_the_order_supplied() -> None:
    evidence = find_ecommerce_evidence(("Add to cart", "Buy now"))

    assert evidence[0].startswith("Add to cart")
    assert evidence[1].startswith("Buy now")


def test_identical_matches_across_fields_are_deduplicated() -> None:
    assert find_ecommerce_evidence(("Buy now", "Buy now", "buy NOW")) == ("Buy now",)


def test_evidence_count_is_bounded() -> None:
    separator = " " * 200
    crowded = separator.join(f"Buy now item {index}" for index in range(MAX_EVIDENCE_ITEMS + 10))

    assert len(find_ecommerce_evidence((crowded,))) == MAX_EVIDENCE_ITEMS


def test_each_evidence_snippet_is_length_bounded() -> None:
    # An absurdly long amount produces a window wider than the display bound.
    evidence = find_ecommerce_evidence(("$" + "1" * 300,))

    assert len(evidence) == 1
    assert len(evidence[0]) == MAX_EVIDENCE_LENGTH


def test_bounded_values_trims_and_drops_empties() -> None:
    assert bounded_values(("  a  ", "", "   ", "b")) == ("a", "b")


def test_bounded_values_applies_the_limit() -> None:
    assert bounded_values(("abcdef",), limit=2) == ("ab",)
