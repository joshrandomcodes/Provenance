"""Scan session state machine: one worker, one token, one budget across the robots pause.

A stub service stands in for the real scan so thread transitions are deterministic. The
real stack is exercised by the scan service tests.

Requirements: 8.3, 8.10, 8.11, 18.1, 18.3, 19.9, 21.1, 21.6
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

from provenance.application.scan import (
    ScanProgress,
    ScanReport,
    ScanRequest,
    ScanStage,
)
from provenance.application.scan_session import ScanRunner, ScanSession, SessionState
from provenance.domain.errors import Failure, FailureCode, Result, failed, ok
from provenance.domain.models import NormalizedUrl, ScanSummary, ScanTerminalReason
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.time import Clock
from provenance.domain.urls import AbsoluteHttpUrl
from provenance.infrastructure.network.robots import RobotsDecision, RobotsVerdict

pytestmark = pytest.mark.unit

PAGE = AbsoluteHttpUrl(scheme="https", host="shop.example", port=443, path="/product")
SETTLE_SECONDS = 2.0

ALLOWED = RobotsDecision(
    verdict=RobotsVerdict.ALLOWED, robots_url="https://shop.example/robots.txt", status=200
)
UNAVAILABLE = RobotsDecision(
    verdict=RobotsVerdict.UNAVAILABLE, robots_url="https://shop.example/robots.txt", status=503
)


class FrozenClock:
    """Clock that never advances."""

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, tzinfo=UTC)

    def monotonic(self) -> float:
        return 100.0


class AdvanceableClock:
    """Monotonic clock moved forward explicitly, to simulate a user deliberating."""

    def __init__(self) -> None:
        self.seconds = 100.0

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 21, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def _summary(reason: ScanTerminalReason = ScanTerminalReason.COMPLETED) -> ScanSummary:
    return ScanSummary(
        discovered=0,
        attempted=0,
        verified=0,
        no_watermark=0,
        corrupt=0,
        unregistered=0,
        failed=0,
        cancelled=0,
        skipped=0,
        total_response_bytes=0,
        elapsed_seconds=0.0,
        terminal_reason=reason,
    )


class StubService:
    """Stands in for ScanService, recording calls and honouring gates."""

    def __init__(
        self,
        *,
        robots: RobotsDecision = ALLOWED,
        robots_failure: Failure | None = None,
        run_failure: Failure | None = None,
        crash: bool = False,
    ) -> None:
        self.robots = robots
        self.robots_failure = robots_failure
        self.run_failure = run_failure
        self.crash = crash
        self.robots_calls = 0
        self.run_calls = 0
        self.approvals: list[bool] = []
        self.budgets: list[ScanBudget] = []
        self.release = threading.Event()
        self.release.set()

    def evaluate_robots(self, request: ScanRequest, budget: ScanBudget) -> Result[RobotsDecision]:
        self.robots_calls += 1
        if request.progress is not None:
            request.progress(ScanProgress(stage=ScanStage.ROBOTS))
        if self.robots_failure is not None:
            return Result(failure=self.robots_failure)
        return ok(self.robots)

    def run(
        self, request: ScanRequest, budget: ScanBudget, *, robots: RobotsDecision
    ) -> Result[ScanReport]:
        self.run_calls += 1
        self.approvals.append(request.robots_unavailable_approved)
        self.budgets.append(budget)
        if self.crash:
            message = "worker exploded"
            raise RuntimeError(message)
        self.release.wait(timeout=SETTLE_SECONDS)
        if request.progress is not None:
            request.progress(ScanProgress(stage=ScanStage.FINISHED))
        if self.run_failure is not None:
            return Result(failure=self.run_failure)
        return ok(ScanReport(page_url=NormalizedUrl(str(PAGE.normalized)), summary=_summary()))


def _session(service: StubService, clock: Clock | None = None) -> ScanSession:
    return ScanSession(service, clock or FrozenClock(), ScanLimits())


def _request(*, acknowledged: bool = True) -> ScanRequest:
    return ScanRequest(page_url=PAGE, acknowledged=acknowledged)


def _await_state(session: ScanSession, expected: SessionState) -> SessionState:
    """Poll until the session reaches a state, or give up after a bounded wait."""
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        state = session.snapshot().state
        if state is expected:
            return state
        time.sleep(0.01)
    return session.snapshot().state


def test_a_new_session_is_idle() -> None:
    session = _session(StubService())
    snapshot = session.snapshot()

    assert snapshot.state is SessionState.IDLE
    assert snapshot.report is None
    assert snapshot.is_running is False


def test_a_started_scan_reaches_a_finished_report() -> None:
    service = StubService()
    session = _session(service)

    assert session.start(_request()) is None
    state = _await_state(session, SessionState.FINISHED)

    assert state is SessionState.FINISHED
    snapshot = session.snapshot()
    assert snapshot.report is not None
    assert snapshot.report.summary.terminal_reason is ScanTerminalReason.COMPLETED
    assert service.robots_calls == 1
    assert service.run_calls == 1


def test_progress_snapshots_are_readable_from_the_main_thread() -> None:
    session = _session(StubService())
    session.start(_request())
    _await_state(session, SessionState.FINISHED)

    snapshot = session.snapshot()

    assert snapshot.progress is not None
    assert snapshot.progress.stage is ScanStage.FINISHED
    assert session.last_stage is ScanStage.FINISHED


def test_starting_twice_while_running_is_refused() -> None:
    service = StubService()
    service.release.clear()
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.RUNNING)

    second = session.start(_request())

    assert second is not None
    assert second.code is FailureCode.BUSY
    service.release.set()
    _await_state(session, SessionState.FINISHED)
    assert service.run_calls == 1


def test_unavailable_robots_pauses_without_running_the_scan() -> None:
    service = StubService(robots=UNAVAILABLE)
    session = _session(service)
    session.start(_request())

    state = _await_state(session, SessionState.AWAITING_ROBOTS)

    assert state is SessionState.AWAITING_ROBOTS
    assert session.snapshot().awaiting_robots_decision is True
    assert service.run_calls == 0


def test_resuming_a_paused_scan_records_the_approval() -> None:
    service = StubService(robots=UNAVAILABLE)
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.AWAITING_ROBOTS)

    assert session.resume() is None
    _await_state(session, SessionState.FINISHED)

    assert service.run_calls == 1
    assert service.approvals == [True]
    assert service.robots_calls == 1


def test_the_scan_deadline_keeps_running_across_the_robots_pause() -> None:
    """Time spent deciding is time taken off the scan, per Requirement 8.11."""
    service = StubService(robots=UNAVAILABLE)
    clock = AdvanceableClock()
    session = _session(service, clock)
    session.start(_request())
    _await_state(session, SessionState.AWAITING_ROBOTS)

    # The user deliberates for thirty seconds.
    clock.advance(30.0)
    session.resume()
    _await_state(session, SessionState.FINISHED)

    assert len(service.budgets) == 1
    assert service.budgets[0].elapsed_seconds >= 30.0


def test_resuming_when_not_paused_is_refused() -> None:
    session = _session(StubService())

    failure = session.resume()

    assert failure is not None
    assert failure.code is FailureCode.BUSY


def test_starting_after_a_pause_is_refused_until_reset() -> None:
    service = StubService(robots=UNAVAILABLE)
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.AWAITING_ROBOTS)

    refused = session.start(_request())

    assert refused is not None
    assert refused.code is FailureCode.BUSY


def test_reset_returns_a_paused_session_to_idle() -> None:
    service = StubService(robots=UNAVAILABLE)
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.AWAITING_ROBOTS)

    session.reset()

    assert session.snapshot().state is SessionState.IDLE
    assert session.start(_request()) is None


def test_a_robots_failure_becomes_a_failed_session() -> None:
    failure = failed(FailureCode.MISSING_ACKNOWLEDGEMENT, "x").unwrap_failure()
    service = StubService(robots_failure=failure)
    session = _session(service)
    session.start(_request())

    state = _await_state(session, SessionState.FAILED)

    assert state is SessionState.FAILED
    snapshot = session.snapshot()
    assert snapshot.failure is not None
    assert snapshot.failure.code is FailureCode.MISSING_ACKNOWLEDGEMENT
    assert service.run_calls == 0


def test_a_run_failure_becomes_a_failed_session() -> None:
    failure = failed(FailureCode.CHECKS_FAILED, "x").unwrap_failure()
    session = _session(StubService(run_failure=failure))
    session.start(_request())

    _await_state(session, SessionState.FAILED)
    snapshot = session.snapshot()

    assert snapshot.failure is not None
    assert snapshot.failure.code is FailureCode.CHECKS_FAILED
    assert snapshot.report is None


def test_a_crashing_worker_becomes_a_visible_failure() -> None:
    """An unexpected error must surface as a failed state, not a silent dead thread."""
    session = _session(StubService(crash=True))
    session.start(_request())

    _await_state(session, SessionState.FAILED)
    snapshot = session.snapshot()

    assert snapshot.state is SessionState.FAILED
    assert snapshot.failure is not None
    assert snapshot.failure.code is FailureCode.INTERNAL_ERROR


def test_cancelling_is_safe_before_a_scan_starts() -> None:
    session = _session(StubService())

    session.cancel()

    assert session.snapshot().state is SessionState.IDLE


def test_cancelling_marks_the_shared_token_for_the_running_budget() -> None:
    service = StubService()
    service.release.clear()
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.RUNNING)

    session.cancel()
    budget = service.budgets[0]

    assert budget.is_cancelled is True
    service.release.set()
    _await_state(session, SessionState.FINISHED)


def test_reset_clears_a_finished_report() -> None:
    session = _session(StubService())
    session.start(_request())
    _await_state(session, SessionState.FINISHED)

    session.reset()
    snapshot = session.snapshot()

    assert snapshot.state is SessionState.IDLE
    assert snapshot.report is None
    assert snapshot.progress is None


def test_a_new_start_after_finishing_uses_a_fresh_budget() -> None:
    service = StubService()
    session = _session(service)
    session.start(_request())
    _await_state(session, SessionState.FINISHED)

    session.reset()
    session.start(_request())
    _await_state(session, SessionState.FINISHED)

    assert len(service.budgets) == 2
    assert service.budgets[0] is not service.budgets[1]


def test_a_caller_supplied_progress_sink_still_receives_snapshots() -> None:
    seen: list[ScanStage] = []
    session = _session(StubService())
    request = ScanRequest(
        page_url=PAGE, acknowledged=True, progress=lambda snap: seen.append(snap.stage)
    )

    session.start(request)
    _await_state(session, SessionState.FINISHED)

    assert ScanStage.ROBOTS in seen
    assert ScanStage.FINISHED in seen


def test_the_stub_satisfies_the_runner_protocol() -> None:
    """Structural check: mypy proves the stub matches the contract the session needs."""
    runner: ScanRunner = StubService()

    assert runner is not None
