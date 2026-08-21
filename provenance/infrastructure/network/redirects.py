"""Redirect following with full revalidation at every hop.

A redirect is the classic SSRF bypass: the first URL passes policy, then ``Location``
points at a private address. So redirects are followed here rather than by the HTTP
library, and each hop repeats the whole check sequence, including a fresh DNS lookup
and peer verification, through the same transport used for the first request.

Redirect bodies are charged to the budget and discarded. No cookie, authorization
header, or caller-supplied header ever crosses a hop, because the transport only sends
the user agent, identity encoding, and connection headers.

Requirements: 7.4, 7.9, 8.4, 8.6, 20.19
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import NormalizedUrl
from provenance.domain.scan_budget import ResponseKind, ScanBudget
from provenance.domain.urls import ALLOWED_PORTS, AbsoluteHttpUrl, resolve_candidate
from provenance.ports.http import SafeHttpTransport, SafeRequest, SafeResponse

FOLLOW_OPERATION: Final = "follow_redirects"
DETAIL_MISSING_LOCATION: Final = "redirect_without_location"


@dataclass(frozen=True, slots=True)
class RedirectOutcome:
    """The response that ended a redirect chain, plus the hops taken to reach it."""

    response: SafeResponse
    final_url: AbsoluteHttpUrl
    hops: tuple[NormalizedUrl, ...]

    @property
    def redirect_count(self) -> int:
        """How many redirects were followed."""
        return max(0, len(self.hops) - 1)


class RedirectFollower:
    """Follows redirects with the full policy applied to every hop."""

    __slots__ = ("_transport", "_allowed_ports")

    def __init__(
        self,
        transport: SafeHttpTransport,
        *,
        allowed_ports: frozenset[int] = ALLOWED_PORTS,
    ) -> None:
        self._transport = transport
        # Only contract tests narrow or widen this; production keeps 80 and 443.
        self._allowed_ports = allowed_ports

    def fetch(self, request: SafeRequest, budget: ScanBudget) -> Result[RedirectOutcome]:
        """Fetch one logical resource, revalidating each redirect destination."""
        chain = budget.open_request_chain()
        current = request.url
        hops: list[NormalizedUrl] = [current.normalized]

        while True:
            guard = budget.check_continue()
            if guard.failure is not None:
                return Result(failure=guard.failure)

            attempt = self._transport.fetch(
                SafeRequest(
                    url=current,
                    kind=request.kind,
                    method=request.method,
                    headers=request.headers,
                ),
                budget,
            )
            if attempt.failure is not None:
                return Result(failure=attempt.failure)

            response = attempt.unwrap()
            if not response.head.has_redirect_status:
                return ok(RedirectOutcome(response=response, final_url=current, hops=tuple(hops)))

            # A redirect status is never treated as a final response, so a missing
            # Location is reported as malformed rather than silently returned as content.
            location = response.head.location
            self._discard_body(response, budget)

            if location is None or location.strip() == "":
                return failed(
                    FailureCode.HTTP_STATUS,
                    FOLLOW_OPERATION,
                    safe_detail=DETAIL_MISSING_LOCATION,
                )

            permitted = chain.follow()
            if permitted.failure is not None:
                return Result(failure=permitted.failure)

            # The destination is resolved against the current hop and revalidated from
            # scratch: scheme, port, credentials, host, then DNS and peer on the next
            # transport call.
            destination = resolve_candidate(
                current.normalized, location, allowed_ports=self._allowed_ports
            )
            if destination.failure is not None:
                return Result(failure=destination.failure)

            current = destination.unwrap()
            hops.append(current.normalized)

    def _discard_body(self, response: SafeResponse, budget: ScanBudget) -> None:
        """Charge and drop a redirect body so it cannot be used or exceed the budget."""
        lease = budget.open_response(ResponseKind.REDIRECT)
        response.read_body(lease)
        response.close()
