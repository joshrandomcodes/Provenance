"""The Forge tab.

Collects an image and creator metadata, runs the Forge workflow, and offers the
watermarked download only after registration has committed.

Requirements: 1.1, 2.1-2.6, 5.1, 5.6, 5.7, 19.1, 19.3, 19.8, 21.6
"""

from __future__ import annotations

from typing import Final

import streamlit as st

from provenance.application.forge import ForgeService
from provenance.domain.models import CreatorId, CreatorMetadata
from provenance.ui import safe_render
from provenance.ui.forge_presenter import (
    ForgeFailureView,
    ForgeSuccessView,
    build_failure_view,
    build_success_view,
)
from provenance.ui.theme import render_intro

FORM_KEY: Final = "forge_form"
RESULT_KEY: Final = "forge_result"

UPLOAD_LABEL: Final = "Image file (PNG or JPEG)"
CREATOR_ID_LABEL: Final = "Creator ID"
DISPLAY_NAME_LABEL: Final = "Display name"
CONTACT_EMAIL_LABEL: Final = "Contact email (optional)"
POSTAL_LABEL: Final = "Postal address (optional)"
RIGHTS_LABEL: Final = "Rights statement (optional)"
SUBMIT_LABEL: Final = "Watermark and register"

INTRO_LINES: Final = (
    "Embed a cryptographic watermark in your own image.",
    "The registration is recorded in your local registry.",
    "The image never leaves this computer.",
)


def render_forge_tab(service: ForgeService, *, writes_enabled: bool) -> None:
    """Render the Forge tab and handle one submission per run."""
    st.header("The Forge")
    render_intro("forge", INTRO_LINES)

    if not writes_enabled:
        st.error(
            "Saving is disabled because the local registry did not pass its startup checks. "
            "See the local status panel for recovery guidance."
        )

    with st.form(FORM_KEY, clear_on_submit=False):
        upload = st.file_uploader(UPLOAD_LABEL, type=["png", "jpg", "jpeg"])
        creator_id = st.text_input(CREATOR_ID_LABEL, max_chars=64, help="Letters, digits, . _ -")
        display_name = st.text_input(DISPLAY_NAME_LABEL, max_chars=200)
        contact_email = st.text_input(CONTACT_EMAIL_LABEL, max_chars=254)
        postal_address = st.text_area(POSTAL_LABEL, max_chars=500)
        rights_statement = st.text_area(RIGHTS_LABEL, max_chars=500)
        submitted = st.form_submit_button(SUBMIT_LABEL, disabled=not writes_enabled)

    if submitted:
        st.session_state[RESULT_KEY] = _submit(
            service,
            upload_bytes=upload.getvalue() if upload is not None else b"",
            file_name=upload.name if upload is not None else "",
            metadata=CreatorMetadata(
                creator_id=CreatorId(creator_id.strip()),
                display_name=display_name.strip(),
                contact_email=contact_email.strip() or None,
                postal_address=postal_address.strip() or None,
                rights_statement=rights_statement.strip() or None,
            ),
        )

    _render_result()


def _submit(
    service: ForgeService, *, upload_bytes: bytes, file_name: str, metadata: CreatorMetadata
) -> ForgeSuccessView | ForgeFailureView:
    """Run the workflow and return the view model for its outcome."""
    result = service.forge(upload_bytes, file_name, metadata)
    if result.failure is not None:
        return build_failure_view(result.unwrap_failure())

    outcome = result.unwrap()
    st.session_state[_download_key()] = (outcome.png_bytes, outcome.download_name)
    return build_success_view(outcome)


def _render_result() -> None:
    """Render the stored outcome of the most recent submission."""
    view = st.session_state.get(RESULT_KEY)
    if view is None:
        return

    if isinstance(view, ForgeFailureView):
        st.subheader("Not registered")
        safe_render.text(view.summary)
        if view.has_field_detail:
            st.text("Fix these fields:")
            for message in view.field_messages:
                safe_render.labelled(message.label, message.message)
        return

    st.subheader(view.headline)
    safe_render.detail_rows(view.rows)
    safe_render.labelled("Capacity used", view.utilisation_text)
    safe_render.caption(view.note)

    stored = st.session_state.get(_download_key())
    if stored is None:
        return
    payload, name = stored
    st.download_button(
        label=f"Download {name}",
        data=payload,
        file_name=name,
        mime="image/png",
    )


def _download_key() -> str:
    return f"{RESULT_KEY}_download"
