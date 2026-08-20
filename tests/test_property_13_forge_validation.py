"""Property 13: Forge validation is complete and side-effect free on rejection.

The expected issue set is computed here independently of the implementation, directly
from the acceptance criteria, so the test cannot pass by mirroring a shared bug.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5
"""

from __future__ import annotations

import re
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.domain.models import (
    MAX_UPLOAD_BYTES,
    CreatorId,
    CreatorMetadata,
    UploadMetadata,
)
from provenance.domain.validation import (
    FIELD_CONTACT_EMAIL,
    FIELD_CREATOR_ID,
    FIELD_DISPLAY_NAME,
    FIELD_FILE,
    FIELD_POSTAL_ADDRESS,
    FIELD_RIGHTS_STATEMENT,
    validate_forge_submission,
)

NUL: Final = "\x00"
_CREATOR_ID_RULE: Final = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z", re.ASCII)


def _email_is_acceptable(value: str) -> bool:
    """Independent restatement of the contact email criteria."""
    if NUL in value:
        return False
    if not 3 <= len(value) <= 254:
        return False
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return local != "" and domain != ""


def _expected_issue_fields(upload: UploadMetadata, metadata: CreatorMetadata) -> set[str]:
    """Field keys that must be reported for this submission."""
    expected: set[str] = set()

    if not 1 <= upload.byte_count <= MAX_UPLOAD_BYTES:
        expected.add(FIELD_FILE)
    if NUL in metadata.creator_id or _CREATOR_ID_RULE.match(metadata.creator_id) is None:
        expected.add(FIELD_CREATOR_ID)
    if NUL in metadata.display_name or not 1 <= len(metadata.display_name) <= 200:
        expected.add(FIELD_DISPLAY_NAME)
    if metadata.contact_email not in (None, "") and not _email_is_acceptable(
        metadata.contact_email or ""
    ):
        expected.add(FIELD_CONTACT_EMAIL)
    if metadata.postal_address not in (None, "") and (
        NUL in (metadata.postal_address or "") or len(metadata.postal_address or "") > 500
    ):
        expected.add(FIELD_POSTAL_ADDRESS)
    if metadata.rights_statement not in (None, "") and (
        NUL in (metadata.rights_statement or "") or len(metadata.rights_statement or "") > 500
    ):
        expected.add(FIELD_RIGHTS_STATEMENT)
    return expected


def _byte_counts() -> st.SearchStrategy[int]:
    return st.one_of(
        st.just(0),
        st.just(1),
        st.just(MAX_UPLOAD_BYTES),
        st.just(MAX_UPLOAD_BYTES + 1),
        st.integers(min_value=-5, max_value=64),
        st.integers(min_value=MAX_UPLOAD_BYTES - 2, max_value=MAX_UPLOAD_BYTES + 2),
    )


def _creator_id_values() -> st.SearchStrategy[str]:
    return st.one_of(
        st.text(alphabet="abzAZ09._-", min_size=1, max_size=64),
        st.text(alphabet="abzAZ09._-", min_size=65, max_size=68),
        st.just(""),
        st.text(alphabet="a b\x00/@", min_size=1, max_size=6),
        st.text(max_size=6),
    )


def _display_name_values() -> st.SearchStrategy[str]:
    return st.one_of(
        st.text(min_size=1, max_size=12),
        st.just(""),
        st.text(alphabet="a\x00", min_size=1, max_size=4),
        st.text(alphabet="d", min_size=199, max_size=202),
    )


def _email_values() -> st.SearchStrategy[str | None]:
    return st.one_of(
        st.none(),
        st.just(""),
        st.just("creator@example.com"),
        st.just("a@b"),
        st.text(alphabet="ab@.\x00", max_size=8),
        st.text(alphabet="a", min_size=253, max_size=256).map(lambda value: f"{value}@b"),
    )


def _optional_text_values() -> st.SearchStrategy[str | None]:
    return st.one_of(
        st.none(),
        st.just(""),
        st.text(max_size=10),
        st.text(alphabet="x", min_size=499, max_size=502),
        st.text(alphabet="y\x00", min_size=1, max_size=4),
    )


@given(
    _byte_counts(),
    _creator_id_values(),
    _display_name_values(),
    _email_values(),
    _optional_text_values(),
    _optional_text_values(),
)
def test_validation_is_exact_and_side_effect_free(
    byte_count: int,
    creator_id: str,
    display_name: str,
    contact_email: str | None,
    postal_address: str | None,
    rights_statement: str | None,
) -> None:
    # Feature: provenance, Property 13: Forge validation is complete and side-effect free
    # on rejection
    upload = UploadMetadata(file_name="artwork.png", byte_count=byte_count)
    metadata = CreatorMetadata(
        creator_id=CreatorId(creator_id),
        display_name=display_name,
        contact_email=contact_email,
        postal_address=postal_address,
        rights_statement=rights_statement,
    )
    expected = _expected_issue_fields(upload, metadata)

    report = validate_forge_submission(upload, metadata)
    reported = {issue.field_key for issue in report.issues}

    assert reported == expected
    assert report.is_valid is (expected == set())
    # Validation is pure: repeating it yields identical issues and mutates no input.
    assert validate_forge_submission(upload, metadata).issues == report.issues
    assert upload.byte_count == byte_count
    assert metadata.creator_id == creator_id
    assert metadata.display_name == display_name


@given(
    _byte_counts(),
    _creator_id_values(),
    _display_name_values(),
    _email_values(),
)
def test_every_issue_is_bound_to_a_known_field_and_message(
    byte_count: int,
    creator_id: str,
    display_name: str,
    contact_email: str | None,
) -> None:
    # Feature: provenance, Property 13: Forge validation is complete and side-effect free
    # on rejection
    known_fields = {
        FIELD_FILE,
        FIELD_CREATOR_ID,
        FIELD_DISPLAY_NAME,
        FIELD_CONTACT_EMAIL,
        FIELD_POSTAL_ADDRESS,
        FIELD_RIGHTS_STATEMENT,
    }
    report = validate_forge_submission(
        UploadMetadata(file_name="artwork.png", byte_count=byte_count),
        CreatorMetadata(
            creator_id=CreatorId(creator_id),
            display_name=display_name,
            contact_email=contact_email,
        ),
    )

    # Short values such as "a" or "@" occur naturally in guidance text, so echoing is
    # only meaningful for values long enough to identify the submission.
    minimum_echo_length = 8
    submitted = (creator_id, display_name, contact_email or "")

    for issue in report.issues:
        assert issue.field_key in known_fields
        assert issue.message != ""
        for value in submitted:
            if len(value) >= minimum_echo_length:
                assert value not in issue.message
