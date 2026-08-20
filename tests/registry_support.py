"""Shared Registry helpers for integration and property tests.

Development-only. Each helper creates an isolated temporary registry so property
examples cannot leak state into one another.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from provenance.domain.models import (
    AssetHash,
    CreatorId,
    CreatorMetadata,
    MediaType,
    NormalizedUrl,
    PageContext,
    RegisterAsset,
    VerifiedDetection,
)
from provenance.domain.time import UtcTimestamp
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter

TABLES: Final = (
    "registered_assets",
    "incidents",
    "whitelist_entries",
    "audit_events",
    "operation_receipts",
    "schema_migrations",
)

DEFAULT_AT: Final = UtcTimestamp("2026-05-06T07:08:09Z")


@dataclass(frozen=True, slots=True)
class RegistryHarness:
    """A temporary registry with its transaction adapter."""

    registry: SqliteRegistry
    adapter: SqliteRegistryAdapter

    def snapshot(self) -> dict[str, list[tuple[object, ...]]]:
        """Full, order-independent contents of every table."""
        captured: dict[str, list[tuple[object, ...]]] = {}
        with self.registry.connect_for_read() as connection:
            for table in TABLES:
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                captured[table] = sorted(tuple(row) for row in rows)
        return captured

    def count(self, table: str) -> int:
        """Row count for one known table."""
        if table not in TABLES:
            message = f"unknown table: {table}"
            raise ValueError(message)
        with self.registry.connect_for_read() as connection:
            return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608

    def incident_keys(self) -> set[tuple[str, str, str]]:
        """Every incident deduplication key currently stored."""
        with self.registry.connect_for_read() as connection:
            rows = connection.execute(
                "SELECT asset_hash, page_url, image_url FROM incidents"
            ).fetchall()
        return {(str(row[0]), str(row[1]), str(row[2])) for row in rows}


@contextmanager
def temporary_registry() -> Iterator[RegistryHarness]:
    """Create an initialized registry in a fresh temporary directory."""
    with tempfile.TemporaryDirectory() as directory:
        registry = SqliteRegistry(Path(directory) / "registry.sqlite3")
        registry.initialize().unwrap()
        yield RegistryHarness(registry=registry, adapter=SqliteRegistryAdapter(registry))


def register_command(
    asset_hash: AssetHash,
    creator_id: CreatorId,
    *,
    at: UtcTimestamp = DEFAULT_AT,
    display_name: str = "Studio",
    contact_email: str | None = None,
    width: int = 8,
    height: int = 8,
) -> RegisterAsset:
    """Build a registration command with sensible defaults."""
    return RegisterAsset(
        asset_hash=asset_hash,
        creator_id=creator_id,
        registered_at=at,
        width=width,
        height=height,
        source_media_type=MediaType.PNG,
        metadata=CreatorMetadata(
            creator_id=creator_id,
            display_name=display_name,
            contact_email=contact_email,
        ),
    )


def detection(
    asset_hash: AssetHash,
    creator_id: CreatorId,
    page_url: NormalizedUrl,
    image_url: NormalizedUrl,
    *,
    at: UtcTimestamp = DEFAULT_AT,
    title: str | None = "Shop",
    crc32: int = 4242,
) -> VerifiedDetection:
    """Build a verified detection with sensible defaults."""
    return VerifiedDetection(
        asset_hash=asset_hash,
        creator_id=creator_id,
        page_url=page_url,
        image_url=image_url,
        payload_created_at=DEFAULT_AT,
        extraction_crc32=crc32,
        context=PageContext(title=title),
        discovered_at=at,
    )


def seed_asset(harness: RegistryHarness, asset_hash: AssetHash, creator_id: CreatorId) -> None:
    """Commit one registered asset."""
    with harness.adapter.begin("seed").unwrap() as uow:
        uow.assets.register_or_reuse(register_command(asset_hash, creator_id)).unwrap()
        uow.commit()
