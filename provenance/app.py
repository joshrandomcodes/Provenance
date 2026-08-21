"""Streamlit entry point and composition root.

Run with ``python scripts/run_app.py`` or
``python -m streamlit run provenance/app.py``.

Only production adapters are wired here. Test fixtures, fake clocks, mock resolvers,
and synthetic evidence providers must never be importable from this module.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.9-6.11, 17.1, 17.2
"""

from __future__ import annotations

from provenance.application.cross_validation import DetectionCrossValidator
from provenance.application.forge import ForgeService
from provenance.application.image_analysis import ImageAnalyzer
from provenance.application.operations import MaterialActionRunner
from provenance.application.scan import ScanService
from provenance.application.triage import TriageService
from provenance.infrastructure.clock import SystemClock
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.network.pinned_transport import (
    PinnedHttpTransport,
    TransportSettings,
)
from provenance.infrastructure.network.redirects import RedirectFollower
from provenance.infrastructure.network.resolver import PublicOnlyResolver
from provenance.infrastructure.network.robots import RobotsGate
from provenance.infrastructure.png_codec import PillowPngEncoder
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter
from provenance.settings import APPLICATION_NAME, RuntimeSettings, load_runtime_settings
from provenance.ui.dashboard import Dashboard, render_dashboard


def build_runtime_settings() -> RuntimeSettings:
    """Resolve settings and create the local application directory."""
    settings = load_runtime_settings()
    settings.ensure_directories()
    return settings


def build_dashboard(settings: RuntimeSettings) -> Dashboard:
    """Wire production adapters and services.

    Startup checks run here. When they fail the dashboard still renders, with writes
    disabled and recovery guidance shown, rather than crashing.
    """
    registry = SqliteRegistry(settings.registry_path)
    registry.initialize()

    clock = SystemClock()
    adapter = SqliteRegistryAdapter(registry)

    forge = ForgeService(
        decoder=PillowImageDecoder(),
        encoder=PillowPngEncoder(),
        registry=adapter,
        clock=clock,
    )
    return Dashboard(
        settings=settings,
        status=registry.status,
        forge=forge,
        scanner=build_scanner(settings, adapter, clock),
        triage=TriageService(adapter, MaterialActionRunner(adapter, clock), clock),
        clock=clock,
    )


def build_scanner(
    settings: RuntimeSettings, adapter: SqliteRegistryAdapter, clock: SystemClock
) -> ScanService:
    """Wire the bounded, peer-verified scanning stack.

    Every outbound request goes through the pinned transport, so DNS is resolved
    immediately before each attempt and the peer is verified before any request byte is
    written. Environment proxies are never consulted.
    """
    transport = PinnedHttpTransport(
        resolver=PublicOnlyResolver(clock),
        settings=TransportSettings(user_agent=settings.user_agent),
    )
    follower = RedirectFollower(transport)
    return ScanService(
        follower,
        RobotsGate(follower, APPLICATION_NAME),
        ImageAnalyzer(PillowImageDecoder()),
        DetectionCrossValidator(adapter, clock),
    )


def main() -> None:
    """Compose and render the local dashboard."""
    render_dashboard(build_dashboard(build_runtime_settings()))


if __name__ == "__main__":
    main()
