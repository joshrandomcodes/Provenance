"""Ordered, checksum-verified SQLite migrations for the local Registry.

Each migration is an immutable, ordered list of statements with a SHA-256 checksum
recorded at apply time. On every startup the recorded checksums are re-verified, so an
edited migration or a database written by a newer build is refused instead of being
silently reinterpreted.

Schema notes:

* Tables are ``STRICT`` so column types are enforced by SQLite.
* URL identity uses the default ``BINARY`` collation, preserving path case and query
  bytes for exact whitelist scope comparison.
* ``audit_events`` deliberately has no foreign key to ``registered_assets`` so audit
  history survives asset deletion as a tombstone reference.

Requirements: 6.1-6.6, 6.9, 6.12, 10.5-10.7, 12.1, 12.6, 17.8, 18.7, 18.8
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok

SCHEMA_MIGRATIONS_DDL: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER NOT NULL PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  CHECK (length(checksum) = 64),
  CHECK (length(name) BETWEEN 1 AND 100)
) STRICT
"""

_REGISTERED_ASSETS: Final = """
CREATE TABLE registered_assets (
  asset_hash TEXT NOT NULL PRIMARY KEY,
  creator_id TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  source_media_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  contact_email TEXT,
  postal_address TEXT,
  rights_statement TEXT,
  CHECK (length(asset_hash) = 64 AND asset_hash NOT GLOB '*[^0-9a-f]*'),
  CHECK (length(creator_id) BETWEEN 1 AND 64 AND creator_id NOT GLOB '*[^A-Za-z0-9._-]*'),
  CHECK (length(registered_at) = 20 AND registered_at LIKE '____-__-__T__:__:__Z'),
  CHECK (width >= 1 AND height >= 1 AND width * height <= 40000000),
  CHECK (source_media_type IN ('image/png', 'image/jpeg')),
  CHECK (length(display_name) BETWEEN 1 AND 200 AND instr(display_name, char(0)) = 0),
  CHECK (
    contact_email IS NULL
    OR (length(contact_email) BETWEEN 3 AND 254 AND instr(contact_email, char(0)) = 0)
  ),
  CHECK (
    postal_address IS NULL
    OR (length(postal_address) <= 500 AND instr(postal_address, char(0)) = 0)
  ),
  CHECK (
    rights_statement IS NULL
    OR (length(rights_statement) <= 500 AND instr(rights_statement, char(0)) = 0)
  )
) STRICT
"""

_INCIDENTS: Final = """
CREATE TABLE incidents (
  id INTEGER PRIMARY KEY,
  asset_hash TEXT NOT NULL REFERENCES registered_assets(asset_hash) ON DELETE CASCADE,
  page_url TEXT NOT NULL,
  image_url TEXT NOT NULL,
  creator_id_evidence TEXT NOT NULL,
  payload_created_at TEXT NOT NULL,
  extraction_crc32 INTEGER NOT NULL,
  context_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL,
  CHECK (length(page_url) BETWEEN 1 AND 2048),
  CHECK (length(image_url) BETWEEN 1 AND 2048),
  CHECK (length(creator_id_evidence) BETWEEN 1 AND 64),
  CHECK (length(payload_created_at) = 20),
  CHECK (extraction_crc32 BETWEEN 0 AND 4294967295),
  CHECK (json_valid(context_json)),
  CHECK (length(first_seen_at) = 20 AND length(last_seen_at) = 20),
  CHECK (status IN ('Detected', 'Strike Authorized', 'Fair Use', 'Credit Requested')),
  UNIQUE (asset_hash, page_url, image_url)
) STRICT
"""

_WHITELIST_ENTRIES: Final = """
CREATE TABLE whitelist_entries (
  id INTEGER PRIMARY KEY,
  asset_hash TEXT NOT NULL REFERENCES registered_assets(asset_hash) ON DELETE CASCADE,
  page_url TEXT NOT NULL,
  rationale TEXT NOT NULL,
  created_at TEXT NOT NULL,
  modified_at TEXT NOT NULL,
  related_incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
  CHECK (length(page_url) BETWEEN 1 AND 2048),
  CHECK (length(rationale) BETWEEN 1 AND 500 AND instr(rationale, char(0)) = 0),
  CHECK (length(created_at) = 20 AND length(modified_at) = 20),
  UNIQUE (asset_hash, page_url)
) STRICT
"""

_AUDIT_EVENTS: Final = """
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  operation_key TEXT NOT NULL UNIQUE,
  asset_hash_tombstone TEXT,
  incident_id INTEGER,
  whitelist_id INTEGER,
  previous_statuses_json TEXT NOT NULL,
  new_statuses_json TEXT NOT NULL,
  content_hash TEXT,
  recipient TEXT,
  CHECK (length(event_type) BETWEEN 1 AND 64),
  CHECK (length(occurred_at) = 20),
  CHECK (length(operation_key) = 64 AND operation_key NOT GLOB '*[^0-9a-f]*'),
  CHECK (asset_hash_tombstone IS NULL OR length(asset_hash_tombstone) = 64),
  CHECK (json_valid(previous_statuses_json)),
  CHECK (json_valid(new_statuses_json)),
  CHECK (content_hash IS NULL OR length(content_hash) = 64),
  CHECK (recipient IS NULL OR length(recipient) BETWEEN 3 AND 254)
) STRICT
"""

_OPERATION_RECEIPTS: Final = """
CREATE TABLE operation_receipts (
  operation_key TEXT NOT NULL PRIMARY KEY,
  operation_type TEXT NOT NULL,
  target_ids_json TEXT NOT NULL,
  requested_values_hash TEXT NOT NULL,
  outcome_json TEXT NOT NULL,
  committed_at TEXT NOT NULL,
  audit_event_id INTEGER REFERENCES audit_events(id),
  CHECK (length(operation_key) = 64 AND operation_key NOT GLOB '*[^0-9a-f]*'),
  CHECK (length(operation_type) BETWEEN 1 AND 64),
  CHECK (json_valid(target_ids_json)),
  CHECK (length(requested_values_hash) = 64),
  CHECK (json_valid(outcome_json)),
  CHECK (length(committed_at) = 20)
) STRICT
"""

_INDEXES: Final = (
    """
    CREATE INDEX idx_incidents_active ON incidents(status, last_seen_at DESC)
      WHERE status IN ('Detected', 'Strike Authorized', 'Credit Requested')
    """,
    "CREATE INDEX idx_incidents_asset_page ON incidents(asset_hash, page_url)",
    "CREATE INDEX idx_incidents_last_seen ON incidents(last_seen_at DESC)",
    "CREATE INDEX idx_whitelist_incident ON whitelist_entries(related_incident_id)",
    "CREATE INDEX idx_audit_asset_time ON audit_events(asset_hash_tombstone, occurred_at DESC)",
    "CREATE INDEX idx_audit_incident_time ON audit_events(incident_id, occurred_at DESC)",
)


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """SHA-256 over the exact statement text, used to detect edits."""
        joined = "\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


MIGRATIONS: Final = (
    Migration(
        version=1,
        name="initial_registry_schema",
        statements=(
            _REGISTERED_ASSETS,
            _INCIDENTS,
            _WHITELIST_ENTRIES,
            _AUDIT_EVENTS,
            _OPERATION_RECEIPTS,
            *_INDEXES,
        ),
    ),
)

LATEST_VERSION: Final = max(migration.version for migration in MIGRATIONS)

MIGRATE_OPERATION: Final = "apply_migrations"
DETAIL_CHECKSUM_MISMATCH: Final = "migration_checksum_mismatch"
DETAIL_UNKNOWN_VERSION: Final = "database_newer_than_application"
DETAIL_APPLY_FAILED: Final = "migration_apply_failed"


def apply_migrations(connection: sqlite3.Connection, applied_at: str) -> Result[int]:
    """Apply every pending migration and verify already-applied checksums.

    Returns the resulting schema version.
    """
    try:
        connection.execute(SCHEMA_MIGRATIONS_DDL)
        recorded = {
            int(row[0]): (str(row[1]), str(row[2]))
            for row in connection.execute("SELECT version, name, checksum FROM schema_migrations")
        }
    except sqlite3.DatabaseError:
        return failed(FailureCode.CHECKS_FAILED, MIGRATE_OPERATION, safe_detail=DETAIL_APPLY_FAILED)

    known_versions = {migration.version for migration in MIGRATIONS}
    unknown = sorted(version for version in recorded if version not in known_versions)
    if unknown:
        return failed(
            FailureCode.CHECKS_FAILED,
            MIGRATE_OPERATION,
            safe_detail=f"{DETAIL_UNKNOWN_VERSION}:{unknown[0]}",
        )

    for migration in sorted(MIGRATIONS, key=lambda item: item.version):
        existing = recorded.get(migration.version)
        if existing is not None:
            if existing[1] != migration.checksum:
                return failed(
                    FailureCode.CHECKS_FAILED,
                    MIGRATE_OPERATION,
                    safe_detail=f"{DETAIL_CHECKSUM_MISMATCH}:{migration.version}",
                )
            continue

        result = _apply_one(connection, migration, applied_at)
        if result.failure is not None:
            return Result(failure=result.failure)

    return ok(LATEST_VERSION)


def _apply_one(
    connection: sqlite3.Connection, migration: Migration, applied_at: str
) -> Result[int]:
    try:
        connection.execute("BEGIN EXCLUSIVE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, migration.checksum, applied_at),
        )
        connection.execute("COMMIT")
    except sqlite3.DatabaseError:
        _safe_rollback(connection)
        return failed(
            FailureCode.CHECKS_FAILED,
            MIGRATE_OPERATION,
            safe_detail=f"{DETAIL_APPLY_FAILED}:{migration.version}",
        )
    return ok(migration.version)


def _safe_rollback(connection: sqlite3.Connection) -> None:
    # Nothing to roll back, or the connection is unusable; the caller reports failure.
    with suppress(sqlite3.DatabaseError):
        connection.execute("ROLLBACK")
