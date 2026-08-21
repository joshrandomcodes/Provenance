"""The stylesheet boundary.

Styling is the one place where markup enters this application, so the limits are
asserted rather than described. The stylesheet must be a static local asset, it must
fetch nothing, and no other module may introduce HTML or JavaScript.

Requirements: 17.2, 17.3, 19.1, 19.4
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Final

import pytest

from provenance.ui import theme

pytestmark = pytest.mark.unit

PACKAGE_ROOT: Final = Path(theme.__file__).resolve().parents[1]

# Anything that would make the browser reach out for a resource.
REMOTE_MARKERS: Final = ("@import", "url(", "http://", "https://", "//fonts.")


def _sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


_COMMENT: Final = re.compile(r"/\*.*?\*/", re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _declarations() -> str:
    """The stylesheet with comments removed.

    Only declarations can make a browser do anything, and the comments in this
    stylesheet discuss the very markers being searched for, so scanning the raw file
    would test the prose rather than the rules.
    """
    return _COMMENT.sub(" ", _read(theme.STYLESHEET_PATH)).lower()


def test_the_stylesheet_ships_with_the_package() -> None:
    assert theme.STYLESHEET_PATH.is_file()
    assert theme.STYLESHEET_PATH.name == "theme.css"
    assert theme.STYLESHEET_PATH.resolve().parent == PACKAGE_ROOT / "ui"


def test_the_stylesheet_fetches_nothing() -> None:
    declarations = _declarations()

    assert "{" in declarations, "comment stripping removed the whole stylesheet"
    for marker in REMOTE_MARKERS:
        assert marker not in declarations, f"stylesheet must not reference {marker}"


def test_the_stylesheet_declares_only_styles() -> None:
    declarations = _declarations()

    # A .css asset is wrapped in a style element by Streamlit, so any element or
    # handler syntax here would mean the file is not what it claims to be.
    assert "<" not in declarations
    assert "javascript:" not in declarations
    assert "expression(" not in declarations


def test_the_stylesheet_honours_reduced_motion() -> None:
    assert "prefers-reduced-motion" in _read(theme.STYLESHEET_PATH)


def test_applying_the_theme_accepts_no_caller_input() -> None:
    # No parameters means no interpolation, which is what makes the asset constant.
    assert len(inspect.signature(theme.apply_theme).parameters) == 0


def test_the_theme_passes_a_path_never_a_string() -> None:
    source = _read(Path(theme.__file__))

    assert "st.html(STYLESHEET_PATH)" in source
    assert isinstance(theme.STYLESHEET_PATH, Path)


def test_a_missing_stylesheet_does_not_break_a_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(theme, "STYLESHEET_PATH", tmp_path / "absent.css")

    # No Streamlit context is active, so reaching st.html at all would raise.
    theme.apply_theme()


def test_no_module_enables_unsafe_html_or_javascript() -> None:
    for path in _sources():
        source = _read(path)
        assert "unsafe_allow_html=True" not in source, f"{path.name} enables unsafe HTML"
        assert "unsafe_allow_javascript" not in source, f"{path.name} touches JavaScript"


def test_only_the_theme_module_inserts_html() -> None:
    inserting = [path.name for path in _sources() if "st.html(" in _read(path)]

    assert inserting == ["theme.py"]


def test_the_render_helpers_remain_text_only() -> None:
    source = _read(PACKAGE_ROOT / "ui" / "safe_render.py")

    for banned in ("st.markdown", "st.html", "st.write"):
        assert banned not in source


_KEYFRAMES_BLOCK: Final = re.compile(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", re.DOTALL)
_ANIMATION_NAME: Final = re.compile(r"animation:\s*([a-z-]+)")
_KEYFRAMES_NAME: Final = re.compile(r"@keyframes\s+([a-z-]+)")


def test_every_animation_refers_to_a_defined_keyframes() -> None:
    declarations = _declarations()
    defined = set(_KEYFRAMES_NAME.findall(declarations))
    used = {name for name in _ANIMATION_NAME.findall(declarations) if name != "none"}

    assert used <= defined, f"undefined animations: {sorted(used - defined)}"


def test_the_hidden_state_of_a_reveal_lives_only_in_keyframes() -> None:
    """A clip in a rule rather than a keyframe would hide text permanently.

    The reveal animations start from a clipped state supplied by backwards fill. If
    that state were written into the rule instead, switching animation off, which is
    exactly what reduced motion does, would leave the text clipped away forever.
    """
    outside_keyframes = _KEYFRAMES_BLOCK.sub(" ", _declarations())

    assert "clip-path" not in outside_keyframes
