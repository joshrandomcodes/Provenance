"""Startup checks, the session write gate, and migration refusal.

Requirements: 6.9, 6.10, 6.11, 17.12, 18.11
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from provenance.domain.errors import FailureCode
from provenance.infrastructure.sqlite.connection import (
    DETAIL_FOREIGN_KEY_VIOLATIONS,
    DETAIL_INTEGRITY_FAILED,
    DETAIL_NOT_A_DATABASE,
    RECOVERY_GUIDANCE,
    GateState,
    RegistryWriteRefusedError,
    SqliteRegistry,
)
from provenance.infrastructure.sqlite.migrations import (
    DETAIL_CHECKSUM_MISMATCH,
    DETAIL_UNKNOWN_VERSION,
    MIGRATIONS,
)

pytestmark = pytest.mark.integration

TIMESTAMP = "2026-05-06T07:08:09Z"
ASSET_HASH = "a" * 64


def _registry(tmp_path: Path, name: str = "registry.sqlite3") -> SqliteRegistry:
    return SqliteRegistry(tmp_path / name)


def test_fresh_registry_enables_writes(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    status = registry.initialize().unwrap()

    assert status.gate is GateState.ENABLED
    assert status.integrity_errors == ()
    assert status.foreign_key_violations == 0
    assert registry.writes_enabled
    assert registry.ensure_writable() is None


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"

    first = SqliteRegistry(path)
    first.initialize().unwrap()
    second = SqliteRegistry(path)
    status = second.initialize().unwrap()

    assert status.gate is GateState.ENABLED
    with second.connect_for_read() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )


def test_writes_are_refused_before_initialize(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    failure = registry.ensure_writable()

    assert failure is not None
    assert failure.code is FailureCode.CHECKS_PENDING
    assert registry.status.gate is GateState.PENDING
    with pytest.raises(RegistryWriteRefusedError), registry.connect_for_write():
        pass


def test_missing_parent_directory_is_created(tmp_path: Path) -> None:
    registry = SqliteRegistry(tmp_path / "nested" / "deeper" / "registry.sqlite3")

    assert registry.initialize().unwrap().gate is GateState.ENABLED
    assert registry.path.exists()


def test_a_file_that_is_not_a_database_disables_writes(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    path.write_bytes(b"this is definitely not a SQLite database file")
    registry = SqliteRegistry(path)

    failure = registry.initialize().unwrap_failure()

    assert failure.code is FailureCode.CHECKS_FAILED
    assert registry.status.gate is GateState.DISABLED
    assert registry.status.detail == DETAIL_NOT_A_DATABASE
    assert registry.status.guidance == RECOVERY_GUIDANCE
    assert not registry.writes_enabled


def test_disabled_gate_refuses_write_connections(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    path.write_bytes(b"not a database")
    registry = SqliteRegistry(path)
    registry.initialize()

    with (
        pytest.raises(RegistryWriteRefusedError) as raised,
        registry.connect_for_write("mark_fair_use"),
    ):
        pass

    assert raised.value.failure.code is FailureCode.CHECKS_FAILED
    assert raised.value.failure.operation == "mark_fair_use"


def test_foreign_key_violations_disable_writes(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    SqliteRegistry(path).initialize().unwrap()

    # Insert an orphan row with enforcement off, simulating external corruption.
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute(
        "INSERT INTO incidents (asset_hash, page_url, image_url, creator_id_evidence, "
        "payload_created_at, extraction_crc32, context_json, first_seen_at, last_seen_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ASSET_HASH,
            "https://e.com/a",
            "https://e.com/a.png",
            "creator",
            TIMESTAMP,
            1,
            "{}",
            TIMESTAMP,
            TIMESTAMP,
            "Detected",
        ),
    )
    raw.close()

    reopened = SqliteRegistry(path)
    failure = reopened.initialize().unwrap_failure()

    assert failure.safe_detail == DETAIL_FOREIGN_KEY_VIOLATIONS
    assert reopened.status.gate is GateState.DISABLED
    assert reopened.status.foreign_key_violations == 1
    assert not reopened.writes_enabled


def test_edited_migration_checksum_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    SqliteRegistry(path).initialize().unwrap()

    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("UPDATE schema_migrations SET checksum = ?", ("f" * 64,))
    raw.close()

    reopened = SqliteRegistry(path)
    failure = reopened.initialize().unwrap_failure()

    assert failure.code is FailureCode.CHECKS_FAILED
    assert failure.safe_detail is not None
    assert failure.safe_detail.startswith(DETAIL_CHECKSUM_MISMATCH)
    assert reopened.status.gate is GateState.DISABLED


def test_database_from_a_newer_build_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    SqliteRegistry(path).initialize().unwrap()

    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute(
        "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
        (999, "future_migration", "e" * 64, TIMESTAMP),
    )
    raw.close()

    reopened = SqliteRegistry(path)
    failure = reopened.initialize().unwrap_failure()

    assert failure.safe_detail is not None
    assert failure.safe_detail.startswith(DETAIL_UNKNOWN_VERSION)
    assert reopened.status.gate is GateState.DISABLED


def test_integrity_failure_reports_errors_and_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path)

    def broken_integrity(_self: SqliteRegistry, _connection: sqlite3.Connection) -> tuple[str, ...]:
        return ("*** in database main ***", "page 3 is never used")

    monkeypatch.setattr(SqliteRegistry, "_integrity_errors", broken_integrity)

    failure = registry.initialize().unwrap_failure()

    assert failure.safe_detail == DETAIL_INTEGRITY_FAILED
    assert registry.status.gate is GateState.DISABLED
    assert len(registry.status.integrity_errors) == 2
    assert registry.status.guidance == RECOVERY_GUIDANCE


def test_read_connections_remain_available_when_the_gate_is_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    SqliteRegistry(path).initialize().unwrap()

    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("UPDATE schema_migrations SET checksum = ?", ("f" * 64,))
    raw.close()

    reopened = SqliteRegistry(path)
    reopened.initialize()

    assert not reopened.writes_enabled
    with reopened.connect_for_read() as connection:
        assert connection.execute("SELECT count(*) FROM registered_assets").fetchone()[0] == 0


def test_status_reports_a_plain_local_path(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.initialize().unwrap()

    rendered = str(registry.status.path)

    assert rendered == str(tmp_path / "registry.sqlite3")
    assert "://" not in rendered


def test_open_transactions_are_rolled_back_on_exit(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.initialize().unwrap()

    with registry.connect_for_write() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO registered_assets (asset_hash, creator_id, registered_at, width, height, "
            "source_media_type, display_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ASSET_HASH, "creator.one", TIMESTAMP, 4, 4, "image/png", "Creator"),
        )
        # Leaving the block without committing must discard the row.

    with registry.connect_for_read() as connection:
        assert connection.execute("SELECT count(*) FROM registered_assets").fetchone()[0] == 0


def test_unsupported_sqlite_version_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 30, 0))
    registry = _registry(tmp_path)

    failure = registry.initialize().unwrap_failure()

    assert failure.code is FailureCode.DEPENDENCY_INCOMPATIBLE
    assert registry.status.gate is GateState.DISABLED
