"""DNS resolution port.

Resolution happens immediately before a connection attempt and returns a frozen set
of addresses. The transport then connects only to those pinned addresses, which closes
the DNS rebinding window between the policy check and the connect.

Requirements: 7.3, 7.6, 7.9, 8.12, 14.1
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Final, Protocol

from provenance.domain.errors import Result

ADDRESS_FAMILIES: Final = (socket.AF_INET, socket.AF_INET6)


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    """One address returned by DNS, with the family needed to open a socket."""

    address: str
    family: int

    @property
    def is_ipv6(self) -> bool:
        """True for an IPv6 address."""
        return self.family == socket.AF_INET6


@dataclass(frozen=True, slots=True)
class Resolution:
    """A frozen DNS answer that a connection attempt is pinned to."""

    host: str
    port: int
    addresses: tuple[ResolvedAddress, ...]
    resolved_at_monotonic: float

    @property
    def pinned(self) -> frozenset[str]:
        """Every address this attempt is permitted to reach."""
        return frozenset(item.address for item in self.addresses)


class DnsResolverPort(Protocol):
    """Resolve a hostname under a bounded deadline, refusing excluded addresses."""

    def resolve(self, host: str, port: int, *, deadline_seconds: float) -> Result[Resolution]:
        """Return every A and AAAA address, or a typed failure."""
        ...
