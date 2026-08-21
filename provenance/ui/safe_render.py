"""Inert rendering helpers.

Every value that originates from a user, a file, or a remote site is written through
these helpers, which use text-only Streamlit APIs. ``unsafe_allow_html`` is never used
anywhere in the application, and Markdown is never interpreted for untrusted values, so
retrieved content cannot become active markup, a link, or a remote image request.

Requirements: 2.6, 9.6, 9.8, 17.3, 17.4, 19.1, 19.4
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import streamlit as st

MAX_DISPLAY_LENGTH: Final = 2_000
TRUNCATION_NOTE: Final = " [truncated]"
EMPTY_PLACEHOLDER: Final = "(none recorded)"


def clamp(value: str, limit: int = MAX_DISPLAY_LENGTH) -> str:
    """Bound an untrusted string and mark it when shortened."""
    if len(value) <= limit:
        return value
    return value[:limit] + TRUNCATION_NOTE


def inert(value: str | None) -> str:
    """Normalize an untrusted value for text display.

    Control characters are replaced so a hostile value cannot disturb layout, and the
    result is length bounded.
    """
    if value is None or value == "":
        return EMPTY_PLACEHOLDER
    cleaned = "".join(
        character if character.isprintable() or character == " " else " " for character in value
    )
    return clamp(cleaned)


def text(value: str | None) -> None:
    """Write one untrusted value as literal text."""
    st.text(inert(value))


def labelled(label: str, value: str | None) -> None:
    """Write a labelled value as literal text, keeping the label visible."""
    st.text(f"{label}: {inert(value)}")


def caption(value: str) -> None:
    """Write application-authored guidance."""
    st.caption(value)


def evidence_block(value: str | None) -> None:
    """Show retrieved evidence verbatim without interpreting it."""
    st.code(inert(value), language=None)


def detail_rows(rows: Iterable[tuple[str, str]]) -> None:
    """Write labelled detail rows as literal text."""
    for label, value in rows:
        labelled(label, value)
