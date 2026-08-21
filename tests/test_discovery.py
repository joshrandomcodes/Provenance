"""Static HTML image discovery, candidate ordering, and context association.

Requirements: 9.1, 9.2, 9.3, 9.6, 18.4, 21.5
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup, Tag

from provenance.domain.errors import FailureCode
from provenance.domain.models import NormalizedUrl
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.urls import AbsoluteHttpUrl
from provenance.infrastructure.discovery import (
    HTML_PARSER,
    DiscoveryResult,
    candidate_values,
    context_for,
    discover_images,
)

pytestmark = pytest.mark.unit

PAGE = AbsoluteHttpUrl(scheme="https", host="gallery.example", port=443, path="/art/page")


class FrozenClock:
    """Clock that never advances, isolating discovery from timing."""

    __slots__ = ()

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, tzinfo=UTC)

    def monotonic(self) -> float:
        return 1_000.0


def _budget(limits: ScanLimits | None = None) -> ScanBudget:
    return ScanBudget(limits or ScanLimits(), FrozenClock(), None)


def _discover(html: str, budget: ScanBudget | None = None) -> DiscoveryResult:
    return discover_images(html.encode(), PAGE, budget or _budget()).unwrap()


def _urls(html: str, budget: ScanBudget | None = None) -> list[str]:
    return [str(candidate.normalized) for candidate in _discover(html, budget).candidates]


def _element(html: str, index: int = 0) -> Tag:
    """The parsed `img` at one index, for direct context assertions."""
    element = BeautifulSoup(html, HTML_PARSER).find_all("img")[index]
    assert isinstance(element, Tag)
    return element


def test_a_document_with_no_images_yields_nothing() -> None:
    result = _discover("<html><body><p>No pictures here.</p></body></html>")

    assert result.candidates == ()
    assert result.considered == 0


def test_a_relative_source_resolves_against_the_page_url() -> None:
    assert _urls('<img src="../img/a.png">') == ["https://gallery.example/img/a.png"]


def test_an_absolute_source_is_kept_as_given() -> None:
    assert _urls('<img src="https://cdn.example/a.png">') == ["https://cdn.example/a.png"]


def test_a_protocol_relative_source_inherits_the_page_scheme() -> None:
    assert _urls('<img src="//cdn.example/a.png">') == ["https://cdn.example/a.png"]


def test_candidates_are_ordered_src_then_srcset_then_data_src() -> None:
    html = '<img src="/s.png" srcset="/a.png 1x, /b.png 2x" data-src="/d.png">'

    assert _urls(html) == [
        "https://gallery.example/s.png",
        "https://gallery.example/a.png",
        "https://gallery.example/b.png",
        "https://gallery.example/d.png",
    ]


def test_images_are_visited_in_document_order() -> None:
    html = '<img src="/first.png"><div><img src="/second.png"></div><img src="/third.png">'

    assert _urls(html) == [
        "https://gallery.example/first.png",
        "https://gallery.example/second.png",
        "https://gallery.example/third.png",
    ]


def test_an_empty_attribute_is_treated_as_absent() -> None:
    result = _discover('<img src="" srcset="   " data-src="">')

    assert result.candidates == ()
    assert result.considered == 0


def test_an_image_with_no_candidate_attributes_is_skipped() -> None:
    assert _discover('<img alt="decorative">').considered == 0


def test_candidate_values_yields_raw_strings_in_order() -> None:
    element = _element('<img src=" /s.png " srcset="/a.png 1x,/b.png 2x" data-src="/d.png">')

    assert list(candidate_values(element)) == ["/s.png", "/a.png", "/b.png", "/d.png"]


@pytest.mark.parametrize(
    "source",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "ftp://cdn.example/a.png",
        "file:///etc/passwd",
        "http://user:pass@cdn.example/a.png",
        "http://exa mple.com/a.png",
        "about:blank",
    ],
)
def test_an_unsafe_or_unsupported_candidate_is_rejected(source: str) -> None:
    result = _discover(f'<img src="{source}">')

    assert result.candidates == ()
    assert result.considered == 1
    assert result.rejected == 1


def test_a_rejected_candidate_does_not_stop_later_candidates() -> None:
    html = '<img src="javascript:alert(1)"><img src="/good.png">'
    result = _discover(html)

    assert [str(item.normalized) for item in result.candidates] == [
        "https://gallery.example/good.png"
    ]
    assert result.rejected == 1


def test_duplicates_are_kept_once_in_first_occurrence_order() -> None:
    html = '<img src="/a.png"><img src="/b.png"><img src="/a.png">'
    result = _discover(html)

    assert [str(item.normalized) for item in result.candidates] == [
        "https://gallery.example/a.png",
        "https://gallery.example/b.png",
    ]
    assert result.duplicates == 1


def test_a_duplicate_across_attributes_of_one_element_is_kept_once() -> None:
    result = _discover('<img src="/a.png" srcset="/a.png 1x" data-src="/a.png">')

    assert result.retained == 1
    assert result.considered == 3
    assert result.duplicates == 2


def test_discovery_retains_at_most_the_unique_image_cap() -> None:
    limits = ScanLimits()
    html = "".join(f'<img src="/img{index}.png">' for index in range(limits.unique_images + 5))
    budget = _budget(limits)

    result = _discover(html, budget)

    assert result.retained == limits.unique_images
    assert result.capped == 5
    assert budget.tally.discovered == limits.unique_images
    assert budget.tally.capped == 5


def test_capped_candidates_are_not_reported_as_discovered() -> None:
    limits = ScanLimits(unique_images=2)
    budget = _budget(limits)

    result = _discover('<img src="/a.png"><img src="/b.png"><img src="/c.png">', budget)

    assert [str(item.normalized) for item in result.candidates] == [
        "https://gallery.example/a.png",
        "https://gallery.example/b.png",
    ]
    assert budget.tally.discovered == 2
    assert result.capped == 1


def test_html_over_the_budget_is_refused_before_parsing() -> None:
    result = discover_images(b"<img src='/a.png'>" * 10, PAGE, _budget(ScanLimits(html_bytes=8)))

    assert result.unwrap_failure().code is FailureCode.HTML_LIMIT


def test_html_at_exactly_the_budget_is_parsed() -> None:
    html = b'<img src="/a.png">'
    result = discover_images(html, PAGE, _budget(ScanLimits(html_bytes=len(html))))

    assert result.unwrap().retained == 1


def test_malformed_markup_still_yields_candidates() -> None:
    html = '<html><body><div><img src="/a.png"><p>unclosed<img src="/b.png"></body>'

    assert len(_urls(html)) == 2


def test_the_document_title_is_normalized_and_shared() -> None:
    html = "<html><head><title>  Sunset\n  Gallery </title></head><body>"
    html += '<img src="/a.png"><img src="/b.png"></body></html>'
    result = _discover(html)

    assert result.title == "Sunset Gallery"
    assert all(item.context.title == "Sunset Gallery" for item in result.candidates)


def test_a_missing_title_is_none() -> None:
    result = _discover('<html><body><img src="/a.png"></body></html>')

    assert result.title is None
    assert result.candidates[0].context.title is None


def test_the_nearest_preceding_heading_is_used() -> None:
    html = '<h1>Collection</h1><h2>Sunset series</h2><img src="/a.png">'

    assert _discover(html).candidates[0].context.heading == "Sunset series"


def test_a_heading_after_the_image_is_not_used() -> None:
    html = '<img src="/a.png"><h1>Later section</h1>'

    assert _discover(html).candidates[0].context.heading is None


def test_each_image_gets_its_own_preceding_heading() -> None:
    html = '<h2>First</h2><img src="/a.png"><h2>Second</h2><img src="/b.png">'
    contexts = [item.context for item in _discover(html).candidates]

    assert contexts[0].heading == "First"
    assert contexts[1].heading == "Second"


def test_an_enclosing_figure_supplies_the_figcaption() -> None:
    html = '<figure><img src="/a.png"><figcaption>Oil on canvas</figcaption></figure>'

    assert _discover(html).candidates[0].context.figcaption == "Oil on canvas"


def test_a_figcaption_before_the_image_is_still_found() -> None:
    html = '<figure><figcaption>Study in blue</figcaption><img src="/a.png"></figure>'

    assert _discover(html).candidates[0].context.figcaption == "Study in blue"


def test_an_image_inside_a_figcaption_uses_that_figcaption() -> None:
    html = '<figure><figcaption>Detail <img src="/a.png"> shown</figcaption></figure>'

    assert _discover(html).candidates[0].context.figcaption == "Detail shown"


def test_a_figure_without_a_caption_has_no_figcaption() -> None:
    assert _discover('<figure><img src="/a.png"></figure>').candidates[0].context.figcaption is None


def test_an_image_outside_any_figure_has_no_figcaption() -> None:
    html = '<figure><figcaption>Not mine</figcaption></figure><div><img src="/a.png"></div>'

    assert _discover(html).candidates[0].context.figcaption is None


def test_a_caption_from_an_outer_figure_does_not_leak_inward() -> None:
    html = '<figure><figcaption>Outer</figcaption><figure><img src="/a.png"></figure></figure>'

    assert _discover(html).candidates[0].context.figcaption is None


def test_alt_text_is_captured_and_normalized() -> None:
    html = '<img src="/a.png" alt="  Sunset  over\n the bay ">'

    assert _discover(html).candidates[0].context.alt == "Sunset over the bay"


def test_an_empty_alt_is_none() -> None:
    assert _discover('<img src="/a.png" alt="">').candidates[0].context.alt is None


def test_a_missing_alt_is_none() -> None:
    assert _discover('<img src="/a.png">').candidates[0].context.alt is None


def test_context_is_shared_by_every_url_from_one_element() -> None:
    html = '<h2>Series</h2><img src="/s.png" srcset="/a.png 1x" alt="Sunset">'
    contexts = {item.context for item in _discover(html).candidates}

    assert len(contexts) == 1
    assert next(iter(contexts)).heading == "Series"


def test_context_does_not_leak_between_sibling_images() -> None:
    html = (
        "<title>Shop</title>"
        '<figure><figcaption>First caption</figcaption><img src="/a.png" alt="One"></figure>'
        '<figure><figcaption>Second caption</figcaption><img src="/b.png" alt="Two"></figure>'
    )
    first, second = (item.context for item in _discover(html).candidates)

    assert (first.figcaption, first.alt) == ("First caption", "One")
    assert (second.figcaption, second.alt) == ("Second caption", "Two")
    assert first.title == second.title == "Shop"


def test_a_currency_amount_near_the_image_is_recorded_as_evidence() -> None:
    html = '<div><img src="/a.png" alt="Print"><p>Price: $250.00</p></div>'
    evidence = _discover(html).candidates[0].context.ecommerce_evidence

    assert evidence != ()
    assert any("$250.00" in item for item in evidence)


def test_an_add_to_cart_control_is_recorded_as_evidence() -> None:
    html = '<div><img src="/a.png"><button>Add to cart</button></div>'
    evidence = _discover(html).candidates[0].context.ecommerce_evidence

    assert any("Add to cart" in item for item in evidence)


def test_schema_product_markup_on_an_ancestor_is_recorded_as_evidence() -> None:
    html = '<div itemtype="https://schema.org/Product"><img src="/a.png"></div>'
    evidence = _discover(html).candidates[0].context.ecommerce_evidence

    assert any("schema.org/Product" in item for item in evidence)


def test_schema_markup_on_the_image_itself_is_recorded_as_evidence() -> None:
    html = '<img src="/a.png" typeof="https://schema.org/Offer">'

    assert _discover(html).candidates[0].context.ecommerce_evidence != ()


def test_a_page_without_commerce_language_records_no_evidence() -> None:
    html = (
        "<title>Sunset Gallery</title><h2>Recent work</h2>"
        '<figure><img src="/a.png" alt="Sunset over the bay">'
        "<figcaption>Oil on canvas</figcaption></figure>"
    )

    assert _discover(html).candidates[0].context.ecommerce_evidence == ()


def test_commerce_evidence_from_a_sibling_block_does_not_reach_another_image() -> None:
    html = (
        '<div><img src="/a.png"><span>Add to cart</span></div>'
        '<div><img src="/b.png"><span>Recently viewed</span></div>'
    )
    first, second = (item.context for item in _discover(html).candidates)

    assert first.ecommerce_evidence != ()
    assert second.ecommerce_evidence == ()


def test_context_for_reads_only_the_supplied_element() -> None:
    html = '<h3>Heading</h3><figure><figcaption>Caption</figcaption><img src="/a.png" alt="Alt">'
    html += "</figure>"

    context = context_for(_element(html), title="Title")

    assert context.title == "Title"
    assert context.heading == "Heading"
    assert context.figcaption == "Caption"
    assert context.alt == "Alt"


def test_the_normalized_form_is_used_for_deduplication() -> None:
    html = '<img src="/a.png"><img src="https://gallery.example:443/a.png">'
    result = _discover(html)

    assert result.retained == 1
    assert result.duplicates == 1
    assert result.candidates[0].normalized == NormalizedUrl("https://gallery.example/a.png")


def test_query_bytes_and_path_case_survive_discovery() -> None:
    html = '<img src="/Art/Sunset.PNG?ref=Fine%20Art">'

    assert _urls(html) == ["https://gallery.example/Art/Sunset.PNG?ref=Fine%20Art"]


def test_a_fragment_is_removed_during_normalization() -> None:
    assert _urls('<img src="/a.png#detail">') == ["https://gallery.example/a.png"]


def test_no_network_access_is_attempted_during_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsing must be purely local, so any socket use here is a defect."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        message = "discovery attempted network access"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    html = '<img src="https://cdn.example/a.png" srcset="//other.example/b.png 2x">'

    assert len(_urls(html)) == 2
