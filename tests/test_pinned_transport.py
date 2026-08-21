"""Transport contract: ordering, peer verification, and bounded body reads.

Requirements: 7.3, 7.5, 7.6, 7.7, 7.9, 8.4-8.7, 8.12, 17.2
"""

from __future__ import annotations

import socket

import pytest

from provenance.domain.errors import FailureCode, Result, failed
from provenance.domain.scan_budget import ResponseKind, ScanLimits
from provenance.domain.urls import AbsoluteHttpUrl, is_public_network_address
from provenance.infrastructure.network.pinned_transport import (
    DETAIL_PEER_NOT_PINNED,
    PinnedHttpTransport,
    TransportSettings,
)
from provenance.ports.dns import ResolvedAddress
from provenance.ports.http import (
    STAGE_ADDRESSES_VALIDATED,
    STAGE_CONNECTED,
    STAGE_DNS,
    STAGE_PEER_VERIFIED,
    STAGE_REQUEST_SENT,
    STAGE_RESPONSE_STARTED,
    SafeRequest,
)
from tests.network_support import (
    LOOPBACK,
    TEST_USER_AGENT,
    ManualClock,
    RecordingProbe,
    ScriptedRoute,
    StubResolver,
    allow_any_address,
    build_budget,
    build_transport,
    scripted_server,
)

pytestmark = pytest.mark.contract


def _request(url: AbsoluteHttpUrl, kind: ResponseKind = ResponseKind.PAGE) -> SafeRequest:
    return SafeRequest(url=url, kind=kind)


def test_a_successful_fetch_returns_the_head_and_body() -> None:
    routes = {
        "/page": ScriptedRoute(body=b"<html>hello</html>", headers={"Content-Type": "text/html"})
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/page")), budget).unwrap()
        lease = budget.open_response(ResponseKind.PAGE)
        body = response.read_body(lease).unwrap()

        assert response.head.status == 200
        assert response.head.content_type == "text/html"
        assert response.head.peer_address == LOOPBACK
        assert response.head.declared_length == len(b"<html>hello</html>")
        assert body == b"<html>hello</html>"
        assert lease.bytes_read == len(body)
        assert budget.total_bytes == len(body)


def test_stages_occur_in_the_required_order() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        probe = RecordingProbe()
        transport = build_transport(clock, probe)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/page")), budget).unwrap()
        response.close()

        order = [
            STAGE_DNS,
            STAGE_ADDRESSES_VALIDATED,
            STAGE_CONNECTED,
            STAGE_PEER_VERIFIED,
            STAGE_REQUEST_SENT,
            STAGE_RESPONSE_STARTED,
        ]
        positions = [probe.index_of(stage) for stage in order]
        assert positions == sorted(positions)


def test_the_peer_is_verified_before_any_request_byte_is_written() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        probe = RecordingProbe()
        transport = build_transport(clock, probe)
        budget = build_budget(clock)

        transport.fetch(_request(server.url("/page")), budget).unwrap().close()

        assert probe.index_of(STAGE_PEER_VERIFIED) < probe.index_of(STAGE_REQUEST_SENT)
        assert probe.index_of(STAGE_CONNECTED) < probe.index_of(STAGE_PEER_VERIFIED)


def test_a_peer_outside_the_pinned_set_is_refused_before_sending() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        probe = RecordingProbe()
        # DNS claims one address while the socket will actually reach loopback.
        resolver = StubResolver(
            addresses=(ResolvedAddress(address="93.184.216.34", family=socket.AF_INET),),
            clock=clock,
        )
        transport = PinnedHttpTransport(
            resolver=resolver,
            settings=TransportSettings(user_agent=TEST_USER_AGENT),
            is_public=allow_any_address,
            probe=probe,
            socket_factory=lambda family: _RedirectingSocket(family, server.port),
        )
        budget = build_budget(clock)

        result = transport.fetch(_request(server.url("/page")), budget)
        failure = result.unwrap_failure()

        assert failure.code is FailureCode.PEER_MISMATCH
        assert failure.safe_detail == DETAIL_PEER_NOT_PINNED
        assert probe.occurred(STAGE_REQUEST_SENT) is False
        assert server.requests == []


def test_a_nonpublic_resolved_address_is_refused_before_connecting() -> None:
    clock = ManualClock()
    probe = RecordingProbe()
    resolver = StubResolver(
        addresses=(ResolvedAddress(address="10.0.0.9", family=socket.AF_INET),), clock=clock
    )
    transport = PinnedHttpTransport(
        resolver=resolver,
        settings=TransportSettings(user_agent=TEST_USER_AGENT),
        is_public=is_public_network_address,
        probe=probe,
    )
    budget = build_budget(clock)
    url = AbsoluteHttpUrl(scheme="http", host="private.example", port=80, path="/")

    result = transport.fetch(_request(url), budget)

    assert result.unwrap_failure().code is FailureCode.NONPUBLIC_ADDRESS
    assert probe.occurred(STAGE_CONNECTED) is False


def test_the_default_policy_refuses_loopback() -> None:
    # This is what keeps production safe while the other tests inject a permissive policy.
    assert is_public_network_address(LOOPBACK) is False
    assert is_public_network_address("::1") is False


def test_a_dns_failure_stops_the_attempt() -> None:
    clock = ManualClock()
    probe = RecordingProbe()
    resolver = StubResolver(
        addresses=(),
        clock=clock,
        failure=failed(FailureCode.DNS_NO_RECORDS, "resolve_host"),
    )
    transport = PinnedHttpTransport(
        resolver=resolver,
        settings=TransportSettings(user_agent=TEST_USER_AGENT),
        is_public=allow_any_address,
        probe=probe,
    )

    result: Result[object] = transport.fetch(
        _request(AbsoluteHttpUrl(scheme="http", host="nowhere.example", port=80, path="/")),
        build_budget(clock),
    )

    assert result.unwrap_failure().code is FailureCode.DNS_NO_RECORDS
    assert probe.occurred(STAGE_CONNECTED) is False


def test_redirects_are_not_followed_automatically() -> None:
    routes = {
        "/from": ScriptedRoute(status=302, body=b"", headers={"Location": "/to"}),
        "/to": ScriptedRoute(body=b"final"),
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/from")), budget).unwrap()
        response.close()

        assert response.head.status == 302
        assert response.head.location == "/to"
        assert response.head.is_redirect is True
        assert [path for path, _headers in server.requests] == ["/from"]


def test_the_declared_length_is_refused_before_the_body_is_read() -> None:
    routes = {"/big": ScriptedRoute(body=b"x" * 400, headers={"Content-Type": "image/png"})}
    with scripted_server(routes) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock, ScanLimits(image_bytes=100, total_bytes=10_000))

        response = transport.fetch(
            _request(server.url("/big"), ResponseKind.IMAGE), budget
        ).unwrap()
        lease = budget.open_response(ResponseKind.IMAGE)
        refused = lease.accept_declared_length(response.head.declared_length)
        response.close()

        assert refused.unwrap_failure().code is FailureCode.IMAGE_BYTES_LIMIT
        assert lease.bytes_read == 0
        assert budget.total_bytes == 0


def test_an_over_limit_body_is_refused_while_streaming() -> None:
    routes = {"/big": ScriptedRoute(body=b"y" * 900, omit_content_length=True)}
    with scripted_server(routes) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock, ScanLimits(image_bytes=200, total_bytes=10_000))

        response = transport.fetch(
            _request(server.url("/big"), ResponseKind.IMAGE), budget
        ).unwrap()
        lease = budget.open_response(ResponseKind.IMAGE)
        result = response.read_body(lease)

        assert result.unwrap_failure().code is FailureCode.IMAGE_BYTES_LIMIT
        assert lease.bytes_read <= 200
        assert budget.total_bytes <= 200


def test_streaming_charges_every_chunk_once() -> None:
    body = b"z" * 5_000
    with scripted_server({"/page": ScriptedRoute(body=body)}) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/page")), budget).unwrap()
        lease = budget.open_response(ResponseKind.PAGE)
        collected = b"".join(response.stream(lease))

        assert collected == body
        assert lease.bytes_read == len(body)
        assert budget.total_bytes == len(body)


def test_the_request_carries_the_provenance_user_agent() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        transport = build_transport(clock)

        transport.fetch(_request(server.url("/page")), build_budget(clock)).unwrap().close()

        _path, headers = server.requests[0]
        assert headers["User-Agent"] == TEST_USER_AGENT
        assert headers["Accept-Encoding"] == "identity"
        assert headers["Host"] == f"{LOOPBACK}:{server.port}"


def test_query_strings_reach_the_server_unchanged() -> None:
    with scripted_server({"/page?tag=Fine%20Art&b=2": ScriptedRoute(body=b"q")}) as server:
        clock = ManualClock()
        transport = build_transport(clock)

        response = transport.fetch(
            _request(server.url("/page?tag=Fine%20Art&b=2")), build_budget(clock)
        ).unwrap()
        response.close()

        assert response.head.status == 200
        assert server.requests[0][0] == "/page?tag=Fine%20Art&b=2"


def test_an_expired_budget_prevents_the_attempt() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)
        clock.advance(budget.limits.total_seconds)

        result = transport.fetch(_request(server.url("/page")), budget)

        assert result.unwrap_failure().code is FailureCode.SCAN_TIMEOUT
        assert server.requests == []


def test_an_expired_deadline_stops_the_stream_mid_body() -> None:
    # The body must exceed one read so the deadline can land between chunks.
    body_size = 40_000
    with scripted_server({"/page": ScriptedRoute(body=b"a" * body_size)}) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/page")), budget).unwrap()
        lease = budget.open_response(ResponseKind.PAGE)
        chunks: list[bytes] = []
        for chunk in response.stream(lease):
            chunks.append(chunk)
            clock.advance(budget.limits.total_seconds)  # time runs out mid-stream

        collected = b"".join(chunks)
        assert len(chunks) == 1
        assert len(collected) < body_size
        # Received bytes are charged even when the stream stops, so charged >= yielded.
        assert lease.bytes_read >= len(collected)
        assert budget.total_bytes == lease.bytes_read


def test_a_complete_body_streams_in_multiple_chunks() -> None:
    body_size = 40_000
    with scripted_server({"/page": ScriptedRoute(body=b"b" * body_size)}) as server:
        clock = ManualClock()
        transport = build_transport(clock)
        budget = build_budget(clock)

        response = transport.fetch(_request(server.url("/page")), budget).unwrap()
        lease = budget.open_response(ResponseKind.PAGE)
        collected = b"".join(response.stream(lease))

        assert len(collected) == body_size
        assert lease.bytes_read == body_size


def test_closing_twice_is_safe() -> None:
    with scripted_server({"/page": ScriptedRoute()}) as server:
        clock = ManualClock()
        transport = build_transport(clock)

        response = transport.fetch(_request(server.url("/page")), build_budget(clock)).unwrap()
        response.close()
        response.close()


class _RedirectingSocket(socket.socket):
    """A socket that connects to a different port than requested.

    Simulates DNS rebinding or a lying resolver: the address the policy approved is not
    the address the socket actually reaches.
    """

    def __init__(self, family: int, actual_port: int) -> None:
        super().__init__(family, socket.SOCK_STREAM)
        self._actual_port = actual_port

    def connect(self, address: object) -> None:
        super().connect((LOOPBACK, self._actual_port))
