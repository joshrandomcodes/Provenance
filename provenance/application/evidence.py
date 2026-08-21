"""Volatile ownership of image evidence.

Registered assets are stored as hashes, never as pixels, and scraped images are never
stored at all. So when the UI shows a scraped image, those bytes exist in exactly one
place: this buffer, in process memory, for as long as that incident is the single current
triage selection.

The lifecycle is deliberately unforgiving. Selecting a different incident, completing or
cancelling a scan, resetting the Forge, and tearing down a session all release what was
held. A lease is a one-time opaque token: once its evidence is released, the same token
never resolves again, so a stale reference cannot resurrect dropped bytes.

Nothing here writes to disk, a temporary file, a Streamlit cache, or a log. Both public
types carry redacted representations, because an exception rendering its arguments is a
realistic way for pixel data to reach a diagnostic record.

Requirements: 9.4, 9.7, 17.3, 17.5, 17.6
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

from provenance.domain.models import MediaType, RgbArray

LEASE_TOKEN_BYTES: Final = 16


@dataclass(frozen=True, slots=True)
class EvidenceLease:
    """An opaque handle to retained evidence. Carries no image data itself."""

    token: str

    @staticmethod
    def issue() -> EvidenceLease:
        """Mint an unguessable, unique lease token."""
        return EvidenceLease(token=secrets.token_hex(LEASE_TOKEN_BYTES))


@dataclass(frozen=True, slots=True, repr=False)
class RetainedEvidence:
    """One incident's image bytes and decoded pixels, held only while selected."""

    incident_key: str
    media_type: MediaType
    width: int
    height: int
    image_bytes: bytes
    rgb: RgbArray

    @property
    def byte_count(self) -> int:
        """Encoded bytes currently held for this incident."""
        return len(self.image_bytes)

    def __repr__(self) -> str:
        """Redacted representation: shape and size only, never pixels or bytes."""
        return (
            f"RetainedEvidence(incident_key={self.incident_key!r}, "
            f"media_type={self.media_type.value}, "
            f"width={self.width}, height={self.height}, bytes={self.byte_count})"
        )


class EvidenceBuffer:
    """Holds at most one incident's image evidence in volatile memory.

    Not thread-safe by design: it is owned by the session that reads it, and the scan
    worker hands over analyzed images rather than writing here concurrently.
    """

    __slots__ = ("_lease", "_evidence")

    def __init__(self) -> None:
        self._lease: EvidenceLease | None = None
        self._evidence: RetainedEvidence | None = None

    @property
    def is_empty(self) -> bool:
        """True when nothing is retained."""
        return self._evidence is None

    @property
    def retained_bytes(self) -> int:
        """Encoded bytes currently held, across the at most one retained item."""
        return 0 if self._evidence is None else self._evidence.byte_count

    @property
    def retained_key(self) -> str | None:
        """The incident key currently retained, if any."""
        return None if self._evidence is None else self._evidence.incident_key

    def select(self, evidence: RetainedEvidence) -> EvidenceLease:
        """Retain one incident's evidence, releasing whatever was held before.

        This is the selection-change path: the previous selection's bytes and pixels are
        dropped in the same call that admits the new one, so two images are never held.
        """
        self.release_all()
        lease = EvidenceLease.issue()
        self._lease = lease
        self._evidence = evidence
        return lease

    def evidence_for(self, lease: EvidenceLease) -> RetainedEvidence | None:
        """Resolve a lease, or None when it is stale, released, or from another buffer."""
        if self._lease is None or self._evidence is None:
            return None
        if lease.token != self._lease.token:
            return None
        return self._evidence

    def holds(self, lease: EvidenceLease) -> bool:
        """True when this lease still resolves to retained evidence."""
        return self.evidence_for(lease) is not None

    def release(self, lease: EvidenceLease) -> bool:
        """Release the evidence behind one lease. Returns whether anything was held.

        A stale lease releases nothing, which keeps a late callback from discarding the
        evidence for a selection the user has since made.
        """
        if not self.holds(lease):
            return False
        self.release_all()
        return True

    def release_all(self) -> None:
        """Drop every reference so the bytes and arrays become collectable.

        Called on scan completion, scan cancellation, selection change, Forge reset, and
        session teardown. Safe to call when already empty.
        """
        self._lease = None
        self._evidence = None

    def __repr__(self) -> str:
        """Redacted representation: occupancy only."""
        return f"EvidenceBuffer(retained={not self.is_empty}, bytes={self.retained_bytes})"
