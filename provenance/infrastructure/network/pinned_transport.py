"""HTTP transport that verifies the peer before sending any request bytes.

Why this exists instead of a plain ``requests`` call: the default adapter only exposes
the connected peer after the request has already been written, and it does so through
private urllib3 attributes. Verifying an address *after* transmitting the request is
too late for SSRF defence, so connection establishment is owned here.

Attempt order, asserted by contract tests:

1. resolve immediately, requiring a non-empty and fully public answer
2. connect a socket directly to one pinned address
3. compare ``getpeername`` against the pinned set and the address policy
4. for HTTPS, wrap with certificate and hostname verification, then recheck the peer
5. only then write the request bytes
6. read the body only through a budget lease, charging each chunk before retaining it

``requests`` still prepares the request line and headers, and the response head is
exposed through familiar structures, but no proxy, environment proxy, ``.netrc`` entry,
cookie, retry, or automatic redirect is ever consulted.

Requirements: 7.3, 7.5, 7.6, 7.7, 7.8, 7.9, 8.4-8.7, 8.10-8.13, 9.4, 17.2
"""

from __future__ import annotations

import http.client
import socket
import ssl
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

import requests

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.scan_budget import ResponseLease, ScanBudget
from provenance.domain.urls import (
    SCHEME_HTTPS,
    AbsoluteHttpUrl,
    is_public_network_address,
)
from provenance.ports.dns import DnsResolverPort, Resolution, ResolvedAddress
from provenance.ports.http import (
    STAGE_ADDRESSES_VALIDATED,
    STAGE_CLOSED,
    STAGE_CONNECTED,
    STAGE_DNS,
    STAGE_PEER_RECHECKED,
    STAGE_PEER_VERIFIED,
    STAGE_REQUEST_SENT,
    STAGE_RESPONSE_STARTED,
    STAGE_TLS_ESTABLISHED,
    NullProbe,
    ResponseHead,
    SafeRequest,
    TransportProbe,
)

FETCH_OPERATION: Final = "fetch_url"
DEFAULT_CHUNK_SIZE: Final = 8_192

DETAIL_PEER_UNKNOWN: Final = "peer_address_unavailable"
DETAIL_PEER_NOT_PINNED: Final = "peer_not_in_resolved_set"
DETAIL_PEER_NONPUBLIC: Final = "peer_not_public"
DETAIL_CONNECT_FAILED: Final = "connect_failed"
DETAIL_TLS_FAILED: Final = "tls_handshake_failed"
DETAIL_REQUEST_FAILED: Final = "request_write_failed"
DETAIL_RESPONSE_FAILED: Final = "response_read_failed"
DETAIL_BAD_STATUS_LINE: Final = "malformed_response"

AddressPolicy = Callable[[str], bool]
SocketFactory = Callable[[int], socket.socket]


def _default_socket_factory(family: int) -> socket.socket:
    return socket.socket(family, socket.SOCK_STREAM)


@dataclass(frozen=True, slots=True)
class TransportSettings:
    """Identity and timing for outbound requests."""

    user_agent: str
    connect_seconds: float = 5.0
    next_byte_seconds: float = 15.0


class PinnedResponse:
    """A response whose body is only readable through a budget lease."""

    __slots__ = ("_head", "_raw", "_connection", "_socket", "_budget", "_probe", "_closed")

    def __init__(
        self,
        head: ResponseHead,
        raw: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
        active_socket: socket.socket,
        budget: ScanBudget,
        probe: TransportProbe,
    ) -> None:
        self._head = head
        self._raw = raw
        self._connection = connection
        self._socket = active_socket
        self._budget = budget
        self._probe = probe
        self._closed = False

    @property
    def head(self) -> ResponseHead:
        """Status, headers, peer address, and redirect target."""
        return self._head

    def stream(self, lease: ResponseLease) -> Iterator[bytes]:
        """Yield body chunks, charging each chunk before it is handed to the caller."""
        try:
            while True:
                if self._budget.check_continue().failure is not None:
                    return
                size = lease.next_read_size(DEFAULT_CHUNK_SIZE)
                self._socket.settimeout(self._next_byte_timeout())
                chunk = self._raw.read(size)
                if chunk == b"":
                    return
                if lease.consume(len(chunk)).failure is not None:
                    # The over-limit chunk is dropped rather than returned.
                    return
                if self._budget.check_continue().failure is not None:
                    return
                yield chunk
        finally:
            self.close()

    def read_body(self, lease: ResponseLease) -> Result[bytes]:
        """Read the entire body, refusing to exceed the lease allowance."""
        collected = bytearray()
        try:
            while True:
                guard = self._budget.check_continue()
                if guard.failure is not None:
                    return Result(failure=guard.failure)

                allowance = lease.remaining
                size = lease.next_read_size(DEFAULT_CHUNK_SIZE)
                self._socket.settimeout(self._next_byte_timeout())
                try:
                    chunk = self._raw.read(size)
                except TimeoutError:
                    return failed(FailureCode.READ_TIMEOUT, FETCH_OPERATION)
                except (OSError, http.client.HTTPException):
                    return failed(
                        FailureCode.HTTP_STATUS, FETCH_OPERATION, safe_detail=DETAIL_RESPONSE_FAILED
                    )

                if chunk == b"":
                    return ok(bytes(collected))
                if len(chunk) > allowance:
                    # The sentinel byte proved the body exceeds its allowance.
                    charge = lease.consume(allowance)
                    if charge.failure is not None:
                        return Result(failure=charge.failure)
                    return Result(failure=lease.consume(len(chunk) - allowance).unwrap_failure())

                charged = lease.consume(len(chunk))
                if charged.failure is not None:
                    return Result(failure=charged.failure)
                collected.extend(chunk)
        finally:
            self.close()

    def close(self) -> None:
        """Close the response, connection, and socket exactly once."""
        if self._closed:
            return
        self._closed = True
        for closer in (self._raw.close, self._connection.close, self._socket.close):
            try:
                closer()
            except OSError:
                continue
        self._probe.record(STAGE_CLOSED)

    def _next_byte_timeout(self) -> float:
        return max(
            0.001, min(self._budget.limits.next_byte_seconds, self._budget.seconds_remaining)
        )


class PinnedHttpTransport:
    """Opens one verified connection per attempt and returns the response head."""

    __slots__ = (
        "_resolver",
        "_settings",
        "_is_public",
        "_socket_factory",
        "_probe",
        "_tls_context",
    )

    def __init__(
        self,
        resolver: DnsResolverPort,
        settings: TransportSettings,
        *,
        is_public: AddressPolicy | None = None,
        socket_factory: SocketFactory | None = None,
        probe: TransportProbe | None = None,
        tls_context: ssl.SSLContext | None = None,
    ) -> None:
        self._resolver = resolver
        self._settings = settings
        self._is_public: AddressPolicy = (
            is_public if is_public is not None else is_public_network_address
        )
        self._socket_factory: SocketFactory = socket_factory or _default_socket_factory
        self._probe: TransportProbe = probe or NullProbe()
        self._tls_context = tls_context or ssl.create_default_context()

    def fetch(self, request: SafeRequest, budget: ScanBudget) -> Result[PinnedResponse]:
        """Resolve, connect, verify, and send. The body stays unread."""
        guard = budget.check_continue()
        if guard.failure is not None:
            return Result(failure=guard.failure)

        url = request.url
        resolved = self._resolver.resolve(
            url.host, url.port, deadline_seconds=self._connect_budget(budget)
        )
        if resolved.failure is not None:
            return Result(failure=resolved.failure)
        resolution = resolved.unwrap()
        self._probe.record(STAGE_DNS, ",".join(sorted(resolution.pinned)))

        for address in resolution.addresses:
            if not self._is_public(address.address):
                return failed(
                    FailureCode.NONPUBLIC_ADDRESS,
                    FETCH_OPERATION,
                    safe_detail=DETAIL_PEER_NONPUBLIC,
                )
        self._probe.record(STAGE_ADDRESSES_VALIDATED)

        return self._attempt(request, resolution, budget)

    def _attempt(
        self, request: SafeRequest, resolution: Resolution, budget: ScanBudget
    ) -> Result[PinnedResponse]:
        last_failure: Result[PinnedResponse] | None = None
        for address in resolution.addresses:
            remaining = self._connect_budget(budget)
            if remaining <= 0:
                return failed(FailureCode.CONNECT_TIMEOUT, FETCH_OPERATION)

            attempt = self._connect_and_send(request, resolution, address, remaining, budget)
            if attempt.failure is None:
                return attempt
            last_failure = attempt
            if attempt.unwrap_failure().code in {
                FailureCode.PEER_MISMATCH,
                FailureCode.NONPUBLIC_ADDRESS,
            }:
                # A policy violation is never retried against another address.
                return attempt

        return last_failure or failed(FailureCode.DNS_NO_RECORDS, FETCH_OPERATION)

    def _connect_and_send(
        self,
        request: SafeRequest,
        resolution: Resolution,
        address: ResolvedAddress,
        connect_seconds: float,
        budget: ScanBudget,
    ) -> Result[PinnedResponse]:
        url = request.url
        raw_socket = self._socket_factory(address.family)
        raw_socket.settimeout(connect_seconds)

        try:
            raw_socket.connect((address.address, url.port))
        except TimeoutError:
            raw_socket.close()
            return failed(FailureCode.CONNECT_TIMEOUT, FETCH_OPERATION)
        except OSError:
            raw_socket.close()
            return failed(FailureCode.TLS, FETCH_OPERATION, safe_detail=DETAIL_CONNECT_FAILED)
        self._probe.record(STAGE_CONNECTED, address.address)

        verified = self._verify_peer(raw_socket, resolution)
        if verified.failure is not None:
            raw_socket.close()
            return Result(failure=verified.failure)
        self._probe.record(STAGE_PEER_VERIFIED)

        active_socket = raw_socket
        if url.scheme == SCHEME_HTTPS:
            wrapped = self._start_tls(raw_socket, url)
            if wrapped.failure is not None:
                raw_socket.close()
                return Result(failure=wrapped.failure)
            active_socket = wrapped.unwrap()
            self._probe.record(STAGE_TLS_ESTABLISHED)

            rechecked = self._verify_peer(active_socket, resolution)
            if rechecked.failure is not None:
                active_socket.close()
                return Result(failure=rechecked.failure)
            self._probe.record(STAGE_PEER_RECHECKED)

        return self._send(request, active_socket, budget)

    def _verify_peer(self, active: socket.socket, resolution: Resolution) -> Result[str]:
        try:
            peer = active.getpeername()
        except OSError:
            return failed(
                FailureCode.PEER_MISMATCH, FETCH_OPERATION, safe_detail=DETAIL_PEER_UNKNOWN
            )

        peer_address = str(peer[0]) if isinstance(peer, tuple) else ""
        if peer_address == "":
            return failed(
                FailureCode.PEER_MISMATCH, FETCH_OPERATION, safe_detail=DETAIL_PEER_UNKNOWN
            )
        if peer_address not in resolution.pinned:
            return failed(
                FailureCode.PEER_MISMATCH, FETCH_OPERATION, safe_detail=DETAIL_PEER_NOT_PINNED
            )
        if not self._is_public(peer_address):
            return failed(
                FailureCode.NONPUBLIC_ADDRESS, FETCH_OPERATION, safe_detail=DETAIL_PEER_NONPUBLIC
            )
        return ok(peer_address)

    def _start_tls(self, raw_socket: socket.socket, url: AbsoluteHttpUrl) -> Result[ssl.SSLSocket]:
        hostname = url.host[1:-1] if url.host.startswith("[") else url.host
        try:
            return ok(self._tls_context.wrap_socket(raw_socket, server_hostname=hostname))
        except (ssl.SSLError, ssl.CertificateError, OSError):
            return failed(FailureCode.TLS, FETCH_OPERATION, safe_detail=DETAIL_TLS_FAILED)

    def _send(
        self, request: SafeRequest, active_socket: socket.socket, budget: ScanBudget
    ) -> Result[PinnedResponse]:
        url = request.url
        prepared = self._prepare(request)
        connection = http.client.HTTPConnection(url.host, url.port)
        connection.sock = active_socket

        try:
            active_socket.settimeout(
                max(0.001, min(self._settings.next_byte_seconds, budget.seconds_remaining))
            )
            connection.request(
                prepared.method or request.method,
                prepared.path_url,
                headers=dict(prepared.headers),
            )
            self._probe.record(STAGE_REQUEST_SENT)
            raw = connection.getresponse()
        except TimeoutError:
            connection.close()
            active_socket.close()
            return failed(FailureCode.READ_TIMEOUT, FETCH_OPERATION)
        except http.client.HTTPException:
            connection.close()
            active_socket.close()
            return failed(
                FailureCode.HTTP_STATUS, FETCH_OPERATION, safe_detail=DETAIL_BAD_STATUS_LINE
            )
        except OSError:
            connection.close()
            active_socket.close()
            return failed(
                FailureCode.HTTP_STATUS, FETCH_OPERATION, safe_detail=DETAIL_REQUEST_FAILED
            )

        self._probe.record(STAGE_RESPONSE_STARTED, str(raw.status))
        head = self._read_head(raw, request, active_socket)
        return ok(
            PinnedResponse(
                head=head,
                raw=raw,
                connection=connection,
                active_socket=active_socket,
                budget=budget,
                probe=self._probe,
            )
        )

    def _prepare(self, request: SafeRequest) -> requests.PreparedRequest:
        headers: dict[str, str] = {
            "User-Agent": self._settings.user_agent,
            # Identity encoding keeps declared and streamed byte counts comparable.
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        headers.update(dict(request.headers))
        return requests.Request(
            method=request.method, url=str(request.url.normalized), headers=headers
        ).prepare()

    def _read_head(
        self, raw: http.client.HTTPResponse, request: SafeRequest, active: socket.socket
    ) -> ResponseHead:
        headers: Mapping[str, str] = {key.lower(): value for key, value in raw.getheaders()}
        declared = headers.get("content-length")
        try:
            declared_length = int(declared) if declared is not None else None
        except ValueError:
            declared_length = None
        try:
            peer = str(active.getpeername()[0])
        except OSError:
            peer = ""

        return ResponseHead(
            status=raw.status,
            headers=headers,
            url=request.url.normalized,
            peer_address=peer,
            declared_length=declared_length,
            content_type=headers.get("content-type"),
            location=headers.get("location"),
        )

    def _connect_budget(self, budget: ScanBudget) -> float:
        """One attempt shares a single connect deadline across pinned addresses."""
        return max(0.0, min(self._settings.connect_seconds, budget.seconds_remaining))
