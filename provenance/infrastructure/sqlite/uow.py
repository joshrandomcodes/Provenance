"""Transaction ownership for the local Registry.

One unit of work equals one ``BEGIN IMMEDIATE`` transaction. Repositories share its
connection and never commit, so a confirmed action and its audit event become visible
together or not at all. Leaving the context without an explicit commit rolls back.

Requirements: 5.5, 6.7, 6.8, 11.8, 18.6, 18.10, 18.11, 18.12
"""

from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Final

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.infrastructure.sqlite.connection import (
    RegistryWriteRefusedError,
    SqliteRegistry,
)
from provenance.infrastructure.sqlite.repositories import (
    SqliteAssetRepository,
    SqliteAuditRepository,
    SqliteIncidentRepository,
    SqliteOperationRepository,
    SqliteWhitelistRepository,
)

BEGIN_OPERATION: Final = "begin_transaction"
DETAIL_ALREADY_FINISHED: Final = "transaction_already_finished"


class SqliteUnitOfWork:
    """One explicit SQLite transaction with its repositories."""

    __slots__ = (
        "_connection",
        "_operation",
        "_finished",
        "_committed",
        "assets",
        "incidents",
        "whitelist",
        "audits",
        "operations",
    )

    def __init__(self, connection: sqlite3.Connection, operation: str) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._operation = operation
        self._finished = False
        self._committed = False

        self.audits = SqliteAuditRepository(connection)
        self.assets = SqliteAssetRepository(connection)
        self.incidents = SqliteIncidentRepository(connection, self.audits)
        self.whitelist = SqliteWhitelistRepository(connection, self.incidents, self.audits)
        self.operations = SqliteOperationRepository(connection)

    @property
    def operation(self) -> str:
        """Name of the action this transaction serves."""
        return self._operation

    @property
    def committed(self) -> bool:
        """True once the transaction committed successfully."""
        return self._committed

    def __enter__(self) -> SqliteUnitOfWork:
        """Start the transaction with an immediate write lock."""
        self._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back unless the caller committed, then close the connection.

        Returning None never suppresses an exception raised inside the block.
        """
        try:
            if not self._finished:
                self.rollback()
        finally:
            self._connection.close()

    def commit(self) -> None:
        """Commit every change made in this transaction."""
        if self._finished:
            raise RuntimeError(DETAIL_ALREADY_FINISHED)
        self._connection.execute("COMMIT")
        self._finished = True
        self._committed = True

    def rollback(self) -> None:
        """Discard every change made in this transaction."""
        if self._finished:
            return
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")
        self._finished = True


class SqliteRegistryAdapter:
    """Opens units of work, refusing when the startup write gate is closed."""

    __slots__ = ("_registry",)

    def __init__(self, registry: SqliteRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> SqliteRegistry:
        """The underlying Registry, for status display."""
        return self._registry

    @property
    def writes_enabled(self) -> bool:
        """True when startup checks passed."""
        return self._registry.writes_enabled

    def begin(self, operation: str = BEGIN_OPERATION) -> Result[SqliteUnitOfWork]:
        """Open one transaction, or fail with the gate's reason."""
        gate_failure = self._registry.ensure_writable(operation)
        if gate_failure is not None:
            return Result(failure=gate_failure)

        try:
            connection = self._registry.connect_raw_for_write()
        except RegistryWriteRefusedError as refused:
            return Result(failure=refused.failure)
        except sqlite3.DatabaseError:
            return failed(FailureCode.BUSY, operation)
        return ok(SqliteUnitOfWork(connection, operation))
