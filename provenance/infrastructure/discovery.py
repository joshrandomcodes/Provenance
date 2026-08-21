"""Bounded static HTML image discovery and context extraction.

Only the already-bounded final page body is parsed, and only as static markup. No script
runs, no subresource is fetched by the parser, and no network access happens here: this
module turns bytes into a list of validated URLs plus the evidence around each one.

Candidate order is fixed by the requirements and is observable: for every ``img`` in
document order, ``src`` first, then each ``srcset`` entry left to right, then
``data-src``. Each candidate is resolved against the final page URL, revalidated by the
full URL policy, and deduplicated by normalized form. The unique-image cap and the
discovered/capped counts are owned by the scan budget, so discovery and accounting can
never disagree.

Context is deliberately narrow. Only the document title, nearest preceding heading,
enclosing figcaption, and the element's own alt text are captured, plus bounded commerce
indicators. Every string is normalized and length-bounded here so the UI layer only ever
receives inert, displayable text.

Requirements: 9.1, 9.2, 9.3, 9.6, 18.4, 21.4, 21.5
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from bs4 import BeautifulSoup, Tag

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.html_evidence import (
    MAX_SURROUNDING_LENGTH,
    bounded_values,
    find_ecommerce_evidence,
    normalize_text,
    parse_srcset,
)
from provenance.domain.models import NormalizedUrl, PageContext
from provenance.domain.scan_budget import DiscoveryDecision, ScanBudget
from provenance.domain.urls import ALLOWED_PORTS, AbsoluteHttpUrl, resolve_candidate

DISCOVERY_OPERATION: Final = "discover_images"

# Only the permissive built-in parser is used. lxml and html5lib are not dependencies,
# and naming the parser explicitly keeps tokenization identical across environments.
HTML_PARSER: Final = "html.parser"

HEADING_NAMES: Final = ["h1", "h2", "h3", "h4", "h5", "h6"]
CANDIDATE_ATTRIBUTES: Final = ("src", "srcset", "data-src")

# Ancestor walks are bounded so a deeply nested or hostile document cannot turn context
# extraction into quadratic work.
MAX_ANCESTOR_DEPTH: Final = 8

# Attributes that carry Schema.org typing for microdata and RDFa.
SCHEMA_ATTRIBUTES: Final = ("itemtype", "typeof")


@dataclass(frozen=True, slots=True)
class ImageCandidate:
    """One retained image URL and the context of the element it came from."""

    image_url: AbsoluteHttpUrl
    context: PageContext

    @property
    def normalized(self) -> NormalizedUrl:
        """The canonical form used for deduplication, storage, and comparison."""
        return self.image_url.normalized


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Retained candidates in first-occurrence order, plus what discovery dropped."""

    candidates: tuple[ImageCandidate, ...]
    title: str | None
    considered: int
    rejected: int
    duplicates: int
    capped: int

    @property
    def retained(self) -> int:
        """Unique candidates retained for scheduling."""
        return len(self.candidates)


def discover_images(
    html: bytes,
    page_url: AbsoluteHttpUrl,
    budget: ScanBudget,
    *,
    allowed_ports: frozenset[int] = ALLOWED_PORTS,
) -> Result[DiscoveryResult]:
    """Parse bounded static HTML into validated, deduplicated image candidates.

    The budget performs deduplication and enforces the unique-image cap, so the returned
    candidates are exactly the URLs the scheduler may attempt. Scanning continues past
    the cap only to count what was dropped; capped URLs are never retained and never
    counted as discovered.
    """
    if len(html) > budget.limits.html_bytes:
        return failed(FailureCode.HTML_LIMIT, DISCOVERY_OPERATION)

    try:
        document = BeautifulSoup(html, HTML_PARSER)
    except (ValueError, UnicodeDecodeError, AssertionError):
        return failed(FailureCode.DECODE_FAILURE, DISCOVERY_OPERATION)

    title = _document_title(document)
    candidates: list[ImageCandidate] = []
    considered = 0
    rejected = 0
    duplicates = 0
    capped = 0

    for element in document.find_all("img"):
        if not isinstance(element, Tag):
            continue

        # Context is derived once per element and shared by every URL that element
        # yields, which is exactly the association the requirements specify.
        context = context_for(element, title=title)

        for raw_candidate in candidate_values(element):
            considered += 1
            resolved = resolve_candidate(
                page_url.normalized, raw_candidate, allowed_ports=allowed_ports
            )
            if resolved.failure is not None:
                rejected += 1
                continue

            image_url = resolved.unwrap()
            match budget.discover(image_url.normalized):
                case DiscoveryDecision.RETAINED:
                    candidates.append(ImageCandidate(image_url=image_url, context=context))
                case DiscoveryDecision.DUPLICATE:
                    duplicates += 1
                case DiscoveryDecision.CAPPED:
                    capped += 1

    return ok(
        DiscoveryResult(
            candidates=tuple(candidates),
            title=title,
            considered=considered,
            rejected=rejected,
            duplicates=duplicates,
            capped=capped,
        )
    )


def candidate_values(element: Tag) -> Iterator[str]:
    """Yield one element's raw candidates in the exact order the requirements fix."""
    source = _attribute(element, "src")
    if source is not None:
        yield source

    srcset = _attribute(element, "srcset")
    if srcset is not None:
        yield from parse_srcset(srcset)

    data_source = _attribute(element, "data-src")
    if data_source is not None:
        yield data_source


def context_for(element: Tag, *, title: str | None) -> PageContext:
    """Collect exactly the specified page context for one image element."""
    heading = _preceding_heading(element)
    figcaption = _enclosing_figcaption(element)
    alt = normalize_text(_raw_attribute(element, "alt"))
    surrounding = _surrounding_text(element)
    markup = _schema_markup(element)

    evidence = find_ecommerce_evidence((title, heading, figcaption, alt, surrounding, *markup))
    return PageContext(
        title=title,
        heading=heading,
        figcaption=figcaption,
        alt=alt,
        ecommerce_evidence=evidence,
    )


def _document_title(document: BeautifulSoup) -> str | None:
    title = document.title
    if not isinstance(title, Tag):
        return None
    return normalize_text(title.get_text(" "))


def _raw_attribute(element: Tag, name: str) -> str | None:
    """One attribute value, or None when absent or multi-valued."""
    raw = element.get(name)
    return raw if isinstance(raw, str) else None


def _attribute(element: Tag, name: str) -> str | None:
    """One attribute value, trimmed, treating an empty value as absent."""
    raw = _raw_attribute(element, name)
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed if trimmed != "" else None


def _ancestors(element: Tag) -> Iterator[Tag]:
    """The element's ancestors, nearest first, to a bounded depth."""
    parent = element.parent
    depth = 0
    while isinstance(parent, Tag) and depth < MAX_ANCESTOR_DEPTH:
        yield parent
        parent = parent.parent
        depth += 1


def _preceding_heading(element: Tag) -> str | None:
    """Text of the nearest `h1`..`h6` preceding the element in document order."""
    heading = element.find_previous(HEADING_NAMES)
    if not isinstance(heading, Tag):
        return None
    return normalize_text(heading.get_text(" "))


def _enclosing_figcaption(element: Tag) -> str | None:
    """Text of the figcaption that encloses the image, directly or via its figure.

    An `img` is normally a sibling of the `figcaption` inside a shared `figure`, so the
    figure is consulted as well. The search stops at the first figure encountered, since
    a caption from an outer figure does not describe this image.
    """
    for ancestor in _ancestors(element):
        if ancestor.name == "figcaption":
            return normalize_text(ancestor.get_text(" "))
        if ancestor.name == "figure":
            caption = ancestor.find("figcaption")
            if isinstance(caption, Tag):
                return normalize_text(caption.get_text(" "))
            return None
    return None


def _surrounding_text(element: Tag) -> str | None:
    """Bounded text of the element that contains the image.

    Requirement 9.6 scopes commerce indicators to the page context or the containing
    element, so exactly one level of containment is read.
    """
    parent = element.parent
    if not isinstance(parent, Tag):
        return None
    return normalize_text(parent.get_text(" "), limit=MAX_SURROUNDING_LENGTH)


def _schema_markup(element: Tag) -> tuple[str, ...]:
    """Schema.org typing attributes on the image and its bounded ancestors."""
    values: list[str] = []
    for tag in (element, *_ancestors(element)):
        for name in SCHEMA_ATTRIBUTES:
            raw = _raw_attribute(tag, name)
            if raw is not None:
                values.append(raw)
    return bounded_values(values)
