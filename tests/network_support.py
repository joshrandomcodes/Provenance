"""Local scripted HTTP server and transport helpers for network contract tests.

Development-only. No test in the deterministic suite contacts a public endpoint.

The SSRF policy refuses loopback, which is exactly what production requires, so these
tests inject a permissive address policy and a stub resolver. A separate test asserts
that the *default* policy refuses loopback, which is what keeps production safe.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from provenance.domain.errors import Result, ok
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.urls import AbsoluteHttpUrl, is_public_network_address
from provenance.infrastructure.network.pinned_transport import (
    PinnedHttpTransport,
    TransportSettings,
)
from provenance.ports.dns import DnsResolverPort, Resolution, ResolvedAddress

AddressPolicy = Callable[[str], bool]

LOOPBACK: Final = "127.0.0.1"
TEST_USER_AGENT: Final = "Provenance/test (+https://example.invalid/provenance)"


class ManualClock:
    """Monotonic clock advanced explicitly by tests."""

    def __init__(self) -> None:
        self.seconds = 1_000.0

    def utc_now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@dataclass(slots=True)
class RecordingProbe:
    """Captures attempt stages so ordering can be asserted."""

    stages: list[str] = field(default_factory=list)
    details: list[tuple[str, str]] = field(default_factory=list)

    def record(self, stage: str, detail: str = "") -> None:
        self.stages.append(stage)
        self.details.append((stage, detail))

    def index_of(self, stage: str) -> int:
        return self.stages.index(stage)

    def occurred(self, stage: str) -> bool:
        return stage in self.stages


@dataclass(frozen=True, slots=True)
class StubResolver:
    """Returns fixed addresses without touching real DNS."""

    addresses: tuple[ResolvedAddress, ...]
    clock: ManualClock
    failure: Result[Resolution] | None = None

    def resolve(self, host: str, port: int, *, deadline_seconds: float) -> Result[Resolution]:
        if self.failure is not None:
            return self.failure
        return ok(
            Resolution(
                host=host,
                port=port,
                addresses=self.addresses,
                resolved_at_monotonic=self.clock.monotonic(),
            )
        )


def loopback_resolver(clock: ManualClock) -> StubResolver:
    """Resolver that always answers with the loopback address."""
    return StubResolver(
        addresses=(ResolvedAddress(address=LOOPBACK, family=socket.AF_INET),), clock=clock
    )


def allow_any_address(_address: str) -> bool:
    """Permissive policy used only by tests."""
    return True


@dataclass(slots=True)
class ScriptedRoute:
    """One canned response."""

    status: int = 200
    body: bytes = b"ok"
    headers: dict[str, str] = field(default_factory=dict)
    delay_before_body: float = 0.0
    omit_content_length: bool = False
    overstate_content_length: int | None = None
    # Real servers honour our `Connection: close` request header and echo it. Python's
    # BaseHTTPRequestHandler does not, which hid a production-only defect.
    announce_close: bool = False


class ScriptedServer:
    """A local HTTP server returning canned responses per path."""

    def __init__(self, routes: dict[str, ScriptedRoute]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._server = ThreadingHTTPServer((LOOPBACK, 0), self._build_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        """Ephemeral port the server bound to."""
        return int(self._server.server_address[1])

    def start(self) -> None:
        """Begin serving in a daemon thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and release the socket."""
        self._server.shutdown()
        self._server.server_close()

    def url(self, path: str = "/") -> AbsoluteHttpUrl:
        """An already validated URL value pointing at this server.

        Constructed directly because the ephemeral test port is outside the accepted
        production ports, and port acceptance is covered by the URL tests.
        """
        target, _, query = path.partition("?")
        return AbsoluteHttpUrl(
            scheme="http", host=LOOPBACK, port=self.port, path=target or "/", query=query
        )

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                route = server.routes.get(self.path)
                server.requests.append((self.path, dict(self.headers.items())))
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                self.send_response(route.status)
                if route.announce_close:
                    self.send_header("Connection", "close")
                for key, value in route.headers.items():
                    self.send_header(key, value)
                if route.overstate_content_length is not None:
                    self.send_header("Content-Length", str(route.overstate_content_length))
                elif not route.omit_content_length:
                    self.send_header("Content-Length", str(len(route.body)))
                self.end_headers()

                if route.delay_before_body > 0:
                    threading.Event().wait(route.delay_before_body)
                if route.body:
                    self.wfile.write(route.body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                """Silence the default stderr logging."""

        return Handler


@contextmanager
def scripted_server(routes: dict[str, ScriptedRoute]) -> Iterator[ScriptedServer]:
    """Run a scripted server for the duration of a test."""
    server = ScriptedServer(routes)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def build_transport(
    clock: ManualClock,
    probe: RecordingProbe | None = None,
    *,
    resolver: DnsResolverPort | None = None,
    connect_seconds: float = 5.0,
    next_byte_seconds: float = 15.0,
    is_public: AddressPolicy = allow_any_address,
) -> PinnedHttpTransport:
    """A transport wired for loopback testing with a permissive address policy."""
    return PinnedHttpTransport(
        resolver=resolver if resolver is not None else loopback_resolver(clock),
        settings=TransportSettings(
            user_agent=TEST_USER_AGENT,
            connect_seconds=connect_seconds,
            next_byte_seconds=next_byte_seconds,
        ),
        is_public=is_public,
        probe=probe,
    )


def build_budget(clock: ManualClock, limits: ScanLimits | None = None) -> ScanBudget:
    """A scan budget bound to the manual clock."""
    return ScanBudget(limits or ScanLimits(), clock, None)


@dataclass(frozen=True, slots=True)
class MappedResolver:
    """Resolves specific hosts to specific addresses, defaulting to loopback."""

    clock: ManualClock
    mapping: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def resolve(self, host: str, port: int, *, deadline_seconds: float) -> Result[Resolution]:
        addresses = self.mapping.get(host, (LOOPBACK,))
        return ok(
            Resolution(
                host=host,
                port=port,
                addresses=tuple(
                    ResolvedAddress(address=address, family=socket.AF_INET) for address in addresses
                ),
                resolved_at_monotonic=self.clock.monotonic(),
            )
        )


def allow_loopback_only(address: str) -> bool:
    """Test policy: loopback is reachable, everything else must be genuinely public.

    This keeps redirect tests meaningful, because a hop to a private address is still
    refused exactly as it would be in production.
    """
    return address == LOOPBACK or is_public_network_address(address)


# Contract tests reach a local server on an ephemeral port, so redirect resolution needs
# a wider port set than production. Production callers keep the default {80, 443}.
TEST_ALLOWED_PORTS: Final = frozenset({80, 443}) | frozenset(range(1024, 65536))


class _PortForwardingSocket(socket.socket):
    """Connects to the test server regardless of the port in the URL.

    This lets tests use production-legal URLs on port 80 while the scripted server
    listens on an ephemeral port. Address checks still apply, because the peer really is
    the loopback address the resolver pinned.
    """

    def __init__(self, family: int, actual_port: int) -> None:
        super().__init__(family, socket.SOCK_STREAM)
        self._actual_port = actual_port

    def connect(self, address: object) -> None:
        super().connect((LOOPBACK, self._actual_port))


def port_forwarding_socket_factory(actual_port: int) -> Callable[[int], socket.socket]:
    """A socket factory that always reaches the scripted server."""

    def factory(family: int) -> socket.socket:
        return _PortForwardingSocket(family, actual_port)

    return factory


def url_on_standard_port(path: str = "/", host: str = LOOPBACK) -> AbsoluteHttpUrl:
    """A production-legal URL that the port-forwarding factory will reach."""
    target, _, query = path.partition("?")
    return AbsoluteHttpUrl(scheme="http", host=host, port=80, path=target or "/", query=query)
