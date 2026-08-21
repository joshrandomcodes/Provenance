"""Forge view models: safe messages, labelled rows, and capacity reporting.

Requirements: 5.6, 5.7, 17.6, 19.4, 19.8, 21.4
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import numpy as np
import pytest
from PIL import Image

from provenance.application.forge import ForgeService
from provenance.domain.errors import Failure, FailureCode, FieldIssue
from provenance.domain.models import CreatorId, CreatorMetadata
from provenance.domain.validation import FIELD_CONTACT_EMAIL, FIELD_CREATOR_ID, FIELD_FILE
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.png_codec import PillowPngEncoder
from provenance.ui.forge_presenter import (
    EVIDENCE_NOTE,
    ForgeFailureView,
    ForgeSuccessView,
    build_failure_view,
    build_success_view,
    describe_utilisation,
    utilisation_percent,
)
from provenance.ui.messages import GENERIC_MESSAGE, USER_MESSAGES, label_for, message_for
from tests.registry_support import RegistryHarness, temporary_registry

pytestmark = pytest.mark.unit

CREATOR = CreatorId("studio.one")


class FixedClock:
    """Deterministic clock."""

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, 3, 42, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _png(width: int = 600, height: int = 1) -> bytes:
    generator = np.random.default_rng(seed=3)
    rgb = generator.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _service(harness: RegistryHarness) -> ForgeService:
    return ForgeService(
        decoder=PillowImageDecoder(),
        encoder=PillowPngEncoder(),
        registry=harness.adapter,
        clock=FixedClock(),
    )


def _metadata() -> CreatorMetadata:
    return CreatorMetadata(creator_id=CREATOR, display_name="Studio One")


def test_success_view_lists_the_registration_facts() -> None:
    with temporary_registry() as harness:
        outcome = _service(harness).forge(_png(), "artwork.png", _metadata()).unwrap()

        view = build_success_view(outcome)

        assert isinstance(view, ForgeSuccessView)
        assert view.created is True
        assert view.headline == "Registered and watermarked."
        labels = [label for label, _value in view.rows]
        assert labels == [
            "Asset hash",
            "Creator ID",
            "Registered at",
            "Watermark created at",
            "Output size",
            "Source type",
            "Watermark payload",
            "Watermark capacity",
            "Record",
        ]
        assert dict(view.rows)["Creator ID"] == CREATOR
        assert dict(view.rows)["Registered at"] == "2026-08-21T03:42:00Z"
        assert dict(view.rows)["Record"] == "created"
        assert view.download_name == "artwork.provenance.png"
        assert view.note == EVIDENCE_NOTE


def test_success_view_marks_a_reused_registration() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        data = _png()
        service.forge(data, "artwork.png", _metadata()).unwrap()

        reused = build_success_view(service.forge(data, "again.png", _metadata()).unwrap())

        assert reused.created is False
        assert reused.headline == "Already registered. Watermark ready."
        assert dict(reused.rows)["Record"] == "reused"


@pytest.mark.parametrize(
    ("payload", "capacity", "expected"),
    [(0, 0, 0), (0, 100, 0), (50, 100, 50), (100, 100, 100), (143, 212, 67), (5, 0, 0)],
)
def test_utilisation_is_bounded(payload: int, capacity: int, expected: int) -> None:
    assert utilisation_percent(payload, capacity) == expected


@pytest.mark.parametrize(
    ("payload", "capacity", "expected"),
    [
        (143, 195_405, "143 of 195,405 bytes (under 1%)"),
        (143, 212, "143 of 212 bytes (67%)"),
        (212, 212, "212 of 212 bytes (100%)"),
        (0, 0, "0 of 0 bytes"),
    ],
)
def test_utilisation_reads_clearly_for_large_images(
    payload: int, capacity: int, expected: str
) -> None:
    assert describe_utilisation(payload, capacity) == expected


def test_failure_view_uses_safe_text_and_field_labels() -> None:
    failure = Failure(
        code=FailureCode.INVALID_FIELD,
        operation="forge_prepare",
        fields=(
            FieldIssue(FIELD_CREATOR_ID, FailureCode.INVALID_FIELD, "Use only ASCII letters."),
            FieldIssue(FIELD_CONTACT_EMAIL, FailureCode.INVALID_FIELD, "Use one @ separator."),
        ),
    )

    view = build_failure_view(failure)

    assert isinstance(view, ForgeFailureView)
    assert view.summary == USER_MESSAGES[FailureCode.INVALID_FIELD]
    assert view.has_field_detail is True
    assert [message.label for message in view.field_messages] == ["Creator ID", "Contact email"]


def test_failure_view_without_field_detail() -> None:
    view = build_failure_view(Failure(code=FailureCode.BUSY, operation="forge_register"))

    assert view.has_field_detail is False
    assert view.field_messages == ()
    assert view.summary == USER_MESSAGES[FailureCode.BUSY]


def test_capacity_failure_explains_the_minimum_image_size() -> None:
    with temporary_registry() as harness:
        result = _service(harness).forge(_png(width=4, height=4), "tiny.png", _metadata())

        view = build_failure_view(result.unwrap_failure())

        assert "too small" in view.summary
        assert "400 pixels" in view.summary


def test_identity_conflict_is_explained_plainly() -> None:
    with temporary_registry() as harness:
        service = _service(harness)
        data = _png()
        service.forge(data, "artwork.png", _metadata()).unwrap()

        conflict = service.forge(
            data,
            "artwork.png",
            CreatorMetadata(creator_id=CreatorId("studio.two"), display_name="Other"),
        )
        view = build_failure_view(conflict.unwrap_failure())

        assert "different creator ID" in view.summary
        assert view.has_field_detail is False


def test_every_failure_code_has_a_message() -> None:
    for code in FailureCode:
        assert code in USER_MESSAGES, f"missing user message for {code}"
        assert USER_MESSAGES[code] != ""


def test_messages_never_expose_internal_detail() -> None:
    failure = Failure(
        code=FailureCode.DECODE_FAILURE,
        operation="forge_prepare",
        safe_detail="pillow: broken PNG chunk at offset 91",
    )

    assert failure.safe_detail is not None
    assert message_for(failure) == USER_MESSAGES[FailureCode.DECODE_FAILURE]
    assert failure.safe_detail not in message_for(failure)


def test_unknown_field_keys_still_get_a_label() -> None:
    assert label_for(FIELD_FILE) == "Image file"
    assert label_for("some_new_field") == "Some new field"


def test_internal_errors_use_the_generic_message() -> None:
    failure = Failure(code=FailureCode.INTERNAL_ERROR, operation="anything")

    assert message_for(failure) == GENERIC_MESSAGE
