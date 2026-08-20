"""Dashboard shell that renders the three named Provenance tabs.

Tab contents are filled in by later tasks; this module establishes the named tab
structure, inert text rendering, and the local-only status panel.

Requirements: 1.1, 1.3, 1.4, 2.6, 17.12, 19.1
"""

from __future__ import annotations

from typing import Final

import streamlit as st

from provenance.settings import APPLICATION_VERSION, RuntimeSettings

FORGE_TAB_LABEL: Final = "The Forge"
RADAR_TAB_LABEL: Final = "Web Radar"
TRIAGE_TAB_LABEL: Final = "Incident Triage"
TAB_LABELS: Final = (FORGE_TAB_LABEL, RADAR_TAB_LABEL, TRIAGE_TAB_LABEL)

_PENDING_NOTICE: Final = (
    "This workflow is not implemented yet. The project scaffolding is in place and "
    "the feature arrives in a later implementation task."
)


def render_dashboard(settings: RuntimeSettings) -> None:
    """Render the dashboard shell for the given runtime settings."""
    st.set_page_config(page_title="Provenance", layout="wide")
    st.title("Provenance")
    st.text(
        "Local-first copyright protection workspace. Provenance assists evidence "
        "collection and notice preparation. It does not provide legal advice and does "
        "not determine ownership, infringement, or fair use."
    )

    forge_tab, radar_tab, triage_tab = st.tabs(list(TAB_LABELS))

    with forge_tab:
        st.header(FORGE_TAB_LABEL)
        st.text("Register an image, embed its watermark, and download the protected copy.")
        st.text(_PENDING_NOTICE)

    with radar_tab:
        st.header(RADAR_TAB_LABEL)
        st.text("Run a bounded, user-initiated scan of one public page for registered marks.")
        st.text(_PENDING_NOTICE)

    with triage_tab:
        st.header(TRIAGE_TAB_LABEL)
        st.text("Review evidence and record fair-use, credit, or strike decisions.")
        st.text(_PENDING_NOTICE)

    _render_local_status(settings)


def _render_local_status(settings: RuntimeSettings) -> None:
    """Show local-only runtime facts as inert text."""
    with st.sidebar:
        st.header("Local status")
        st.text(f"Version: {APPLICATION_VERSION}")
        st.text(f"Registry file: {settings.registry_path}")
        st.text(f"Outbound user agent: {settings.user_agent}")
        st.text("Telemetry, analytics, cloud storage, and remote logging: disabled")
        st.text(
            "Local diagnostic log: "
            f"{'enabled' if settings.local_diagnostic_log_enabled else 'disabled'}"
        )
