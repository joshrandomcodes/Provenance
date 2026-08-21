"""Pure text extraction helpers for static page evidence.

These functions operate on strings that a parser has already produced, so they carry no
dependency on Beautiful Soup and are cheap to test exhaustively. Three jobs live here:

* deterministic whitespace and control-character normalization, so the same document
  always yields the same context strings and nothing unprintable reaches the UI;
* a ``srcset`` tokenizer following the HTML specification's candidate-string algorithm,
  because a naive comma split corrupts URLs that legitimately contain commas;
* ecommerce indicator matching, which reports the exact matching text as evidence and
  draws no conclusion about infringement or fair use.

Every returned value is length-bounded and count-bounded before it can reach a display
surface, and matches are ordered by position so output is stable.

Requirements: 9.1, 9.3, 9.6, 21.4, 21.5
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Final

MAX_CONTEXT_LENGTH: Final = 500
MAX_SURROUNDING_LENGTH: Final = 1_000
MAX_EVIDENCE_LENGTH: Final = 200
MAX_EVIDENCE_ITEMS: Final = 10

# Code points of context kept on each side of a match, so a bare word like "price"
# reaches the user as readable evidence rather than an unmoored fragment.
SNIPPET_RADIUS: Final = 40

_WHITESPACE_RUN: Final = re.compile(r"\s+")

# Symbols that denote a displayed amount. Not exhaustive by design: an unlisted symbol
# simply produces no indicator, which is a missing hint rather than a false one.
CURRENCY_SYMBOLS: Final = (
    "$\u20ac\u00a3\u00a5\u20b9\u20bd\u00a2\u20a9\u20aa\u20b4"
    "\u20a6\u20b1\u0e3f\u20ba\u20ab\u20a1\u20b2\u20b5\u20b8\u20bc"
)

_SYMBOL_CLASS: Final = f"[{re.escape(CURRENCY_SYMBOLS)}]"
_AMOUNT: Final = re.compile(
    rf"{_SYMBOL_CLASS}\s?\d+(?:[.,\u00a0\u202f ]\d+)*|\d+(?:[.,]\d+)*\s?{_SYMBOL_CLASS}"
)

# ISO 4217 codes match only as a standalone uppercase token, which keeps ordinary
# three-letter words such as "ART" or "OIL" from being read as currency. Matching is
# deliberately case-sensitive for the same reason.
CURRENCY_CODE_PATTERN: Final = (
    r"\b(?:AED|ARS|AUD|BDT|BRL|CAD|CHF|CLP|CNY|COP|CZK|DKK|EGP|EUR|GBP|HKD|HUF|IDR"
    r"|ILS|INR|JPY|KES|KRW|LKR|MXN|MYR|NGN|NOK|NZD|PEN|PHP|PKR|PLN|QAR|RON|RUB|SAR"
    r"|SEK|SGD|THB|TRY|TWD|UAH|USD|VND|ZAR)\b"
)
_CURRENCY_CODE: Final = re.compile(CURRENCY_CODE_PATTERN)

PHRASE_INDICATORS: Final = ("add to cart", "buy now", "price")
_PHRASE_PATTERNS: Final = tuple(
    re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in PHRASE_INDICATORS
)

# Schema.org commerce typing, either as microdata/RDFa attribute values or JSON-LD.
_SCHEMA_MARKUP: Final = re.compile(r"schema\.org/(?:product|offer)\b", re.IGNORECASE)
_SCHEMA_JSON_LD: Final = re.compile(
    r"""["']?@type["']?\s*:\s*["'](?:product|offer)["']""", re.IGNORECASE
)

_MARKUP_PATTERNS: Final = (_AMOUNT, _CURRENCY_CODE, _SCHEMA_MARKUP, _SCHEMA_JSON_LD)


def normalize_text(raw: str | None, *, limit: int = MAX_CONTEXT_LENGTH) -> str | None:
    """Collapse whitespace, drop non-printable characters, and bound the length.

    Returns ``None`` for absent or effectively empty text so callers can distinguish
    "no context" from "empty string", which the Registry stores differently.
    """
    if raw is None:
        return None

    # Whitespace is kept so runs can collapse; every other control, format, surrogate,
    # private-use, and unassigned code point is removed rather than rendered.
    printable = "".join(
        character
        for character in raw
        if character.isspace() or unicodedata.category(character)[0] != "C"
    )
    collapsed = _WHITESPACE_RUN.sub(" ", printable).strip()
    if collapsed == "":
        return None
    return collapsed[:limit]


def parse_srcset(value: str) -> tuple[str, ...]:
    """Tokenize a ``srcset`` attribute into its URLs, left to right.

    Follows the HTML specification: a candidate is a run of non-whitespace characters,
    optionally followed by descriptors. Only *trailing* commas delimit a candidate, so a
    comma inside a URL is preserved. Descriptors are skipped with parenthesis awareness.
    """
    urls: list[str] = []
    position = 0
    length = len(value)

    while position < length:
        while position < length and (value[position].isspace() or value[position] == ","):
            position += 1
        if position >= length:
            break

        start = position
        while position < length and not value[position].isspace():
            position += 1
        token = value[start:position]

        if token.endswith(","):
            token = token.rstrip(",")
        else:
            position = _skip_descriptors(value, position)

        if token != "":
            urls.append(token)

    return tuple(urls)


def _skip_descriptors(value: str, position: int) -> int:
    """Advance past width and density descriptors to the next top-level comma."""
    depth = 0
    length = len(value)
    while position < length:
        character = value[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            return position
        position += 1
    return position


def find_ecommerce_evidence(fields: Sequence[str | None]) -> tuple[str, ...]:
    """Report bounded snippets of any commerce indicator found in the supplied text.

    Fields are scanned in the order given, and matches within a field in position order,
    so output is deterministic. Identical snippets collapse to one entry, which is what
    keeps an amount and its neighbouring "Price" label from being reported twice.

    This is evidence collection only. A match means the page displays commerce language
    near the image, not that the use is infringing.
    """
    found: list[str] = []
    seen: set[str] = set()

    for field in fields:
        if field is None or field == "":
            continue
        for start, end in _indicator_spans(field):
            snippet = _snippet(field, start, end)
            if snippet == "":
                continue
            key = snippet.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(snippet)
            if len(found) >= MAX_EVIDENCE_ITEMS:
                return tuple(found)

    return tuple(found)


def _indicator_spans(text: str) -> list[tuple[int, int]]:
    """Every indicator match in one field, ordered by position then length."""
    spans: list[tuple[int, int]] = []

    for pattern in (*_MARKUP_PATTERNS, *_PHRASE_PATTERNS):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))

    spans.sort()
    return spans


def _snippet(text: str, start: int, end: int) -> str:
    """A bounded window around one match, suitable for inert literal display."""
    left = max(0, start - SNIPPET_RADIUS)
    right = min(len(text), end + SNIPPET_RADIUS)
    return text[left:right].strip()[:MAX_EVIDENCE_LENGTH]


def bounded_values(values: Iterable[str], *, limit: int = MAX_EVIDENCE_LENGTH) -> tuple[str, ...]:
    """Trim and bound a sequence of attribute values, dropping empties."""
    kept: list[str] = []
    for value in values:
        trimmed = value.strip()
        if trimmed != "":
            kept.append(trimmed[:limit])
    return tuple(kept)
