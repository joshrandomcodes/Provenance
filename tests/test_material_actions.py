"""Idempotent execution of confirmed material actions.

Requirements: 18.6, 18.7, 18.8, 18.10, 18.12
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from provenance.application.operations import (
    MaterialActionRunner,
    OperationEffect,
    OperationRequest,
    operation_key,
    values_hash,
)
from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CreatorId,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionPlan,
    NewAuditEvent,
    NormalizedUrl,
)
from provenance.domain.time import UtcTimestamp
from provenance.ports.registry import UnitOfWork
from tests.registry_support import (
    RegistryHarness,
    detection,
    seed_asset,
    temporary_registry,
)

pytestmark = pytest.mark.integration

HASH = AssetHash("a" * 64)
CREATOR = CreatorId("studio.one")
PAGE = NormalizedUrl("https://example.com/art")
IMAGE = NormalizedUrl("https://cdn.example.com/a.png")
AT = UtcTimestamp("2026-05-06T07:08:09Z")


class FixedClock:
    """Deterministic clock."""

    def utc_now(self) -> datetime:
        return datetime(2026, 9, 10, 11, 12, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _runner(harness: RegistryHarness) -> MaterialActionRunner:
    return MaterialActionRunner(harness.adapter, FixedClock())


def _authorize_request(incident_id: int) -> OperationRequest:
    return OperationRequest(
        operation_type="authorize_strike",
        target_ids={"incident_id": str(incident_id)},
        requested_values={"new_status": IncidentStatus.STRIKE_AUTHORIZED.value},
    )


def _authorize_action(
    incident_id: int,
) -> Callable[[UnitOfWork], Result[OperationEffect[int]]]:
    def action(uow: UnitOfWork) -> Result[OperationEffect[int]]:
        plan = IncidentTransitionPlan(
            transitions=(
                IncidentTransition(
                    incident_id=incident_id,
                    previous_status=IncidentStatus.DETECTED,
                    new_status=IncidentStatus.STRIKE_AUTHORIZED,
                ),
            ),
            audit=NewAuditEvent(
                event_type=AuditEventType.STRIKE_AUTHORIZED,
                occurred_at=AT,
                operation_key=_authorize_request(incident_id).key,
                incident_id=incident_id,
                previous_statuses={incident_id: IncidentStatus.DETECTED},
                new_statuses={incident_id: IncidentStatus.STRIKE_AUTHORIZED},
            ),
        )
        applied = uow.incidents.apply_status_plan(plan)
        if applied.failure is not None:
            return Result(failure=applied.failure)
        return ok(
            OperationEffect(
                value=incident_id,
                outcome={"status": IncidentStatus.STRIKE_AUTHORIZED.value},
                audit_event_id=applied.unwrap().audit.id,
            )
        )

    return action


def _seed_incident(harness: RegistryHarness) -> int:
    seed_asset(harness, HASH, CREATOR)
    with harness.adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(detection(HASH, CREATOR, PAGE, IMAGE)).unwrap()
        uow.commit()
    return incident.id


def test_keys_are_stable_and_value_sensitive() -> None:
    first = operation_key("authorize_strike", {"incident_id": "1"}, {"status": "x"})
    same = operation_key("authorize_strike", {"incident_id": "1"}, {"status": "x"})
    other_target = operation_key("authorize_strike", {"incident_id": "2"}, {"status": "x"})
    other_values = operation_key("authorize_strike", {"incident_id": "1"}, {"status": "y"})
    other_type = operation_key("mark_fair_use", {"incident_id": "1"}, {"status": "x"})

    assert first == same
    assert len({first, other_target, other_values, other_type}) == 4
    assert len(first) == 64


def test_key_is_independent_of_mapping_order() -> None:
    first = operation_key("op", {"a": "1", "b": "2"}, {"x": "9", "y": "8"})
    second = operation_key("op", {"b": "2", "a": "1"}, {"y": "8", "x": "9"})

    assert first == second


def test_values_hash_is_stable() -> None:
    assert values_hash({"a": "1"}) == values_hash({"a": "1"})
    assert values_hash({"a": "1"}) != values_hash({"a": "2"})


def test_action_commits_state_audit_and_receipt_together() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        runner = _runner(harness)

        result = runner.run(
            _authorize_request(incident_id),
            _authorize_action(incident_id),
        ).unwrap()

        assert result.replayed is False
        assert result.value == incident_id
        assert result.receipt.outcome == {"status": IncidentStatus.STRIKE_AUTHORIZED.value}
        assert result.receipt.audit_event_id is not None
        assert result.receipt.committed_at == "2026-09-10T11:12:13Z"

        with harness.adapter.begin("read").unwrap() as uow:
            stored = uow.incidents.get(incident_id)
        assert stored is not None
        assert stored.status is IncidentStatus.STRIKE_AUTHORIZED
        assert harness.count("audit_events") == 1
        assert harness.count("operation_receipts") == 1


def test_identical_retry_replays_the_receipt_without_changing_anything() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        runner = _runner(harness)
        request = _authorize_request(incident_id)

        first = runner.run(request, _authorize_action(incident_id)).unwrap()
        snapshot = harness.snapshot()

        second = runner.run(request, _authorize_action(incident_id)).unwrap()

        assert first.replayed is False
        assert second.replayed is True
        assert second.value is None
        assert second.receipt.operation_key == first.receipt.operation_key
        assert second.receipt.committed_at == first.receipt.committed_at
        assert harness.snapshot() == snapshot


def test_failed_action_leaves_no_state_audit_or_receipt() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        runner = _runner(harness)
        snapshot = harness.snapshot()

        def failing(_uow: UnitOfWork) -> Result[OperationEffect[int]]:
            return failed(FailureCode.STALE_CONFIRMATION, "authorize_strike")

        result = runner.run(_authorize_request(incident_id), failing)

        assert result.unwrap_failure().code is FailureCode.STALE_CONFIRMATION
        assert harness.snapshot() == snapshot
        assert harness.count("operation_receipts") == 0


def test_partially_applied_action_is_rolled_back_on_failure() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        runner = _runner(harness)
        snapshot = harness.snapshot()

        def apply_then_fail(uow: UnitOfWork) -> Result[OperationEffect[int]]:
            uow.incidents.apply_status_plan(
                IncidentTransitionPlan(
                    transitions=(
                        IncidentTransition(
                            incident_id=incident_id,
                            previous_status=IncidentStatus.DETECTED,
                            new_status=IncidentStatus.STRIKE_AUTHORIZED,
                        ),
                    ),
                    audit=NewAuditEvent(
                        event_type=AuditEventType.STRIKE_AUTHORIZED,
                        occurred_at=AT,
                        operation_key=_authorize_request(incident_id).key,
                        incident_id=incident_id,
                    ),
                )
            )
            return failed(FailureCode.COMMIT_FAILED, "authorize_strike")

        result = runner.run(_authorize_request(incident_id), apply_then_fail)

        assert result.value is None
        assert harness.snapshot() == snapshot


def test_different_values_produce_a_separate_operation() -> None:
    with temporary_registry() as harness:
        incident_id = _seed_incident(harness)
        runner = _runner(harness)

        runner.run(
            _authorize_request(incident_id),
            _authorize_action(incident_id),
        ).unwrap()

        # A different requested value is a different operation, and this one is stale
        # because the incident already moved to Strike Authorized.
        other = OperationRequest(
            operation_type="authorize_strike",
            target_ids={"incident_id": str(incident_id)},
            requested_values={"new_status": IncidentStatus.CREDIT_REQUESTED.value},
        )
        result = runner.run(other, _authorize_action(incident_id))

        assert result.unwrap_failure().code is FailureCode.STALE_PREVIEW
        assert harness.count("operation_receipts") == 1


def test_runner_reports_the_closed_gate(tmp_path_factory: pytest.TempPathFactory) -> None:
    from provenance.infrastructure.sqlite.connection import SqliteRegistry
    from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter

    path = tmp_path_factory.mktemp("closed") / "registry.sqlite3"
    path.write_bytes(b"not a database")
    registry = SqliteRegistry(path)
    registry.initialize()
    runner = MaterialActionRunner(SqliteRegistryAdapter(registry), FixedClock())

    def unreachable(_uow: UnitOfWork) -> Result[OperationEffect[int]]:
        raise AssertionError("the action must not run when the gate is closed")

    result = runner.run(OperationRequest(operation_type="authorize_strike"), unreachable)

    assert result.unwrap_failure().code is FailureCode.CHECKS_FAILED
