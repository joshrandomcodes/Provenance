"""Streamlit entry point and composition root.

Run with ``python scripts/run_app.py`` or
``python -m streamlit run provenance/app.py``.

Only production adapters are wired here. Test fixtures, fake clocks, mock
resolvers, and synthetic evidence providers must never be importable from this
module.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 17.1, 17.2
"""

from __future__ import annotations

from provenance.settings import RuntimeSettings, load_runtime_settings
from provenance.ui.dashboard import render_dashboard


def build_runtime_settings() -> RuntimeSettings:
    """Resolve settings and create the local application directory."""
    settings = load_runtime_settings()
    settings.ensure_directories()
    return settings


def main() -> None:
    """Compose and render the local dashboard."""
    render_dashboard(build_runtime_settings())


if __name__ == "__main__":
    main()
