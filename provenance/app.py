"""Streamlit entry point and composition root.

Run with ``python scripts/run_app.py`` or
``python -m streamlit run provenance/app.py``.

Only production adapters are wired here. Test fixtures, fake clocks, mock resolvers,
and synthetic evidence providers must never be importable from this module.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.9-6.11, 17.1, 17.2
"""

from __future__ import annotations

from provenance.application.forge import ForgeService
from provenance.infrastructure.clock import SystemClock
from provenance.infrastructure.image_decoder import PillowImageDecoder
from provenance.infrastructure.png_codec import PillowPngEncoder
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter
from provenance.settings import RuntimeSettings, load_runtime_settings
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
    forge = ForgeService(
        decoder=PillowImageDecoder(),
        encoder=PillowPngEncoder(),
        registry=SqliteRegistryAdapter(registry),
        clock=clock,
    )
    return Dashboard(settings=settings, status=registry.status, forge=forge)


def main() -> None:
    """Compose and render the local dashboard."""
    render_dashboard(build_dashboard(build_runtime_settings()))


if __name__ == "__main__":
    main()
