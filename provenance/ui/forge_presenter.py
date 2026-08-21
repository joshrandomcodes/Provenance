"""Pure view models for The Forge.

Rendering is kept separate from decision making so the visible outcome of a submission
can be tested without a browser or a Streamlit session.

Requirements: 5.6, 5.7, 17.6, 19.4, 19.8, 21.4
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from provenance.application.forge import ForgeOutcome
from provenance.domain.errors import Failure
from provenance.ui.messages import label_for, message_for

EVIDENCE_NOTE: Final = (
    "Registration records your own local evidence. It does not by itself prove "
    "ownership, and Provenance does not provide legal advice."
)


@dataclass(frozen=True, slots=True)
class FieldMessage:
    """One validation problem bound to a visible field label."""

    field_key: str
    label: str
    message: str


@dataclass(frozen=True, slots=True)
class ForgeFailureView:
    """What to show when a submission did not complete."""

    summary: str
    field_messages: tuple[FieldMessage, ...] = ()

    @property
    def has_field_detail(self) -> bool:
        """True when at least one field-specific message is present."""
        return len(self.field_messages) > 0


@dataclass(frozen=True, slots=True)
class ForgeSuccessView:
    """What to show when registration succeeded."""

    headline: str
    created: bool
    rows: tuple[tuple[str, str], ...]
    download_name: str
    payload_bytes: int
    capacity_bytes: int
    utilisation_percent: int
    utilisation_text: str
    note: str = EVIDENCE_NOTE


def build_failure_view(failure: Failure) -> ForgeFailureView:
    """Turn a failure into safe, actionable display text."""
    messages = tuple(
        FieldMessage(
            field_key=issue.field_key,
            label=label_for(issue.field_key),
            message=issue.message,
        )
        for issue in failure.fields
    )
    return ForgeFailureView(summary=message_for(failure), field_messages=messages)


def utilisation_percent(payload_bytes: int, capacity_bytes: int) -> int:
    """Share of the watermark capacity used, rounded down."""
    if capacity_bytes <= 0:
        return 0
    return min(100, payload_bytes * 100 // capacity_bytes)


def describe_utilisation(payload_bytes: int, capacity_bytes: int) -> str:
    """Readable capacity line. Large images use a tiny fraction, so avoid a bare 0%."""
    if capacity_bytes <= 0:
        return "0 of 0 bytes"
    percent = utilisation_percent(payload_bytes, capacity_bytes)
    share = f"{percent}%" if percent >= 1 else "under 1%"
    return f"{payload_bytes:,} of {capacity_bytes:,} bytes ({share})"


def build_success_view(outcome: ForgeOutcome) -> ForgeSuccessView:
    """Turn a registration outcome into labelled display rows."""
    asset = outcome.asset
    headline = (
        "Registered and watermarked." if outcome.created else "Already registered. Watermark ready."
    )
    rows: tuple[tuple[str, str], ...] = (
        ("Asset hash", asset.asset_hash),
        ("Creator ID", asset.creator_id),
        ("Registered at", asset.registered_at),
        ("Watermark created at", outcome.payload.created_at),
        ("Output size", f"{asset.width} x {asset.height} pixels"),
        ("Source type", asset.source_media_type.value),
        ("Watermark payload", f"{outcome.payload_bytes} bytes"),
        ("Watermark capacity", f"{outcome.capacity_bytes} bytes"),
        ("Record", "created" if outcome.created else "reused"),
    )
    return ForgeSuccessView(
        headline=headline,
        created=outcome.created,
        rows=rows,
        download_name=outcome.download_name,
        payload_bytes=outcome.payload_bytes,
        capacity_bytes=outcome.capacity_bytes,
        utilisation_percent=utilisation_percent(outcome.payload_bytes, outcome.capacity_bytes),
        utilisation_text=describe_utilisation(outcome.payload_bytes, outcome.capacity_bytes),
    )
