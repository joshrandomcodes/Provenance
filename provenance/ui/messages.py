"""Safe, user-facing text for every expected failure.

Messages never echo submitted values, library exception text, or local paths. Each one
tells the creator what to do next.

Requirements: 17.6, 19.8, 21.4
"""

from __future__ import annotations

from typing import Final

from provenance.domain.errors import Failure, FailureCode
from provenance.domain.validation import (
    FIELD_CONTACT_EMAIL,
    FIELD_CREATOR_ID,
    FIELD_DISPLAY_NAME,
    FIELD_FILE,
    FIELD_POSTAL_ADDRESS,
    FIELD_RATIONALE,
    FIELD_RIGHTS_STATEMENT,
)

FIELD_LABELS: Final = {
    FIELD_FILE: "Image file",
    FIELD_CREATOR_ID: "Creator ID",
    FIELD_DISPLAY_NAME: "Display name",
    FIELD_CONTACT_EMAIL: "Contact email",
    FIELD_POSTAL_ADDRESS: "Postal address",
    FIELD_RIGHTS_STATEMENT: "Rights statement",
    FIELD_RATIONALE: "Fair use rationale",
}

GENERIC_MESSAGE: Final = "That did not complete. Nothing was saved."

USER_MESSAGES: Final = {
    FailureCode.EMPTY_FILE: "Choose an image file that is not empty.",
    FailureCode.BYTE_LIMIT: "Choose an image of 25 MiB or less.",
    FailureCode.UNSUPPORTED_FORMAT: "Choose a PNG or JPEG image.",
    FailureCode.INVALID_DIMENSIONS: "Choose an image at least one pixel wide and tall.",
    FailureCode.PIXEL_LIMIT: "Choose an image of 40 megapixels or less.",
    FailureCode.DECODE_FAILURE: "That file could not be read as a complete image.",
    FailureCode.INVALID_FIELD: "Correct the highlighted fields and try again.",
    FailureCode.MISSING_FIELD: "Fill in the highlighted fields and try again.",
    FailureCode.FIELD_TOO_LONG: "Shorten the highlighted fields and try again.",
    FailureCode.FIELD_TOO_SHORT: "Add more detail to the highlighted fields.",
    FailureCode.FORBIDDEN_CHARACTER: "Remove unsupported characters from the highlighted fields.",
    FailureCode.CAPACITY_EXCEEDED: (
        "This image is too small to carry a watermark. A watermark needs roughly "
        "400 pixels or more in total."
    ),
    FailureCode.NO_WATERMARK: "No Provenance watermark was found in that image.",
    FailureCode.CORRUPT_WATERMARK: (
        "A Provenance marker was found but the watermark did not verify, so no identity "
        "can be read from it."
    ),
    FailureCode.PNG_ROUNDTRIP_FAILED: (
        "The watermarked image could not be written losslessly, so it was discarded."
    ),
    FailureCode.CHECKS_PENDING: "The local registry has not finished its startup checks yet.",
    FailureCode.CHECKS_FAILED: (
        "The local registry did not pass its startup checks, so saving is disabled."
    ),
    FailureCode.IDENTITY_CONFLICT: (
        "This exact image is already registered to a different creator ID, so it was not "
        "registered again."
    ),
    FailureCode.CONSTRAINT: "That would conflict with an existing record, so nothing was saved.",
    FailureCode.BUSY: "The local registry is busy. Try again in a moment.",
    FailureCode.COMMIT_FAILED: "The change could not be saved, so nothing was changed.",
    FailureCode.STALE_PREVIEW: "The details changed since you reviewed them. Review them again.",
    FailureCode.NOT_FOUND: "That record no longer exists.",
    FailureCode.UNSUPPORTED_SCHEME: "Enter an address beginning with https:// or http://.",
    FailureCode.PORT: "Only the standard web ports 80 and 443 are supported.",
    FailureCode.CREDENTIALS: "Remove the username or password from the address.",
    FailureCode.MALFORMED_HOST: "That address could not be read. Check it and try again.",
    FailureCode.DNS_FAILED: "The address could not be resolved.",
    FailureCode.NONPUBLIC_ADDRESS: (
        "That address resolves to a private or local network, which Provenance will not contact."
    ),
    FailureCode.PEER_MISMATCH: (
        "The server answered from an unexpected address, so the request was stopped."
    ),
    FailureCode.ROBOTS_DISALLOWED: "That site's robots.txt asks crawlers not to fetch this page.",
    FailureCode.ROBOTS_UNAVAILABLE: "That site's robots.txt could not be read.",
    FailureCode.HTTP_STATUS: "The site returned a response that could not be used.",
    FailureCode.TLS: "A secure connection to that site could not be established.",
    FailureCode.CONNECT_TIMEOUT: "That site did not answer in time.",
    FailureCode.READ_TIMEOUT: "That site stopped sending data.",
    FailureCode.REDIRECT_LIMIT: "That address redirected too many times.",
    FailureCode.UNSUPPORTED_MEDIA_TYPE: "That response was not an image.",
    FailureCode.HTML_LIMIT: "That page is larger than the scan limit.",
    FailureCode.IMAGE_BYTES_LIMIT: "That image is larger than the scan limit.",
    FailureCode.TOTAL_BYTES_LIMIT: "The scan reached its total download limit.",
    FailureCode.SCAN_TIMEOUT: "The scan reached its time limit.",
    FailureCode.CANCELLED: "That was cancelled, so nothing was changed.",
    FailureCode.DNS_NO_RECORDS: "No addresses were returned for that host.",
    FailureCode.WHOIS_NO_DATA: "No WHOIS data was returned for that host.",
    FailureCode.WHOIS_MALFORMED: "The WHOIS response could not be read.",
    FailureCode.WHOIS_LIMIT: "The WHOIS response exceeded its size or time limit.",
    FailureCode.DEPENDENCY_INCOMPATIBLE: "A required local component is not compatible.",
    FailureCode.STALE_CONFIRMATION: "The content changed after you confirmed it. Confirm again.",
    FailureCode.MISSING_ACKNOWLEDGEMENT: (
        "Confirm you are authorized to scan this page before starting."
    ),
    FailureCode.MISSING_ATTESTATION: "Every confirmation is required before sending.",
    FailureCode.DRAFT_UNAVAILABLE: "No email draft could be opened on this computer.",
    FailureCode.DRAFT_CANCELLED: "The email draft was not opened.",
    FailureCode.OUTCOME_PENDING: "Record whether the notice was sent.",
    FailureCode.UI_COMPATIBILITY_FAILED: "This screen is not compatible with the installed UI.",
    FailureCode.INTERNAL_ERROR: GENERIC_MESSAGE,
}


def message_for(failure: Failure) -> str:
    """Return the safe user-facing message for a failure."""
    return USER_MESSAGES.get(failure.code, GENERIC_MESSAGE)


def label_for(field_key: str) -> str:
    """Return the visible label for a field key."""
    return FIELD_LABELS.get(field_key, field_key.replace("_", " ").capitalize())
