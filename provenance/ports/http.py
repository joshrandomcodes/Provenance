"""Bounded, SSRF-safe HTTP port.

The transport never follows redirects on its own and never returns a body without a
budget lease, so redirect revalidation and byte accounting stay with the caller.

Requirements: 7.3-7.9, 8.4-8.7, 8.10-8.13, 9.4, 17.2
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from provenance.domain.errors import Result
from provenance.domain.models import NormalizedUrl
from provenance.domain.scan_budget import ResponseKind, ResponseLease, ScanBudget
from provenance.domain.urls import AbsoluteHttpUrl

# Stages recorded during one attempt, in the order they must occur.
STAGE_DNS = "dns_resolved"
STAGE_ADDRESSES_VALIDATED = "addresses_validated"
STAGE_CONNECTED = "socket_connected"
STAGE_PEER_VERIFIED = "peer_verified"
STAGE_TLS_ESTABLISHED = "tls_established"
STAGE_PEER_RECHECKED = "peer_rechecked"
STAGE_REQUEST_SENT = "request_sent"
STAGE_RESPONSE_STARTED = "response_started"
STAGE_CLOSED = "closed"

# Statuses that carry a redirect. A redirect status without a usable Location is a
# malformed response, not a final one, so the two conditions are checked separately.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class TransportProbe(Protocol):
    """Observes attempt stages. Used by contract tests to assert ordering."""

    def record(self, stage: str, detail: str = "") -> None:
        """Record one stage of an attempt."""
        ...


class NullProbe:
    """Discards stage records."""

    __slots__ = ()

    def record(self, stage: str, detail: str = "") -> None:
        """Do nothing."""


@dataclass(frozen=True, slots=True)
class SafeRequest:
    """One outbound request against an already validated URL."""

    url: AbsoluteHttpUrl
    kind: ResponseKind
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResponseHead:
    """Status and metadata available before the body is read."""

    status: int
    headers: Mapping[str, str]
    url: NormalizedUrl
    peer_address: str
    declared_length: int | None = None
    content_type: str | None = None
    location: str | None = None

    @property
    def has_redirect_status(self) -> bool:
        """True for a redirect status, whether or not a Location was supplied."""
        return self.status in REDIRECT_STATUSES

    @property
    def is_redirect(self) -> bool:
        """True for a redirect status carrying a Location header."""
        return self.has_redirect_status and self.location is not None


class SafeResponse(Protocol):
    """A streamed response whose body is charged to a budget lease."""

    @property
    def head(self) -> ResponseHead: ...

    def stream(self, lease: ResponseLease) -> Iterator[bytes]:
        """Yield body chunks, charging each to the lease before returning it."""
        ...

    def read_body(self, lease: ResponseLease) -> Result[bytes]:
        """Read the whole body within the lease allowance."""
        ...

    def close(self) -> None:
        """Release the socket and connection."""
        ...


class SafeHttpTransport(Protocol):
    """Performs one bounded, pinned attempt per call."""

    def fetch(self, request: SafeRequest, budget: ScanBudget) -> Result[SafeResponse]:
        """Resolve, connect, verify the peer, and return response head only."""
        ...
