"""Registry ports.

Repositories never commit. The unit of work owns the transaction so every confirmed
material action commits its records and exactly one audit event together.

Requirements: 5.2-5.5, 6.1-6.11, 10.5-10.7, 12.1-12.7, 17.7-17.11, 18.6-18.12
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol

from provenance.domain.errors import Result
from provenance.domain.models import (
    AssetHash,
    AuditEvent,
    CommittedOperation,
    DeleteOutcome,
    DeletionCounts,
    DeletionPreview,
    Incident,
    IncidentTransitionPlan,
    MarkFairUse,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
    RegisterAsset,
    RegisteredAsset,
    RegistrationOutcome,
    RemoveFairUse,
    TransitionSet,
    VerifiedDetection,
    WhitelistEntry,
)


class AssetRepository(Protocol):
    """Registered asset reads and writes within an open transaction."""

    def get(self, asset_hash: AssetHash) -> RegisteredAsset | None: ...

    def register_or_reuse(self, command: RegisterAsset) -> Result[RegistrationOutcome]: ...

    def deletion_counts(self, asset_hash: AssetHash) -> DeletionCounts: ...

    def deletion_preview(self, asset_hash: AssetHash) -> Result[DeletionPreview]: ...

    def delete_if_preview_matches(self, preview: DeletionPreview) -> Result[DeleteOutcome]: ...


class IncidentRepository(Protocol):
    """Incident reads and writes within an open transaction."""

    def get(self, incident_id: int) -> Incident | None: ...

    def upsert_detection(self, detection: VerifiedDetection) -> Result[Incident]: ...

    def list_active(self) -> Sequence[Incident]: ...

    def list_fair_use(self) -> Sequence[Incident]: ...

    def apply_status_plan(self, plan: IncidentTransitionPlan) -> Result[TransitionSet]: ...


class WhitelistRepository(Protocol):
    """Exact-scope fair-use reads and writes within an open transaction."""

    def exact(self, asset_hash: AssetHash, page_url: NormalizedUrl) -> WhitelistEntry | None: ...

    def upsert_and_mark_fair_use(self, command: MarkFairUse) -> Result[TransitionSet]: ...

    def remove_and_reopen(self, command: RemoveFairUse) -> Result[TransitionSet]: ...


class AuditRepository(Protocol):
    """Append-only audit trail within an open transaction."""

    def append(self, event: NewAuditEvent) -> Result[AuditEvent]: ...

    def by_operation_key(self, key: OperationKey) -> AuditEvent | None: ...


class OperationRepository(Protocol):
    """Idempotency receipts for confirmed material actions."""

    def committed(self, key: OperationKey) -> CommittedOperation | None: ...

    def record(self, receipt: CommittedOperation) -> Result[CommittedOperation]: ...


class UnitOfWork(Protocol):
    """One SQLite transaction spanning every repository."""

    @property
    def assets(self) -> AssetRepository: ...

    @property
    def incidents(self) -> IncidentRepository: ...

    @property
    def whitelist(self) -> WhitelistRepository: ...

    @property
    def audits(self) -> AuditRepository: ...

    @property
    def operations(self) -> OperationRepository: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class RegistryPort(Protocol):
    """Factory for units of work, gated by startup integrity checks."""

    @property
    def writes_enabled(self) -> bool: ...

    def begin(self, operation: str) -> Result[UnitOfWork]:
        """Open one transaction, or fail when the write gate is closed."""
        ...
