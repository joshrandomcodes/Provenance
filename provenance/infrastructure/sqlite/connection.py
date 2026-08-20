"""Registry connection setup, startup checks, and the session write gate.

Startup opens the local database, enables and verifies foreign keys, applies pending
migrations, then runs ``PRAGMA integrity_check`` and ``PRAGMA foreign_key_check`` to
completion. Writes are enabled only when both checks report nothing. Otherwise the
gate is disabled for the whole session and every mutation is refused with recovery
guidance, leaving the file untouched for backup.

Requirements: 6.7-6.12, 17.12, 18.10-18.12
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from provenance.domain.errors import Failure, FailureCode, Result, failed, ok
from provenance.domain.time import format_utc_timestamp
from provenance.infrastructure.sqlite.migrations import LATEST_VERSION, apply_migrations

# STRICT tables require SQLite 3.37 or newer.
MINIMUM_SQLITE_VERSION: Final = (3, 37, 0)
BUSY_TIMEOUT_MS: Final = 5_000

INITIALIZE_OPERATION: Final = "initialize_registry"
WRITE_GATE_OPERATION: Final = "registry_write"

RECOVERY_GUIDANCE: Final = (
    "Registry checks did not pass, so writes are disabled for this session. "
    "Back up the registry file before any repair attempt, then restore a known-good "
    "backup or start a new registry."
)

DETAIL_UNSUPPORTED_SQLITE: Final = "sqlite_version_unsupported"
DETAIL_FOREIGN_KEYS_UNAVAILABLE: Final = "foreign_keys_unavailable"
DETAIL_NOT_A_DATABASE: Final = "file_is_not_a_database"
DETAIL_INTEGRITY_FAILED: Final = "integrity_check_failed"
DETAIL_FOREIGN_KEY_VIOLATIONS: Final = "foreign_key_violations"
DETAIL_CHECKS_INCOMPLETE: Final = "checks_did_not_complete"
DETAIL_PATH_UNUSABLE: Final = "registry_path_unusable"


class GateState(StrEnum):
    """Whether the Registry accepts writes in this session."""

    PENDING = "pending"
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class RegistryStatus:
    """Outcome of the startup checks, safe to display as text."""

    path: Path
    gate: GateState
    schema_version: int = 0
    integrity_errors: tuple[str, ...] = field(default_factory=tuple)
    foreign_key_violations: int = 0
    detail: str | None = None

    @property
    def writable(self) -> bool:
        """True when writes are permitted."""
        return self.gate is GateState.ENABLED

    @property
    def guidance(self) -> str | None:
        """Recovery guidance to show when the gate is not enabled."""
        return None if self.writable else RECOVERY_GUIDANCE


class SqliteRegistry:
    """Owns the local Registry file, its startup checks, and its write gate."""

    __slots__ = ("_path", "_status")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._status = RegistryStatus(path=path, gate=GateState.PENDING)

    @property
    def path(self) -> Path:
        """Local Registry path. Never converted into a URL."""
        return self._path

    @property
    def status(self) -> RegistryStatus:
        """Current gate status."""
        return self._status

    @property
    def writes_enabled(self) -> bool:
        """True when startup checks passed and writes are allowed."""
        return self._status.writable

    def initialize(self) -> Result[RegistryStatus]:
        """Run startup checks and set the session write gate."""
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
            return self._disable(DETAIL_UNSUPPORTED_SQLITE, FailureCode.DEPENDENCY_INCOMPATIBLE)

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return self._disable(DETAIL_PATH_UNUSABLE, FailureCode.CHECKS_FAILED)

        try:
            connection = self._open()
        except sqlite3.DatabaseError:
            return self._disable(DETAIL_NOT_A_DATABASE, FailureCode.CHECKS_FAILED)

        try:
            return self._initialize_with(connection)
        except sqlite3.DatabaseError:
            return self._disable(DETAIL_NOT_A_DATABASE, FailureCode.CHECKS_FAILED)
        finally:
            connection.close()

    def _initialize_with(self, connection: sqlite3.Connection) -> Result[RegistryStatus]:
        if not self._foreign_keys_enabled(connection):
            return self._disable(DETAIL_FOREIGN_KEYS_UNAVAILABLE, FailureCode.CHECKS_FAILED)

        migrated = apply_migrations(connection, format_utc_timestamp(datetime.now(UTC)))
        if migrated.failure is not None:
            return self._disable(
                migrated.failure.safe_detail or DETAIL_CHECKS_INCOMPLETE,
                FailureCode.CHECKS_FAILED,
            )

        integrity_errors = self._integrity_errors(connection)
        violations = self._foreign_key_violations(connection)
        if integrity_errors:
            self._status = RegistryStatus(
                path=self._path,
                gate=GateState.DISABLED,
                integrity_errors=integrity_errors,
                foreign_key_violations=violations,
                detail=DETAIL_INTEGRITY_FAILED,
            )
            return failed(
                FailureCode.CHECKS_FAILED,
                INITIALIZE_OPERATION,
                safe_detail=DETAIL_INTEGRITY_FAILED,
            )
        if violations:
            self._status = RegistryStatus(
                path=self._path,
                gate=GateState.DISABLED,
                foreign_key_violations=violations,
                detail=DETAIL_FOREIGN_KEY_VIOLATIONS,
            )
            return failed(
                FailureCode.CHECKS_FAILED,
                INITIALIZE_OPERATION,
                safe_detail=DETAIL_FOREIGN_KEY_VIOLATIONS,
            )

        self._status = RegistryStatus(
            path=self._path,
            gate=GateState.ENABLED,
            schema_version=LATEST_VERSION,
        )
        return ok(self._status)

    def ensure_writable(self, operation: str = WRITE_GATE_OPERATION) -> Failure | None:
        """Return a failure when writes are not permitted, or None when they are."""
        if self._status.gate is GateState.ENABLED:
            return None
        code = (
            FailureCode.CHECKS_PENDING
            if self._status.gate is GateState.PENDING
            else FailureCode.CHECKS_FAILED
        )
        return Failure(
            code=code,
            operation=operation,
            safe_detail=self._status.detail
            or (
                "startup_checks_pending"
                if self._status.gate is GateState.PENDING
                else DETAIL_CHECKS_INCOMPLETE
            ),
        )

    @contextmanager
    def connect_for_write(
        self, operation: str = WRITE_GATE_OPERATION
    ) -> Iterator[sqlite3.Connection]:
        """Yield a write connection, refusing when the gate is not enabled."""
        gate_failure = self.ensure_writable(operation)
        if gate_failure is not None:
            raise RegistryWriteRefusedError(gate_failure)

        connection = self._open()
        try:
            yield connection
        finally:
            _rollback_if_active(connection)
            connection.close()

    def connect_raw_for_write(self, operation: str = WRITE_GATE_OPERATION) -> sqlite3.Connection:
        """Open a write connection whose lifetime the caller owns.

        Used by the unit of work, which closes the connection when its transaction ends.
        """
        gate_failure = self.ensure_writable(operation)
        if gate_failure is not None:
            raise RegistryWriteRefusedError(gate_failure)
        return self._open()

    @contextmanager
    def connect_for_read(self) -> Iterator[sqlite3.Connection]:
        """Yield a short-lived read connection, permitted even when the gate is closed."""
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            isolation_level=None,  # explicit transaction control
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection

    def _foreign_keys_enabled(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute("PRAGMA foreign_keys").fetchone()
        return bool(row is not None and int(row[0]) == 1)

    def _integrity_errors(self, connection: sqlite3.Connection) -> tuple[str, ...]:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        messages = [str(row[0]) for row in rows]
        if messages == ["ok"]:
            return ()
        return tuple(messages)

    def _foreign_key_violations(self, connection: sqlite3.Connection) -> int:
        return len(connection.execute("PRAGMA foreign_key_check").fetchall())

    def _disable(self, detail: str, code: FailureCode) -> Result[RegistryStatus]:
        self._status = RegistryStatus(path=self._path, gate=GateState.DISABLED, detail=detail)
        return failed(code, INITIALIZE_OPERATION, safe_detail=detail)


class RegistryWriteRefusedError(Exception):
    """Raised when a write connection is requested while the gate is closed."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.code.value)
        self.failure = failure


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    # The connection is being closed regardless; the caller already has a failure.
    with suppress(sqlite3.DatabaseError):
        connection.execute("ROLLBACK")
