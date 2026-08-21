"""Dashboard shell that renders the three named Provenance tabs.

Requirements: 1.1, 1.3, 1.4, 2.6, 17.12, 19.1, 21.4, 21.5
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import streamlit as st

from provenance.application.forge import ForgeService
from provenance.infrastructure.sqlite.connection import RegistryStatus
from provenance.settings import APPLICATION_VERSION, RuntimeSettings
from provenance.ui import safe_render
from provenance.ui.forge_view import render_forge_tab

FORGE_TAB_LABEL: Final = "The Forge"
RADAR_TAB_LABEL: Final = "Web Radar"
TRIAGE_TAB_LABEL: Final = "Incident Triage"
TAB_LABELS: Final = (FORGE_TAB_LABEL, RADAR_TAB_LABEL, TRIAGE_TAB_LABEL)

_PENDING_NOTICE: Final = (
    "This workflow is not implemented yet. The feature arrives in a later implementation task."
)

_SCOPE_NOTICE: Final = (
    "Provenance assists evidence collection and notice preparation. It does not provide "
    "legal advice and does not determine ownership, infringement, or fair use."
)


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Everything the dashboard needs to render."""

    settings: RuntimeSettings
    status: RegistryStatus
    forge: ForgeService


def render_dashboard(dashboard: Dashboard) -> None:
    """Render the dashboard shell and its tabs."""
    st.set_page_config(page_title="Provenance", layout="wide")
    st.title("Provenance")
    safe_render.caption(_SCOPE_NOTICE)

    forge_tab, radar_tab, triage_tab = st.tabs(list(TAB_LABELS))

    with forge_tab:
        render_forge_tab(dashboard.forge, writes_enabled=dashboard.status.writable)

    with radar_tab:
        st.header(RADAR_TAB_LABEL)
        safe_render.caption(
            "Run a bounded, user-initiated scan of one public page for your registered marks."
        )
        st.text(_PENDING_NOTICE)

    with triage_tab:
        st.header(TRIAGE_TAB_LABEL)
        safe_render.caption(
            "Review evidence and record fair-use, credit, or strike decisions yourself."
        )
        st.text(_PENDING_NOTICE)

    _render_local_status(dashboard)


def _render_local_status(dashboard: Dashboard) -> None:
    """Show local-only runtime facts as inert text."""
    settings = dashboard.settings
    status = dashboard.status

    with st.sidebar:
        st.header("Local status")
        safe_render.labelled("Version", APPLICATION_VERSION)
        safe_render.labelled("Registry file", str(status.path))
        safe_render.labelled("Registry writes", "enabled" if status.writable else "disabled")
        safe_render.labelled("Schema version", str(status.schema_version))
        safe_render.labelled("Outbound user agent", settings.user_agent)
        st.text("Telemetry, analytics, cloud storage, remote logging: disabled")
        safe_render.labelled(
            "Local diagnostic log",
            "enabled" if settings.local_diagnostic_log_enabled else "disabled",
        )

        if status.guidance is not None:
            st.warning(status.guidance)
            if status.integrity_errors:
                st.text(f"Integrity findings: {len(status.integrity_errors)}")
