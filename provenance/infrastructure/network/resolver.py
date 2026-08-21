"""Bounded, public-only DNS resolution.

``socket.getaddrinfo`` cannot be interrupted, so it runs on a daemon worker thread and
the caller waits only until its deadline. A late answer is discarded: the operation has
already been reported as a timeout, and no connection is made from a discarded answer.

A hostname is usable only when it returns at least one address and *every* returned
address is public. Accepting a partially public answer would let a host publish one
routable address alongside a private one and have the scan reach the private target.

Requirements: 7.3, 7.6, 7.9, 8.12, 14.1, 14.8
"""

from __future__ import annotations

import queue
import socket
import threading
from collections.abc import Callable
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.time import Clock
from provenance.domain.urls import is_public_network_address
from provenance.ports.dns import ADDRESS_FAMILIES, Resolution, ResolvedAddress

RESOLVE_OPERATION: Final = "resolve_host"

DETAIL_TIMEOUT: Final = "dns_timeout"
DETAIL_NO_RECORDS: Final = "dns_no_records"
DETAIL_LOOKUP_FAILED: Final = "dns_lookup_failed"
DETAIL_NONPUBLIC: Final = "dns_returned_nonpublic_address"

# Injectable so tests can allow loopback. Production always uses the strict predicate.
AddressPolicy = Callable[[str], bool]


class PublicOnlyResolver:
    """Resolves hostnames and admits only fully public answers."""

    __slots__ = ("_clock", "_is_public")

    def __init__(self, clock: Clock, is_public: AddressPolicy | None = None) -> None:
        self._clock = clock
        self._is_public: AddressPolicy = (
            is_public if is_public is not None else is_public_network_address
        )

    def resolve(self, host: str, port: int, *, deadline_seconds: float) -> Result[Resolution]:
        """Resolve within the deadline, refusing empty or partially public answers."""
        bare_host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        answers = self._lookup(bare_host, port, deadline_seconds)
        if answers.failure is not None:
            return Result(failure=answers.failure)

        addresses = answers.unwrap()
        if not addresses:
            return failed(
                FailureCode.DNS_NO_RECORDS, RESOLVE_OPERATION, safe_detail=DETAIL_NO_RECORDS
            )
        for candidate in addresses:
            if not self._is_public(candidate.address):
                return failed(
                    FailureCode.NONPUBLIC_ADDRESS,
                    RESOLVE_OPERATION,
                    safe_detail=DETAIL_NONPUBLIC,
                )

        return ok(
            Resolution(
                host=bare_host,
                port=port,
                addresses=addresses,
                resolved_at_monotonic=self._clock.monotonic(),
            )
        )

    def _lookup(
        self, host: str, port: int, deadline_seconds: float
    ) -> Result[tuple[ResolvedAddress, ...]]:
        results: queue.Queue[tuple[ResolvedAddress, ...] | BaseException] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except (OSError, UnicodeError) as error:
                results.put(error)
                return
            seen: dict[str, ResolvedAddress] = {}
            for family, _type, _proto, _canonical, sockaddr in infos:
                if family not in ADDRESS_FAMILIES:
                    continue
                address = str(sockaddr[0])
                if address not in seen:
                    seen[address] = ResolvedAddress(address=address, family=family)
            results.put(tuple(seen.values()))

        thread = threading.Thread(target=worker, name="provenance-dns", daemon=True)
        thread.start()

        try:
            outcome = results.get(timeout=max(0.0, deadline_seconds))
        except queue.Empty:
            # The worker is abandoned; its late answer is never used.
            return failed(FailureCode.DNS_FAILED, RESOLVE_OPERATION, safe_detail=DETAIL_TIMEOUT)

        if isinstance(outcome, BaseException):
            return failed(
                FailureCode.DNS_FAILED, RESOLVE_OPERATION, safe_detail=DETAIL_LOOKUP_FAILED
            )
        return ok(outcome)
