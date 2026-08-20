"""Crash recovery: an interrupted transaction leaves either all or none of its work.

A real process kill is used rather than a simulated one, so the assertions cover
SQLite's own rollback-journal recovery.

Requirements: 6.7, 6.8, 6.9, 18.10, 18.11, 18.12
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from provenance.domain.models import AssetHash
from provenance.infrastructure.sqlite.connection import GateState, SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter
from tests.crash_helper import (
    CRASH_ASSET_HASH,
    CRASH_EXIT_CODE,
    MODE_AFTER_COMMIT,
    MODE_BEFORE_COMMIT,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]


def _crash(registry_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.crash_helper", str(registry_path), mode],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={
            "PATH": "",
            "SYSTEMROOT": "C:\\Windows",
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONIOENCODING": "utf-8",
        },
    )


def _reopen(registry_path: Path) -> SqliteRegistry:
    registry = SqliteRegistry(registry_path)
    registry.initialize().unwrap()
    return registry


def test_crash_before_commit_exposes_the_prior_state(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.sqlite3"

    completed = _crash(registry_path, MODE_BEFORE_COMMIT)

    assert completed.returncode == CRASH_EXIT_CODE, completed.stderr
    registry = _reopen(registry_path)
    assert registry.status.gate is GateState.ENABLED
    assert registry.status.integrity_errors == ()
    with SqliteRegistryAdapter(registry).begin("read").unwrap() as uow:
        assert uow.assets.get(AssetHash(CRASH_ASSET_HASH)) is None
        assert uow.incidents.list_active() == []


def test_crash_after_commit_exposes_the_committed_state(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.sqlite3"

    completed = _crash(registry_path, MODE_AFTER_COMMIT)

    assert completed.returncode == CRASH_EXIT_CODE, completed.stderr
    registry = _reopen(registry_path)
    assert registry.status.gate is GateState.ENABLED
    with SqliteRegistryAdapter(registry).begin("read").unwrap() as uow:
        asset = uow.assets.get(AssetHash(CRASH_ASSET_HASH))

    assert asset is not None
    assert asset.creator_id == "crash.creator"


def test_integrity_checks_pass_after_an_interrupted_transaction(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.sqlite3"
    _crash(registry_path, MODE_BEFORE_COMMIT)

    registry = _reopen(registry_path)

    assert registry.writes_enabled
    assert registry.status.foreign_key_violations == 0
    with registry.connect_for_read() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_registry_remains_writable_after_recovery(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.sqlite3"
    _crash(registry_path, MODE_BEFORE_COMMIT)
    registry = _reopen(registry_path)
    adapter = SqliteRegistryAdapter(registry)

    with adapter.begin("register").unwrap() as uow:
        counts = uow.assets.deletion_counts(AssetHash(CRASH_ASSET_HASH))
        uow.commit()

    assert counts.incidents == 0


def test_retrying_the_same_registration_after_a_committed_crash_is_idempotent(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.sqlite3"
    _crash(registry_path, MODE_AFTER_COMMIT)

    # The identical operation runs again, as a retry would after the outcome was lost.
    completed = _crash(registry_path, MODE_AFTER_COMMIT)

    assert completed.returncode == CRASH_EXIT_CODE, completed.stderr
    registry = _reopen(registry_path)
    with registry.connect_for_read() as connection:
        stored = connection.execute(
            "SELECT count(*) FROM registered_assets WHERE asset_hash = ?", (CRASH_ASSET_HASH,)
        ).fetchone()[0]

    assert stored == 1
