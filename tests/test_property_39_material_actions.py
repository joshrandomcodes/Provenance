"""Property 39: Confirmed material actions are atomic and retry-idempotent.

Validates: Requirements 18.6, 18.7, 18.8
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from provenance.application.operations import (
    MaterialActionRunner,
    OperationEffect,
    OperationRequest,
)
from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import (
    AssetHash,
    AuditEventType,
    CreatorId,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionPlan,
    MarkFairUse,
    NewAuditEvent,
    NormalizedUrl,
    OperationKey,
    RemoveFairUse,
)
from provenance.domain.time import UtcTimestamp
from provenance.ports.registry import UnitOfWork
from tests.registry_support import RegistryHarness, detection, seed_asset, temporary_registry

ActionCallable = Callable[[UnitOfWork], Result[OperationEffect[str]]]

HASH: Final = AssetHash("a" * 64)
CREATOR: Final = CreatorId("studio.one")
PAGE: Final = NormalizedUrl("https://example.com/art")
IMAGE: Final = NormalizedUrl("https://cdn.example.com/a.png")
AT: Final = UtcTimestamp("2026-05-06T07:08:09Z")

ACTIONS: Final = ("authorize_strike", "request_credit", "mark_fair_use", "remove_fair_use")


class FixedClock:
    """Deterministic clock."""

    def utc_now(self) -> datetime:
        return datetime(2026, 9, 10, 11, 12, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


def _prepare(harness: RegistryHarness) -> int:
    seed_asset(harness, HASH, CREATOR)
    with harness.adapter.begin("scan").unwrap() as uow:
        incident = uow.incidents.upsert_detection(
            detection(HASH, CREATOR, PAGE, IMAGE, at=AT)
        ).unwrap()
        uow.commit()
    return incident.id


def _status_action(
    incident_id: int,
    new_status: IncidentStatus,
    event_type: AuditEventType,
    key: OperationKey,
) -> ActionCallable:
    def action(uow: UnitOfWork) -> Result[OperationEffect[str]]:
        current = uow.incidents.get(incident_id)
        if current is None:
            return failed(FailureCode.NOT_FOUND, "status_action")
        applied = uow.incidents.apply_status_plan(
            IncidentTransitionPlan(
                transitions=(
                    IncidentTransition(
                        incident_id=incident_id,
                        previous_status=current.status,
                        new_status=new_status,
                    ),
                ),
                audit=NewAuditEvent(
                    event_type=event_type,
                    occurred_at=AT,
                    operation_key=key,
                    incident_id=incident_id,
                ),
            )
        )
        if applied.failure is not None:
            return Result(failure=applied.failure)
        return ok(
            OperationEffect(
                value=new_status.value,
                outcome={"status": new_status.value},
                audit_event_id=applied.unwrap().audit.id,
            )
        )

    return action


def _whitelist_action(*, mark: bool, key: OperationKey) -> ActionCallable:
    def action(uow: UnitOfWork) -> Result[OperationEffect[str]]:
        if mark:
            applied = uow.whitelist.upsert_and_mark_fair_use(
                MarkFairUse(
                    asset_hash=HASH,
                    page_url=PAGE,
                    rationale="commentary",
                    at=AT,
                    operation_key=key,
                )
            )
        else:
            applied = uow.whitelist.remove_and_reopen(
                RemoveFairUse(
                    asset_hash=HASH,
                    page_url=PAGE,
                    at=AT,
                    operation_key=key,
                )
            )
        if applied.failure is not None:
            return Result(failure=applied.failure)
        return ok(
            OperationEffect(
                value="marked" if mark else "removed",
                outcome={"scope": PAGE},
                audit_event_id=applied.unwrap().audit.id,
            )
        )

    return action


def _build(name: str, incident_id: int) -> tuple[OperationRequest, ActionCallable]:
    request = OperationRequest(
        operation_type=name,
        target_ids={"incident_id": str(incident_id), "asset_hash": HASH},
        requested_values={"action": name},
    )
    key = request.key
    if name == "authorize_strike":
        return request, _status_action(
            incident_id, IncidentStatus.STRIKE_AUTHORIZED, AuditEventType.STRIKE_AUTHORIZED, key
        )
    if name == "request_credit":
        return request, _status_action(
            incident_id, IncidentStatus.CREDIT_REQUESTED, AuditEventType.CREDIT_REQUESTED, key
        )
    if name == "mark_fair_use":
        return request, _whitelist_action(mark=True, key=key)
    return request, _whitelist_action(mark=False, key=key)  # remove_fair_use


@given(st.sampled_from(ACTIONS), st.integers(min_value=1, max_value=4))
def test_identical_retries_change_nothing_after_the_first_commit(name: str, retries: int) -> None:
    # Feature: provenance, Property 39: Confirmed material actions are atomic and
    # retry-idempotent
    with temporary_registry() as harness:
        incident_id = _prepare(harness)
        runner = MaterialActionRunner(harness.adapter, FixedClock())
        request, action = _build(name, incident_id)

        first = runner.run(request, action)
        if first.failure is not None:
            # Removing a whitelist entry that does not exist is a legitimate refusal.
            assert first.unwrap_failure().code is FailureCode.NOT_FOUND
            return

        committed = harness.snapshot()
        assert first.unwrap().replayed is False

        for _ in range(retries):
            repeated = runner.run(request, action).unwrap()
            assert repeated.replayed is True
            assert repeated.receipt.operation_key == first.unwrap().receipt.operation_key
            assert repeated.receipt.committed_at == first.unwrap().receipt.committed_at

        assert harness.snapshot() == committed


@given(st.sampled_from(ACTIONS))
def test_one_audit_and_one_receipt_per_committed_action(name: str) -> None:
    # Feature: provenance, Property 39: Confirmed material actions are atomic and
    # retry-idempotent
    with temporary_registry() as harness:
        incident_id = _prepare(harness)
        runner = MaterialActionRunner(harness.adapter, FixedClock())
        request, action = _build(name, incident_id)

        result = runner.run(request, action)
        if result.failure is not None:
            assert result.unwrap_failure().code is FailureCode.NOT_FOUND
            assert harness.count("audit_events") == 0
            assert harness.count("operation_receipts") == 0
            return

        assert harness.count("audit_events") == 1
        assert harness.count("operation_receipts") == 1
        assert result.unwrap().receipt.audit_event_id is not None


@given(st.sampled_from(ACTIONS), st.booleans())
def test_failures_leave_no_partial_state(name: str, fail_after_write: bool) -> None:
    # Feature: provenance, Property 39: Confirmed material actions are atomic and
    # retry-idempotent
    with temporary_registry() as harness:
        incident_id = _prepare(harness)
        runner = MaterialActionRunner(harness.adapter, FixedClock())
        request, action = _build(name, incident_id)
        before = harness.snapshot()

        def failing(uow: UnitOfWork) -> Result[OperationEffect[str]]:
            if fail_after_write:
                action(uow)
            return failed(FailureCode.COMMIT_FAILED, "material_action")

        result = runner.run(request, failing)

        assert result.value is None
        assert result.unwrap_failure().code is FailureCode.COMMIT_FAILED
        assert harness.snapshot() == before


@given(st.lists(st.sampled_from(ACTIONS), min_size=2, max_size=4, unique=True))
def test_distinct_actions_each_get_their_own_receipt(names: list[str]) -> None:
    # Feature: provenance, Property 39: Confirmed material actions are atomic and
    # retry-idempotent
    with temporary_registry() as harness:
        incident_id = _prepare(harness)
        runner = MaterialActionRunner(harness.adapter, FixedClock())
        committed = 0

        for name in names:
            request, action = _build(name, incident_id)
            result = runner.run(request, action)
            if result.failure is None:
                committed += 1

        assert harness.count("operation_receipts") == committed
        assert harness.count("audit_events") == committed
