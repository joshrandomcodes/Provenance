"""Idempotent execution of confirmed material actions.

A material action is one the creator explicitly confirmed: a status change, a
whitelist change, a strike authorization, or a recorded dispatch outcome. Each runs
inside a single transaction that commits the state change, exactly one audit event,
and one operation receipt together.

The receipt is keyed by a hash over the operation type, the target identifiers, and
the requested values, so repeating an identical request returns the previously
committed outcome instead of mutating anything a second time.

Requirements: 18.6, 18.7, 18.8, 18.10, 18.12
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from provenance.domain.errors import Result, ok
from provenance.domain.models import CommittedOperation, ContentHash, OperationKey
from provenance.domain.time import Clock, now_timestamp
from provenance.ports.registry import RegistryPort, UnitOfWork

RUN_OPERATION: Final = "run_material_action"


def _canonical(payload: Mapping[str, str]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def values_hash(requested_values: Mapping[str, str]) -> ContentHash:
    """Hash the requested values so a retry can be recognized as identical."""
    digest = hashlib.sha256(_canonical(requested_values).encode("utf-8")).hexdigest()
    return ContentHash(digest)


def operation_key(
    operation_type: str,
    target_ids: Mapping[str, str],
    requested_values: Mapping[str, str],
) -> OperationKey:
    """Derive the stable idempotency key for one confirmed action."""
    material = _canonical(
        {
            "operation_type": operation_type,
            "target_ids": _canonical(target_ids),
            "requested_values": _canonical(requested_values),
        }
    )
    return OperationKey(hashlib.sha256(material.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """Identifies one confirmed action and the values it was confirmed against."""

    operation_type: str
    target_ids: Mapping[str, str] = field(default_factory=dict)
    requested_values: Mapping[str, str] = field(default_factory=dict)

    @property
    def key(self) -> OperationKey:
        """The idempotency key for this request."""
        return operation_key(self.operation_type, self.target_ids, self.requested_values)

    @property
    def values_digest(self) -> ContentHash:
        """Hash of the requested values, stored on the receipt."""
        return values_hash(self.requested_values)


@dataclass(frozen=True, slots=True)
class OperationEffect[T]:
    """What an action produced inside the transaction."""

    value: T
    outcome: Mapping[str, str] = field(default_factory=dict)
    audit_event_id: int | None = None


@dataclass(frozen=True, slots=True)
class OperationResult[T]:
    """Committed outcome, or the receipt proving it already committed."""

    receipt: CommittedOperation
    value: T | None = None
    replayed: bool = False


class MaterialActionRunner:
    """Runs confirmed actions exactly once per idempotency key."""

    __slots__ = ("_registry", "_clock")

    def __init__(self, registry: RegistryPort, clock: Clock) -> None:
        self._registry = registry
        self._clock = clock

    def run[T](
        self,
        request: OperationRequest,
        action: Callable[[UnitOfWork], Result[OperationEffect[T]]],
    ) -> Result[OperationResult[T]]:
        """Execute one action, or return the receipt from an identical earlier run."""
        begun = self._registry.begin(request.operation_type)
        if begun.failure is not None:
            return Result(failure=begun.failure)

        with begun.unwrap() as uow:
            existing = uow.operations.committed(request.key)
            if existing is not None:
                # A retry of an already committed action changes nothing.
                return ok(OperationResult(receipt=existing, value=None, replayed=True))

            effect = action(uow)
            if effect.failure is not None:
                return Result(failure=effect.failure)

            produced = effect.unwrap()
            recorded = uow.operations.record(
                CommittedOperation(
                    operation_key=request.key,
                    operation_type=request.operation_type,
                    target_ids=dict(request.target_ids),
                    requested_values_hash=request.values_digest,
                    outcome=dict(produced.outcome),
                    committed_at=now_timestamp(self._clock),
                    audit_event_id=produced.audit_event_id,
                )
            )
            if recorded.failure is not None:
                return Result(failure=recorded.failure)

            uow.commit()
            return ok(
                OperationResult(receipt=recorded.unwrap(), value=produced.value, replayed=False)
            )
