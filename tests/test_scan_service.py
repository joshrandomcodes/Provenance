"""Scan orchestration end to end against a local scripted server and temporary registry.

Requirements: 1.5, 1.6, 8.1-8.13, 9.1-9.5, 10.1-10.7, 18.1-18.5, 18.9, 21.1, 21.5
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from provenance.application.cross_validation import DetectionCrossValidator
from provenance.application.image_analysis import ImageAnalyzer
from provenance.application.scan import (
    STATIC_HTML_LIMITATION,
    ImageOutcome,
    ProgressSink,
    ScanProgress,
    ScanReport,
    ScanRequest,
    ScanService,
    ScanStage,
)
from provenance.domain.cancellation import CooperativeCancellationToken
from provenance.domain.errors import FailureCode
from provenance.domain.models import (
    AssetHash,
    CreatorId,
    ExtractionKind,
    NormalizedUrl,
    PageContext,
    ScanTerminalReason,
    WatermarkPayload,
)
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.time import UtcTimestamp
from provenance.domain.watermark import embed_payload
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.network.pinned_transport import (
    PinnedHttpTransport,
    TransportSettings,
)
from provenance.infrastructure.network.redirects import RedirectFollower
from provenance.infrastructure.network.robots import RobotsDecision, RobotsGate, RobotsVerdict
from provenance.ports.registry import RegistryPort
from tests.network_support import (
    TEST_ALLOWED_PORTS,
    TEST_USER_AGENT,
    ManualClock,
    MappedResolver,
    ScriptedRoute,
    ScriptedServer,
    allow_any_address,
    port_forwarding_socket_factory,
    scripted_server,
    url_on_standard_port,
)
from tests.registry_support import RegistryHarness, seed_asset, temporary_registry

pytestmark = pytest.mark.integration

AGENT_TOKEN = "Provenance"  # noqa: S105 - a robots.txt product token, not a credential

HASH = AssetHash("e" * 64)
OTHER_HASH = AssetHash("f" * 64)
CREATOR = CreatorId("studio.one")
OTHER_CREATOR = CreatorId("studio.two")
PAYLOAD_AT = UtcTimestamp("2026-03-04T05:06:07Z")

ALLOW_ALL_ROBOTS = ScriptedRoute(body=b"User-agent: *\nDisallow:\n")
DENY_ALL_ROBOTS = ScriptedRoute(body=b"User-agent: *\nDisallow: /\n")
PNG_HEADERS = {"Content-Type": "image/png"}
HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _rgb(width: int = 400, height: int = 4, seed: int = 5) -> np.ndarray:
    generator = np.random.default_rng(seed=seed)
    return generator.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def _encode_png(rgb: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _clean_png(seed: int = 5) -> bytes:
    return _encode_png(_rgb(seed=seed))


def _watermarked_png(
    asset_hash: AssetHash = HASH, creator_id: CreatorId = CREATOR, *, seed: int = 5
) -> bytes:
    payload = WatermarkPayload(asset_hash=asset_hash, creator_id=creator_id, created_at=PAYLOAD_AT)
    embedded = embed_payload(_rgb(seed=seed), None, payload).unwrap()
    return _encode_png(embedded.rgb)


def _page(*sources: str) -> ScriptedRoute:
    """A product-listing page: each image sits in a figure with its own price.

    The price is inside the figure on purpose. Commerce indicators are scoped to the page
    context and the element containing the image, so a price in a sibling block would
    correctly not be attributed to this image.
    """
    body = "<html><head><title>Shop</title></head><body><h1>Prints</h1>"
    body += "".join(
        f'<figure><img src="{source}" alt="Print"><p>Price: $250.00</p></figure>'
        for source in sources
    )
    body += "</body></html>"
    return ScriptedRoute(body=body.encode(), headers=HTML_HEADERS)


def _service(server: ScriptedServer, clock: ManualClock, registry: RegistryPort) -> ScanService:
    transport = PinnedHttpTransport(
        resolver=MappedResolver(clock=clock, mapping={}),
        settings=TransportSettings(user_agent=TEST_USER_AGENT),
        is_public=allow_any_address,
        socket_factory=port_forwarding_socket_factory(server.port),
    )
    follower = RedirectFollower(transport, allowed_ports=TEST_ALLOWED_PORTS)
    return ScanService(
        follower,
        RobotsGate(follower, AGENT_TOKEN),
        ImageAnalyzer(PillowImageDecoder()),
        DetectionCrossValidator(registry, clock),
        allowed_ports=TEST_ALLOWED_PORTS,
    )


def _request(
    *,
    acknowledged: bool = True,
    approved: bool = False,
    progress: ProgressSink | None = None,
) -> ScanRequest:
    return ScanRequest(
        page_url=url_on_standard_port("/gallery"),
        acknowledged=acknowledged,
        robots_unavailable_approved=approved,
        progress=progress,
    )


def _scan(
    server: ScriptedServer,
    harness: RegistryHarness,
    *,
    limits: ScanLimits | None = None,
    token: CooperativeCancellationToken | None = None,
    request: ScanRequest | None = None,
) -> ScanReport:
    clock = ManualClock()
    budget = ScanBudget(limits or ScanLimits(), clock, token)
    service = _service(server, clock, harness.adapter)
    scan_request = request if request is not None else _request()
    robots = service.evaluate_robots(scan_request, budget).unwrap()
    return service.run(scan_request, budget, robots=robots).unwrap()


def _paths(server: ScriptedServer) -> list[str]:
    return [path for path, _headers in server.requests]


def test_an_unacknowledged_scan_is_refused_before_any_request() -> None:
    with scripted_server({"/robots.txt": ALLOW_ALL_ROBOTS}) as server, temporary_registry() as h:
        clock = ManualClock()
        budget = ScanBudget(ScanLimits(), clock, None)
        service = _service(server, clock, h.adapter)

        result = service.evaluate_robots(_request(acknowledged=False), budget)

        assert result.unwrap_failure().code is FailureCode.MISSING_ACKNOWLEDGEMENT
        assert server.requests == []


def test_run_also_refuses_an_unacknowledged_request() -> None:
    with scripted_server({"/robots.txt": ALLOW_ALL_ROBOTS}) as server, temporary_registry() as h:
        clock = ManualClock()
        budget = ScanBudget(ScanLimits(), clock, None)
        service = _service(server, clock, h.adapter)
        decision = RobotsDecision(verdict=RobotsVerdict.ALLOWED, robots_url="x", status=200)

        result = service.run(_request(acknowledged=False), budget, robots=decision)

        assert result.unwrap_failure().code is FailureCode.MISSING_ACKNOWLEDGEMENT
        assert server.requests == []


def test_robots_is_consulted_before_the_page() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS, "/gallery": _page()}
    with scripted_server(routes) as server, temporary_registry() as harness:
        _scan(server, harness)

        assert _paths(server)[:2] == ["/robots.txt", "/gallery"]


def test_a_disallowed_page_is_never_fetched() -> None:
    routes = {"/robots.txt": DENY_ALL_ROBOTS, "/gallery": _page("/a.png")}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.terminal_reason is ScanTerminalReason.ROBOTS_DISALLOWED
        assert report.outcomes == ()
        assert _paths(server) == ["/robots.txt"]


def test_unavailable_robots_without_approval_stops_the_scan() -> None:
    routes = {"/robots.txt": ScriptedRoute(status=503, body=b"down"), "/gallery": _page("/a.png")}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.terminal_reason is ScanTerminalReason.ROBOTS_DECLINED
        assert _paths(server) == ["/robots.txt"]


def test_unavailable_robots_with_explicit_approval_continues() -> None:
    routes = {
        "/robots.txt": ScriptedRoute(status=503, body=b"down"),
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_clean_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness, request=_request(approved=True))

        assert report.terminal_reason is ScanTerminalReason.COMPLETED
        assert "/gallery" in _paths(server)
        assert report.summary.attempted == 1


def test_a_registered_watermarked_image_produces_one_verified_incident() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)

        assert report.summary.verified == 1
        assert len(report.verified) == 1
        outcome = report.outcomes[0]
        assert outcome.kind is ExtractionKind.VERIFIED
        assert outcome.incident_id is not None
        assert harness.count("incidents") == 1


def test_the_incident_carries_the_page_context_from_discovery() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)
        context = report.outcomes[0].context

        assert context.title == "Shop"
        assert context.heading == "Prints"
        assert context.alt == "Print"
        assert any("$250.00" in item for item in context.ecommerce_evidence)


def test_an_unregistered_watermark_creates_no_incident() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_watermarked_png(OTHER_HASH), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)

        assert report.outcomes[0].kind is ExtractionKind.UNREGISTERED
        assert report.summary.unregistered == 1
        assert harness.count("incidents") == 0


def test_a_watermark_naming_another_creator_creates_no_incident() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_watermarked_png(HASH, OTHER_CREATOR), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)

        assert report.outcomes[0].kind is ExtractionKind.UNREGISTERED
        assert harness.count("incidents") == 0


def test_an_unwatermarked_image_is_reported_as_such() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_clean_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)

        assert report.outcomes[0].kind is ExtractionKind.NO_WATERMARK
        assert report.summary.no_watermark == 1
        assert harness.count("incidents") == 0


def test_a_non_image_response_is_a_labelled_failure() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=b"<html>nope</html>", headers=HTML_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)
        outcome = report.outcomes[0]

        assert outcome.kind is ExtractionKind.FAILED
        assert outcome.failure_code is FailureCode.UNSUPPORTED_MEDIA_TYPE
        assert report.summary.failed == 1


def test_a_missing_image_is_a_labelled_failure() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS, "/gallery": _page("/missing.png")}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.outcomes[0].failure_code is FailureCode.HTTP_STATUS


def test_one_image_failure_preserves_the_incident_from_another() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/bad.png", "/good.png"),
        "/bad.png": ScriptedRoute(body=b"not an image", headers=PNG_HEADERS),
        "/good.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)
        kinds = [outcome.kind for outcome in report.outcomes]

        assert kinds == [ExtractionKind.FAILED, ExtractionKind.VERIFIED]
        assert harness.count("incidents") == 1


def test_outcomes_follow_document_order() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/one.png", "/two.png", "/three.png"),
        "/one.png": ScriptedRoute(body=_clean_png(seed=1), headers=PNG_HEADERS),
        "/two.png": ScriptedRoute(body=_clean_png(seed=2), headers=PNG_HEADERS),
        "/three.png": ScriptedRoute(body=_clean_png(seed=3), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert [str(outcome.image_url) for outcome in report.outcomes] == [
            "http://127.0.0.1/one.png",
            "http://127.0.0.1/two.png",
            "http://127.0.0.1/three.png",
        ]


def test_a_failed_page_records_no_images_and_reports_the_failure() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.terminal_reason is ScanTerminalReason.PAGE_FAILURE
        assert report.page_failure is not None
        assert report.page_failure.code is FailureCode.HTTP_STATUS
        assert report.outcomes == ()
        assert report.summary.discovered == 0


def test_candidates_beyond_the_unique_image_cap_are_dropped_and_disclosed() -> None:
    """Capped candidates are never retained, so they are reported separately.

    Requirement 18.4 defines "discovered" as a unique *retained* URL, so the summary
    counts one discovered image here. The dropped candidates surface as `capped` rather
    than vanishing.
    """
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/one.png", "/two.png", "/three.png"),
        "/one.png": ScriptedRoute(body=_clean_png(seed=1), headers=PNG_HEADERS),
        "/two.png": ScriptedRoute(body=_clean_png(seed=2), headers=PNG_HEADERS),
        "/three.png": ScriptedRoute(body=_clean_png(seed=3), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness, limits=ScanLimits(unique_images=1))

        assert report.summary.discovered == 1
        assert report.summary.attempted == 1
        assert report.summary.skipped == 0
        assert report.capped == 2
        assert len(report.outcomes) == 1
        assert _paths(server).count("/two.png") == 0


def test_cancelling_mid_scan_preserves_completed_results_and_skips_the_rest() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/one.png", "/two.png", "/three.png"),
        "/one.png": ScriptedRoute(body=_clean_png(seed=1), headers=PNG_HEADERS),
        "/two.png": ScriptedRoute(body=_clean_png(seed=2), headers=PNG_HEADERS),
        "/three.png": ScriptedRoute(body=_clean_png(seed=3), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        token = CooperativeCancellationToken()
        clock = ManualClock()
        budget = ScanBudget(ScanLimits(), clock, token)
        service = _service(server, clock, harness.adapter)

        def cancel_after_first(snapshot: ScanProgress) -> None:
            if snapshot.stage is ScanStage.IMAGES and snapshot.completed == 1:
                token.cancel()

        request = _request(progress=cancel_after_first)
        robots = service.evaluate_robots(request, budget).unwrap()
        report = service.run(request, budget, robots=robots).unwrap()

        assert report.terminal_reason is ScanTerminalReason.CANCELLED
        assert report.outcomes[0].kind is ExtractionKind.NO_WATERMARK
        assert report.outcomes[1].kind is ExtractionKind.CANCELLED
        assert report.summary.skipped == 1
        assert len(report.skipped_urls) == 1
        assert _paths(server).count("/three.png") == 0


def test_cancellation_before_the_page_is_reported_as_cancelled() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS, "/gallery": _page("/a.png")}
    with scripted_server(routes) as server, temporary_registry() as harness:
        token = CooperativeCancellationToken()
        clock = ManualClock()
        budget = ScanBudget(ScanLimits(), clock, token)
        service = _service(server, clock, harness.adapter)
        request = _request()
        robots = service.evaluate_robots(request, budget).unwrap()
        token.cancel()

        report = service.run(request, budget, robots=robots).unwrap()

        assert report.terminal_reason is ScanTerminalReason.CANCELLED
        assert report.outcomes == ()


def test_every_attempted_image_has_exactly_one_terminal_category() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/good.png", "/bad.png", "/missing.png"),
        "/good.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
        "/bad.png": ScriptedRoute(body=b"junk", headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)
        summary = report.summary
        counted = (
            summary.verified
            + summary.no_watermark
            + summary.corrupt
            + summary.unregistered
            + summary.failed
            + summary.cancelled
        )

        assert summary.attempted == 3
        assert counted == summary.attempted
        assert len(report.outcomes) == summary.attempted


def test_the_summary_accounts_for_every_response_byte() -> None:
    robots_body = b"User-agent: *\nDisallow:\n"
    image = _clean_png()
    page = _page("/a.png")
    routes = {
        "/robots.txt": ScriptedRoute(body=robots_body),
        "/gallery": page,
        "/a.png": ScriptedRoute(body=image, headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.summary.total_response_bytes == len(robots_body) + len(page.body) + len(image)


def test_the_summary_discloses_the_static_html_limitation() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS, "/gallery": _page()}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.limitation == STATIC_HTML_LIMITATION
        assert "JavaScript" in report.limitation


def test_a_page_with_no_images_completes_cleanly() -> None:
    routes = {"/robots.txt": ALLOW_ALL_ROBOTS, "/gallery": _page()}
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.is_complete is True
        assert report.summary.discovered == 0
        assert report.outcomes == ()


def test_progress_snapshots_follow_the_scan_stages() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_clean_png(), headers=PNG_HEADERS),
    }
    seen: list[ScanStage] = []
    with scripted_server(routes) as server, temporary_registry() as harness:
        request = _request(progress=lambda snapshot: seen.append(snapshot.stage))
        _scan(server, harness, request=request)

    assert seen[0] is ScanStage.ROBOTS
    assert ScanStage.PAGE in seen
    assert ScanStage.DISCOVERY in seen
    assert ScanStage.IMAGES in seen
    assert seen[-1] is ScanStage.FINISHED


def test_a_second_scan_refreshes_one_incident_rather_than_adding_another() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        first = _scan(server, harness)
        second = _scan(server, harness)

        assert harness.count("incidents") == 1
        assert first.outcomes[0].incident_id == second.outcomes[0].incident_id


def test_duplicate_sources_on_one_page_are_scanned_once() -> None:
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png", "/a.png"),
        "/a.png": ScriptedRoute(body=_clean_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        report = _scan(server, harness)

        assert report.summary.discovered == 1
        assert len(report.outcomes) == 1
        assert _paths(server).count("/a.png") == 1


def test_no_image_bytes_reach_the_registry_file() -> None:
    """The Registry stores URLs, hashes, and context. Never pixels or encoded bytes.

    Checked against the database file itself rather than through queries, so a stray BLOB
    in any column would still be caught.
    """
    image = _watermarked_png()
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": _page("/a.png"),
        "/a.png": ScriptedRoute(body=image, headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        report = _scan(server, harness)
        assert report.summary.verified == 1

        stored = harness.registry.path.read_bytes()

        assert PNG_MAGIC not in stored
        assert image[:128] not in stored
        assert image[-128:] not in stored
        # No column in the schema is a BLOB, so no cell should hold raw bytes.
        for rows in harness.snapshot().values():
            for row in rows:
                assert not any(isinstance(cell, bytes) for cell in row)


def test_the_page_html_is_not_stored_either() -> None:
    page = _page("/a.png")
    routes = {
        "/robots.txt": ALLOW_ALL_ROBOTS,
        "/gallery": page,
        "/a.png": ScriptedRoute(body=_watermarked_png(), headers=PNG_HEADERS),
    }
    with scripted_server(routes) as server, temporary_registry() as harness:
        seed_asset(harness, HASH, CREATOR)

        _scan(server, harness)
        stored = harness.registry.path.read_bytes()

        # Bounded context is stored; the raw document is not.
        assert b"<html>" not in stored
        assert b"<figure>" not in stored
        assert page.body not in stored


def test_a_verified_kind_without_an_incident_is_not_treated_as_verified() -> None:
    """Defensive: the verified flag requires a real incident id, not just a kind."""
    outcome = ImageOutcome(
        image_url=NormalizedUrl("https://cdn.example/a.png"),
        kind=ExtractionKind.VERIFIED,
        context=PageContext(title="Shop"),
    )

    assert outcome.is_verified is False
