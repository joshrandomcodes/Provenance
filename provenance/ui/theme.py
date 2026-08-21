"""Attach the dashboard stylesheet.

The stylesheet is a static asset next to this module, and it is passed to Streamlit as
a ``Path`` rather than a string. That is the whole safety argument: no value computed at
runtime, and therefore no value from a user, a file, or a scanned page, can reach the
document. Streamlit wraps the file in a style element, sanitizes it with DOMPurify, and
ignores JavaScript unless a caller explicitly opts in, which this module never does.

Styling stays decoration. Every fact the interface states is stated in text through
``safe_render``, so if a future Streamlit release changes its DOM and the selectors stop
matching, the dashboard keeps working on the configured theme palette alone. The same is
true if the asset is missing: this module returns quietly rather than failing a render.

Requirements: 17.2, 17.3, 19.1, 19.4
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import streamlit as st

from provenance.ui import safe_render

STYLESHEET_PATH: Final = Path(__file__).with_name("theme.css")
INTRO_KEY_PREFIX: Final = "prov-intro"


def apply_theme() -> None:
    """Attach the local stylesheet, or do nothing if it is unavailable."""
    if not STYLESHEET_PATH.is_file():
        return
    st.html(STYLESHEET_PATH)


def render_intro(name: str, lines: Sequence[str]) -> None:
    """Render a tab's opening lines so the stylesheet can reveal them in sequence.

    Streamlit marks a container created with a key using a matching class, and that
    class is the only hook the stylesheet needs, which keeps the animation off every
    other caption on the page. The lines themselves are application-authored
    constants; no value from a user, a file, or a scanned page passes through here.

    Without the stylesheet, or with reduced motion requested, these are ordinary
    captions.
    """
    with st.container(key=f"{INTRO_KEY_PREFIX}-{name}"):
        for line in lines:
            safe_render.caption(line)
