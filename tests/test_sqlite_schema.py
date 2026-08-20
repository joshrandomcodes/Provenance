"""Registry schema, constraints, indexes, and migration integrity.

Requirements: 6.1-6.6, 6.9, 6.12, 12.4, 17.8
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from provenance.infrastructure.sqlite.connection import GateState, SqliteRegistry
from provenance.infrastructure.sqlite.migrations import LATEST_VERSION, MIGRATIONS

pytestmark = pytest.mark.integration

ASSET_HASH = "a" * 64
OTHER_HASH = "b" * 64
TIMESTAMP = "2026-05-06T07:08:09Z"
OPERATION_KEY = "c" * 64

_INSERT_ASSET = (
    "INSERT INTO registered_assets (asset_hash, creator_id, registered_at, width, height, "
    "source_media_type, display_name) VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_INCIDENT = (
    "INSERT INTO incidents (asset_hash, page_url, image_url, creator_id_evidence, "
    "payload_created_at, extraction_crc32, context_json, first_seen_at, last_seen_at, status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_WHITELIST = (
    "INSERT INTO whitelist_entries (asset_hash, page_url, rationale, created_at, modified_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
_INSERT_AUDIT = (
    "INSERT INTO audit_events (event_type, occurred_at, operation_key, asset_hash_tombstone, "
    "previous_statuses_json, new_statuses_json) VALUES (?, ?, ?, ?, ?, ?)"
)


@pytest.fixture
def registry(tmp_path: Path) -> SqliteRegistry:
    registry = SqliteRegistry(tmp_path / "registry.sqlite3")
    assert registry.initialize().unwrap().gate is GateState.ENABLED
    return registry


@pytest.fixture
def connection(registry: SqliteRegistry) -> Iterator[sqlite3.Connection]:
    with registry.connect_for_write() as active:
        yield active


def _add_asset(connection: sqlite3.Connection, asset_hash: str = ASSET_HASH) -> None:
    connection.execute(
        _INSERT_ASSET, (asset_hash, "creator.one", TIMESTAMP, 10, 10, "image/png", "Creator One")
    )


def _add_incident(
    connection: sqlite3.Connection,
    page_url: str = "https://example.com/a",
    image_url: str = "https://cdn.example.com/a.png",
    asset_hash: str = ASSET_HASH,
    status: str = "Detected",
) -> None:
    connection.execute(
        _INSERT_INCIDENT,
        (
            asset_hash,
            page_url,
            image_url,
            "creator.one",
            TIMESTAMP,
            12345,
            "{}",
            TIMESTAMP,
            TIMESTAMP,
            status,
        ),
    )


def test_schema_creates_expected_tables_and_indexes(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }

    assert {
        "schema_migrations",
        "registered_assets",
        "incidents",
        "whitelist_entries",
        "audit_events",
        "operation_receipts",
    } <= tables
    assert {
        "idx_incidents_active",
        "idx_incidents_asset_page",
        "idx_incidents_last_seen",
        "idx_whitelist_incident",
        "idx_audit_asset_time",
        "idx_audit_incident_time",
    } <= indexes


def test_tables_are_strict(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO incidents (asset_hash, page_url, image_url, creator_id_evidence, "
            "payload_created_at, extraction_crc32, context_json, first_seen_at, last_seen_at, "
            "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ASSET_HASH,
                "https://e.com/a",
                "https://e.com/a.png",
                "creator.one",
                TIMESTAMP,
                "not-an-integer",
                "{}",
                TIMESTAMP,
                TIMESTAMP,
                "Detected",
            ),
        )


def test_migration_version_is_recorded(connection: sqlite3.Connection) -> None:
    rows = list(connection.execute("SELECT version, name, checksum FROM schema_migrations"))

    assert [row[0] for row in rows] == [migration.version for migration in MIGRATIONS]
    assert rows[0][2] == MIGRATIONS[0].checksum
    assert len(rows[0][2]) == 64


def test_foreign_keys_are_enabled_on_every_connection(registry: SqliteRegistry) -> None:
    with registry.connect_for_write() as first, registry.connect_for_read() as second:
        assert int(first.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        assert int(second.execute("PRAGMA foreign_keys").fetchone()[0]) == 1


def test_durability_pragmas_are_set(connection: sqlite3.Connection) -> None:
    journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])

    assert journal_mode == "delete"
    assert synchronous == 2  # FULL


def test_asset_hash_must_be_lowercase_hex(connection: sqlite3.Connection) -> None:
    for invalid in ("A" * 64, "z" * 64, "a" * 63):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                _INSERT_ASSET,
                (invalid, "creator.one", TIMESTAMP, 4, 4, "image/png", "Creator"),
            )


def test_duplicate_asset_hash_is_rejected(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    with pytest.raises(sqlite3.IntegrityError):
        _add_asset(connection)


def test_media_type_is_constrained(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_ASSET, (ASSET_HASH, "creator.one", TIMESTAMP, 4, 4, "image/gif", "Creator")
        )


def test_pixel_ceiling_is_enforced(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_ASSET,
            (ASSET_HASH, "creator.one", TIMESTAMP, 10_000, 4_001, "image/png", "Creator"),
        )


def test_timestamp_layout_is_enforced(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_ASSET,
            (ASSET_HASH, "creator.one", "2026-05-06 07:08:09", 4, 4, "image/png", "Creator"),
        )


def test_incident_requires_an_existing_asset(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _add_incident(connection)


def test_incident_uniqueness_is_the_full_triple(connection: sqlite3.Connection) -> None:
    _add_asset(connection)
    _add_incident(connection)

    with pytest.raises(sqlite3.IntegrityError):
        _add_incident(connection)

    # A different image URL under the same page is a distinct incident.
    _add_incident(connection, image_url="https://cdn.example.com/b.png")
    assert connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 2


def test_incident_status_is_constrained(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    with pytest.raises(sqlite3.IntegrityError):
        _add_incident(connection, status="Resolved")


def test_incident_context_must_be_json(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_INCIDENT,
            (
                ASSET_HASH,
                "https://e.com/a",
                "https://e.com/a.png",
                "creator.one",
                TIMESTAMP,
                1,
                "not json",
                TIMESTAMP,
                TIMESTAMP,
                "Detected",
            ),
        )


def test_crc_range_is_constrained(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_INCIDENT,
            (
                ASSET_HASH,
                "https://e.com/a",
                "https://e.com/a.png",
                "creator.one",
                TIMESTAMP,
                4_294_967_296,
                "{}",
                TIMESTAMP,
                TIMESTAMP,
                "Detected",
            ),
        )


def test_url_comparison_is_case_sensitive(connection: sqlite3.Connection) -> None:
    _add_asset(connection)
    _add_incident(connection, page_url="https://example.com/Art")
    _add_incident(connection, page_url="https://example.com/art")

    assert connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 2


def test_whitelist_uniqueness_is_asset_and_page(connection: sqlite3.Connection) -> None:
    _add_asset(connection)
    connection.execute(
        _INSERT_WHITELIST, (ASSET_HASH, "https://example.com/a", "review", TIMESTAMP, TIMESTAMP)
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_WHITELIST,
            (ASSET_HASH, "https://example.com/a", "second", TIMESTAMP, TIMESTAMP),
        )

    # A different path is a different scope.
    connection.execute(
        _INSERT_WHITELIST, (ASSET_HASH, "https://example.com/b", "review", TIMESTAMP, TIMESTAMP)
    )
    assert connection.execute("SELECT count(*) FROM whitelist_entries").fetchone()[0] == 2


def test_whitelist_rationale_bounds_are_enforced(connection: sqlite3.Connection) -> None:
    _add_asset(connection)

    for rationale in ("", "x" * 501):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                _INSERT_WHITELIST,
                (
                    ASSET_HASH,
                    f"https://example.com/{len(rationale)}",
                    rationale,
                    TIMESTAMP,
                    TIMESTAMP,
                ),
            )


def test_deleting_an_asset_cascades_to_dependents(connection: sqlite3.Connection) -> None:
    _add_asset(connection)
    _add_incident(connection)
    connection.execute(
        _INSERT_WHITELIST, (ASSET_HASH, "https://example.com/a", "review", TIMESTAMP, TIMESTAMP)
    )
    connection.execute(
        _INSERT_AUDIT, ("asset_registered", TIMESTAMP, OPERATION_KEY, ASSET_HASH, "{}", "{}")
    )

    connection.execute("DELETE FROM registered_assets WHERE asset_hash = ?", (ASSET_HASH,))

    assert connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM whitelist_entries").fetchone()[0] == 0
    # Audit history survives as a tombstone.
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1


def test_audit_operation_key_is_unique(connection: sqlite3.Connection) -> None:
    connection.execute(
        _INSERT_AUDIT, ("credit_requested", TIMESTAMP, OPERATION_KEY, None, "{}", "{}")
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            _INSERT_AUDIT, ("credit_requested", TIMESTAMP, OPERATION_KEY, None, "{}", "{}")
        )


def test_operation_receipt_key_is_unique(connection: sqlite3.Connection) -> None:
    statement = (
        "INSERT INTO operation_receipts (operation_key, operation_type, target_ids_json, "
        "requested_values_hash, outcome_json, committed_at) VALUES (?, ?, ?, ?, ?, ?)"
    )
    connection.execute(statement, (OPERATION_KEY, "mark_fair_use", "{}", "d" * 64, "{}", TIMESTAMP))

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            statement, (OPERATION_KEY, "mark_fair_use", "{}", "d" * 64, "{}", TIMESTAMP)
        )


def test_whitelist_incident_reference_is_cleared_on_incident_delete(
    connection: sqlite3.Connection,
) -> None:
    _add_asset(connection)
    _add_incident(connection)
    incident_id = connection.execute("SELECT id FROM incidents").fetchone()[0]
    connection.execute(
        "INSERT INTO whitelist_entries (asset_hash, page_url, rationale, created_at, modified_at, "
        "related_incident_id) VALUES (?, ?, ?, ?, ?, ?)",
        (ASSET_HASH, "https://example.com/a", "review", TIMESTAMP, TIMESTAMP, incident_id),
    )

    connection.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))

    assert (
        connection.execute("SELECT related_incident_id FROM whitelist_entries").fetchone()[0]
        is None
    )


def test_schema_version_reported_after_initialize(registry: SqliteRegistry) -> None:
    assert registry.status.schema_version == LATEST_VERSION
    assert registry.status.writable
    assert registry.status.guidance is None
