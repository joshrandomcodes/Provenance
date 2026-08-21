"""Redirect revalidation and robots.txt handling against a local scripted server.

Requirements: 7.4, 7.6, 7.9, 8.1, 8.2, 8.3, 8.4, 8.6
"""

from __future__ import annotations

import socket

import pytest

from provenance.domain.errors import FailureCode
from provenance.domain.scan_budget import ResponseKind, ScanBudget
from provenance.infrastructure.network.pinned_transport import (
    PinnedHttpTransport,
    TransportSettings,
)
from provenance.infrastructure.network.redirects import (
    DETAIL_MISSING_LOCATION,
    RedirectFollower,
)
from provenance.infrastructure.network.robots import (
    RobotsGate,
    RobotsVerdict,
    robots_url_for,
)
from provenance.ports.http import SafeRequest, SafeResponse
from tests.network_support import (
    LOOPBACK,
    TEST_ALLOWED_PORTS,
    TEST_USER_AGENT,
    AddressPolicy,
    ManualClock,
    MappedResolver,
    ScriptedRoute,
    ScriptedServer,
    allow_any_address,
    allow_loopback_only,
    build_budget,
    port_forwarding_socket_factory,
    scripted_server,
    url_on_standard_port,
)

pytestmark = pytest.mark.contract

# The robots.txt product token for this crawler, not a credential.
AGENT_TOKEN = "Provenance"  # noqa: S105


def _follower(
    server: ScriptedServer,
    clock: ManualClock,
    *,
    is_public: AddressPolicy = allow_any_address,
    mapping: dict[str, tuple[str, ...]] | None = None,
) -> RedirectFollower:
    transport = PinnedHttpTransport(
        resolver=MappedResolver(clock=clock, mapping=mapping or {}),
        settings=TransportSettings(user_agent=TEST_USER_AGENT),
        is_public=is_public,
        socket_factory=port_forwarding_socket_factory(server.port),
    )
    return RedirectFollower(transport, allowed_ports=TEST_ALLOWED_PORTS)


def _page_request(path: str) -> SafeRequest:
    return SafeRequest(url=url_on_standard_port(path), kind=ResponseKind.PAGE)


def _read(response: SafeResponse, budget: ScanBudget) -> bytes:
    lease = budget.open_response(ResponseKind.PAGE)
    return response.read_body(lease).unwrap()


def _redirect_chain(length: int) -> dict[str, ScriptedRoute]:
    routes: dict[str, ScriptedRoute] = {}
    for index in range(length):
        routes[f"/hop{index}"] = ScriptedRoute(
            status=302, body=b"", headers={"Location": f"/hop{index + 1}"}
        )
    routes[f"/hop{length}"] = ScriptedRoute(body=b"final")
    return routes


def test_a_direct_response_needs_no_redirects() -> None:
    with scripted_server({"/page": ScriptedRoute(body=b"direct")}) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        outcome = _follower(server, clock).fetch(_page_request("/page"), budget).unwrap()

        assert outcome.redirect_count == 0
        assert outcome.hops == (url_on_standard_port("/page").normalized,)
        assert _read(outcome.response, budget) == b"direct"


def test_one_redirect_is_followed_to_its_target() -> None:
    routes = {
        "/from": ScriptedRoute(status=302, body=b"", headers={"Location": "/to"}),
        "/to": ScriptedRoute(body=b"arrived"),
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        outcome = _follower(server, clock).fetch(_page_request("/from"), budget).unwrap()

        assert outcome.redirect_count == 1
        assert outcome.final_url.path == "/to"
        assert _read(outcome.response, budget) == b"arrived"
        assert [path for path, _headers in server.requests] == ["/from", "/to"]


def test_five_redirects_are_permitted() -> None:
    with scripted_server(_redirect_chain(5)) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        outcome = _follower(server, clock).fetch(_page_request("/hop0"), budget).unwrap()

        assert outcome.redirect_count == 5
        assert _read(outcome.response, budget) == b"final"


def test_a_sixth_redirect_is_refused() -> None:
    with scripted_server(_redirect_chain(6)) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        result = _follower(server, clock).fetch(_page_request("/hop0"), budget)

        assert result.unwrap_failure().code is FailureCode.REDIRECT_LIMIT
        # Six hops were requested; the seventh was never attempted.
        assert len(server.requests) == 6


def test_a_redirect_without_a_location_is_malformed() -> None:
    with scripted_server({"/page": ScriptedRoute(status=302, body=b"")}) as server:
        clock = ManualClock()

        result = _follower(server, clock).fetch(_page_request("/page"), build_budget(clock))
        failure = result.unwrap_failure()

        assert failure.code is FailureCode.HTTP_STATUS
        assert failure.safe_detail == DETAIL_MISSING_LOCATION


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("ftp://example.com/a", FailureCode.UNSUPPORTED_SCHEME),
        ("file:///etc/passwd", FailureCode.UNSUPPORTED_SCHEME),
        ("http://user:pass@example.com/", FailureCode.CREDENTIALS),
        ("http://exa mple.com/", FailureCode.MALFORMED_HOST),
    ],
)
def test_an_unsafe_redirect_destination_is_refused(location: str, code: FailureCode) -> None:
    routes = {"/page": ScriptedRoute(status=302, body=b"", headers={"Location": location})}
    with scripted_server(routes) as server:
        clock = ManualClock()

        result = _follower(server, clock).fetch(_page_request("/page"), build_budget(clock))

        assert result.unwrap_failure().code is code


def test_a_redirect_to_a_private_address_is_refused() -> None:
    routes = {
        "/page": ScriptedRoute(
            status=302, body=b"", headers={"Location": "http://private.example/secret"}
        )
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        follower = _follower(
            server,
            clock,
            is_public=allow_loopback_only,
            mapping={"private.example": ("10.0.0.9",)},
        )

        result = follower.fetch(_page_request("/page"), build_budget(clock))

        assert result.unwrap_failure().code is FailureCode.NONPUBLIC_ADDRESS
        # The private host was never contacted.
        assert [path for path, _headers in server.requests] == ["/page"]


def test_redirect_bodies_are_charged_and_discarded() -> None:
    routes = {
        "/from": ScriptedRoute(status=302, body=b"x" * 300, headers={"Location": "/to"}),
        "/to": ScriptedRoute(body=b"y" * 100),
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        outcome = _follower(server, clock).fetch(_page_request("/from"), budget).unwrap()
        final_body = _read(outcome.response, budget)

        assert final_body == b"y" * 100
        # The discarded redirect body still counted toward the scan total.
        assert budget.total_bytes == 400


def test_no_cookie_or_authorization_header_crosses_a_hop() -> None:
    routes = {
        "/from": ScriptedRoute(
            status=302,
            body=b"",
            headers={"Location": "/to", "Set-Cookie": "session=secret; Path=/"},
        ),
        "/to": ScriptedRoute(body=b"arrived"),
    }
    with scripted_server(routes) as server:
        clock = ManualClock()

        _follower(server, clock).fetch(_page_request("/from"), build_budget(clock)).unwrap()

        for _path, headers in server.requests:
            assert "Cookie" not in headers
            assert "Authorization" not in headers
            assert headers["User-Agent"] == TEST_USER_AGENT


def _gate(
    server: ScriptedServer,
    clock: ManualClock,
    *,
    mapping: dict[str, tuple[str, ...]] | None = None,
) -> RobotsGate:
    return RobotsGate(_follower(server, clock, mapping=mapping), AGENT_TOKEN)


def test_robots_url_uses_the_origin_only() -> None:
    page = url_on_standard_port("/gallery/piece?ref=1")

    robots = robots_url_for(page)

    assert robots.path == "/robots.txt"
    assert robots.query == ""
    assert robots.host == page.host
    assert robots.scheme == page.scheme


def test_an_allow_all_robots_file_permits_the_page() -> None:
    routes = {"/robots.txt": ScriptedRoute(body=b"User-agent: *\nDisallow:\n")}
    with scripted_server(routes) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.ALLOWED
        assert decision.may_continue is True
        assert decision.status == 200


def test_a_disallow_all_robots_file_blocks_the_page() -> None:
    routes = {"/robots.txt": ScriptedRoute(body=b"User-agent: *\nDisallow: /\n")}
    with scripted_server(routes) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.DISALLOWED
        assert decision.may_continue is False
        assert decision.matched_rule is not None


def test_rules_for_another_agent_do_not_block_us() -> None:
    routes = {"/robots.txt": ScriptedRoute(body=b"User-agent: SomeOtherBot\nDisallow: /\n")}
    with scripted_server(routes) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.ALLOWED


def test_a_named_disallow_for_our_agent_blocks_the_page() -> None:
    routes = {
        "/robots.txt": ScriptedRoute(
            body=f"User-agent: {AGENT_TOKEN}\nDisallow: /private\n".encode()
        )
    }
    with scripted_server(routes) as server:
        clock = ManualClock()
        gate = _gate(server, clock)

        blocked = gate.evaluate(url_on_standard_port("/private/x"), build_budget(clock)).unwrap()
        allowed = gate.evaluate(url_on_standard_port("/public/x"), build_budget(clock)).unwrap()

        assert blocked.verdict is RobotsVerdict.DISALLOWED
        assert allowed.verdict is RobotsVerdict.ALLOWED


def test_a_missing_robots_file_allows_the_page() -> None:
    with scripted_server({}) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.ALLOWED
        assert decision.status == 404
        assert decision.detail == "no_rules_published"


def test_a_server_error_requires_a_user_decision() -> None:
    routes = {"/robots.txt": ScriptedRoute(status=503, body=b"unavailable")}
    with scripted_server(routes) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.UNAVAILABLE
        assert decision.needs_user_decision is True
        assert decision.status == 503


def test_a_transport_failure_requires_a_user_decision() -> None:
    with scripted_server({}) as server:
        closed_port = _free_port()
        clock = ManualClock()
        transport = PinnedHttpTransport(
            resolver=MappedResolver(clock=clock, mapping={}),
            settings=TransportSettings(user_agent=TEST_USER_AGENT),
            is_public=allow_any_address,
            socket_factory=port_forwarding_socket_factory(closed_port),
        )
        gate = RobotsGate(
            RedirectFollower(transport, allowed_ports=TEST_ALLOWED_PORTS), AGENT_TOKEN
        )

        decision = gate.evaluate(url_on_standard_port("/gallery"), build_budget(clock)).unwrap()

        assert decision.verdict is RobotsVerdict.UNAVAILABLE
        assert decision.needs_user_decision is True
        assert server.requests == []


def test_a_redirected_robots_file_is_followed() -> None:
    routes = {
        "/robots.txt": ScriptedRoute(status=301, body=b"", headers={"Location": "/rules.txt"}),
        "/rules.txt": ScriptedRoute(body=b"User-agent: *\nDisallow: /\n"),
    }
    with scripted_server(routes) as server:
        clock = ManualClock()

        decision = (
            _gate(server, clock)
            .evaluate(url_on_standard_port("/gallery"), build_budget(clock))
            .unwrap()
        )

        assert decision.verdict is RobotsVerdict.DISALLOWED
        assert [path for path, _headers in server.requests] == ["/robots.txt", "/rules.txt"]


def test_the_robots_body_is_charged_to_the_budget() -> None:
    body = b"User-agent: *\nDisallow:\n"
    with scripted_server({"/robots.txt": ScriptedRoute(body=body)}) as server:
        clock = ManualClock()
        budget = build_budget(clock)

        _gate(server, clock).evaluate(url_on_standard_port("/gallery"), budget).unwrap()

        assert budget.total_bytes == len(body)


def _free_port() -> int:
    """Bind and release a port so connections to it are refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])
