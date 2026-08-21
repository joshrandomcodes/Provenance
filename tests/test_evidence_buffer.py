"""Volatile evidence leases: single occupancy, stale-lease safety, and redaction.

Requirements: 9.4, 9.7, 17.3, 17.5, 17.6
"""

from __future__ import annotations

import gc
import weakref

import numpy as np
import pytest

from provenance.application.evidence import (
    EvidenceBuffer,
    EvidenceLease,
    RetainedEvidence,
)
from provenance.domain.models import MediaType

pytestmark = pytest.mark.unit

SECRET_BYTES = b"\x89PNG-secret-pixel-payload"


def _evidence(key: str = "incident-1", *, width: int = 4, height: int = 2) -> RetainedEvidence:
    return RetainedEvidence(
        incident_key=key,
        media_type=MediaType.PNG,
        width=width,
        height=height,
        image_bytes=SECRET_BYTES,
        rgb=np.zeros((height, width, 3), dtype=np.uint8),
    )


def test_a_new_buffer_is_empty() -> None:
    buffer = EvidenceBuffer()

    assert buffer.is_empty is True
    assert buffer.retained_bytes == 0
    assert buffer.retained_key is None


def test_selected_evidence_resolves_through_its_lease() -> None:
    buffer = EvidenceBuffer()
    evidence = _evidence()

    lease = buffer.select(evidence)

    assert buffer.evidence_for(lease) is evidence
    assert buffer.holds(lease) is True
    assert buffer.is_empty is False
    assert buffer.retained_key == "incident-1"
    assert buffer.retained_bytes == len(SECRET_BYTES)


def test_selecting_another_incident_releases_the_previous_one() -> None:
    buffer = EvidenceBuffer()
    first_lease = buffer.select(_evidence("incident-1"))

    second_lease = buffer.select(_evidence("incident-2"))

    assert buffer.evidence_for(first_lease) is None
    assert buffer.holds(first_lease) is False
    assert buffer.retained_key == "incident-2"
    assert buffer.holds(second_lease) is True


def test_at_most_one_incident_is_ever_retained() -> None:
    buffer = EvidenceBuffer()
    leases = [buffer.select(_evidence(f"incident-{index}")) for index in range(5)]

    resolved = [lease for lease in leases if buffer.holds(lease)]

    assert len(resolved) == 1
    assert resolved[0] == leases[-1]


def test_releasing_a_lease_clears_the_buffer() -> None:
    buffer = EvidenceBuffer()
    lease = buffer.select(_evidence())

    assert buffer.release(lease) is True
    assert buffer.is_empty is True
    assert buffer.evidence_for(lease) is None


def test_releasing_a_stale_lease_does_not_discard_current_evidence() -> None:
    """A late callback from a previous selection must not clear the current one."""
    buffer = EvidenceBuffer()
    stale = buffer.select(_evidence("incident-1"))
    current = buffer.select(_evidence("incident-2"))

    released = buffer.release(stale)

    assert released is False
    assert buffer.holds(current) is True
    assert buffer.retained_key == "incident-2"


def test_releasing_the_same_lease_twice_is_safe() -> None:
    buffer = EvidenceBuffer()
    lease = buffer.select(_evidence())

    assert buffer.release(lease) is True
    assert buffer.release(lease) is False


def test_release_all_is_idempotent() -> None:
    buffer = EvidenceBuffer()
    buffer.select(_evidence())

    buffer.release_all()
    buffer.release_all()

    assert buffer.is_empty is True


def test_release_all_on_an_empty_buffer_is_safe() -> None:
    buffer = EvidenceBuffer()

    buffer.release_all()

    assert buffer.is_empty is True


def test_a_lease_from_another_buffer_never_resolves() -> None:
    first = EvidenceBuffer()
    second = EvidenceBuffer()
    foreign_lease = first.select(_evidence("incident-1"))
    second.select(_evidence("incident-2"))

    assert second.evidence_for(foreign_lease) is None


def test_a_forged_lease_never_resolves() -> None:
    buffer = EvidenceBuffer()
    buffer.select(_evidence())

    assert buffer.evidence_for(EvidenceLease(token="0" * 32)) is None


def test_lease_tokens_are_unique_per_selection() -> None:
    buffer = EvidenceBuffer()

    tokens = {buffer.select(_evidence(f"incident-{index}")).token for index in range(20)}

    assert len(tokens) == 20


def test_lease_tokens_are_issued_unguessably() -> None:
    tokens = {EvidenceLease.issue().token for _ in range(50)}

    assert len(tokens) == 50
    assert all(len(token) == 32 for token in tokens)


def test_released_pixels_become_collectable() -> None:
    """The buffer must hold the only reference, so releasing frees the array."""
    buffer = EvidenceBuffer()
    array = np.zeros((2, 4, 3), dtype=np.uint8)
    tracker = weakref.ref(array)
    buffer.select(
        RetainedEvidence(
            incident_key="incident-1",
            media_type=MediaType.PNG,
            width=4,
            height=2,
            image_bytes=SECRET_BYTES,
            rgb=array,
        )
    )
    del array

    assert tracker() is not None

    buffer.release_all()
    gc.collect()

    assert tracker() is None


def test_replacing_a_selection_frees_the_previous_pixels() -> None:
    buffer = EvidenceBuffer()
    array = np.zeros((2, 4, 3), dtype=np.uint8)
    tracker = weakref.ref(array)
    buffer.select(
        RetainedEvidence(
            incident_key="incident-1",
            media_type=MediaType.PNG,
            width=4,
            height=2,
            image_bytes=SECRET_BYTES,
            rgb=array,
        )
    )
    del array

    buffer.select(_evidence("incident-2"))
    gc.collect()

    assert tracker() is None


def test_the_evidence_representation_redacts_bytes_and_pixels() -> None:
    rendered = repr(_evidence())

    assert "RetainedEvidence(" in rendered
    assert "incident-1" in rendered
    assert "width=4" in rendered
    assert f"bytes={len(SECRET_BYTES)}" in rendered
    assert "secret" not in rendered
    assert "PNG-secret" not in rendered
    assert "array" not in rendered


def test_the_buffer_representation_redacts_its_contents() -> None:
    buffer = EvidenceBuffer()
    buffer.select(_evidence())

    rendered = repr(buffer)

    assert "EvidenceBuffer(" in rendered
    assert "retained=True" in rendered
    assert "secret" not in rendered
    assert "incident-1" not in rendered


def test_an_empty_buffer_representation_reports_no_contents() -> None:
    assert repr(EvidenceBuffer()) == "EvidenceBuffer(retained=False, bytes=0)"


def test_an_evidence_lease_representation_carries_no_image_data() -> None:
    lease = EvidenceLease.issue()

    assert lease.token in repr(lease)
    assert "secret" not in repr(lease)


def test_evidence_reports_its_own_byte_count() -> None:
    assert _evidence().byte_count == len(SECRET_BYTES)
