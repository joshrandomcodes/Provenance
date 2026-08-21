"""One scan session: a worker thread, a progress queue, and a cancellation token.

A scan may run for up to two minutes, so it cannot block the interface. This module owns
the worker so the UI layer only ever reads snapshots and sets flags, and so the whole
state machine is testable without Streamlit.

The robots pause is the interesting part. When robots.txt cannot be read, the scan stops
in ``AWAITING_ROBOTS`` and waits for the user to continue or abandon. Critically, the
``ScanBudget`` is created once by ``start`` and reused by ``resume``, so the 120-second
scan deadline keeps running across the pause rather than restarting. A user who thinks for
a minute has a minute less scanning, which is what Requirement 8.11 requires.

At most one scan runs per session. Starting again is refused while a scan is live, which
is what keeps a single cancellation token and a single progress queue meaningful.

Requirements: 8.3, 8.10, 8.11, 18.1, 18.3, 19.9, 21.1, 21.6
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from provenance.application.scan import (
    ScanProgress,
    ScanReport,
    ScanRequest,
    ScanStage,
)
from provenance.domain.cancellation import CooperativeCancellationToken
from provenance.domain.errors import Failure, FailureCode, Result
from provenance.domain.scan_budget import ScanBudget, ScanLimits
from provenance.domain.time import Clock
from provenance.infrastructure.network.robots import RobotsDecision

SESSION_OPERATION: Final = "scan_session"
WORKER_NAME: Final = "provenance-scan"
JOIN_TIMEOUT_SECONDS: Final = 5.0

DETAIL_ALREADY_RUNNING: Final = "scan_already_running"
DETAIL_NOT_PAUSED: Final = "scan_not_awaiting_robots_decision"
DETAIL_WORKER_CRASHED: Final = "scan_worker_failed"


class ScanRunner(Protocol):
    """The two-phase scan surface this session drives.

    Narrower than ``ScanService`` on purpose, so the session depends on the robots-then-run
    contract rather than on a concrete implementation.
    """

    def evaluate_robots(
        self, request: ScanRequest, budget: ScanBudget
    ) -> Result[RobotsDecision]: ...

    def run(
        self, request: ScanRequest, budget: ScanBudget, *, robots: RobotsDecision
    ) -> Result[ScanReport]: ...


class SessionState(StrEnum):
    """Where one scan session currently stands."""

    IDLE = "idle"
    RUNNING = "running"
    AWAITING_ROBOTS = "awaiting_robots"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """An immutable view of the session, safe to render."""

    state: SessionState
    progress: ScanProgress | None = None
    report: ScanReport | None = None
    failure: Failure | None = None
    robots: RobotsDecision | None = None

    @property
    def is_running(self) -> bool:
        """True while the worker is active."""
        return self.state is SessionState.RUNNING

    @property
    def awaiting_robots_decision(self) -> bool:
        """True when the user must choose to continue or stop."""
        return self.state is SessionState.AWAITING_ROBOTS


class ScanSession:
    """Owns at most one running scan, its token, its queue, and its outcome."""

    __slots__ = (
        "_service",
        "_clock",
        "_limits",
        "_lock",
        "_state",
        "_thread",
        "_token",
        "_budget",
        "_queue",
        "_latest",
        "_request",
        "_robots",
        "_report",
        "_failure",
    )

    def __init__(self, service: ScanRunner, clock: Clock, limits: ScanLimits | None = None) -> None:
        self._service = service
        self._clock = clock
        self._limits = limits or ScanLimits()
        self._lock = threading.Lock()
        self._state = SessionState.IDLE
        self._thread: threading.Thread | None = None
        self._token = CooperativeCancellationToken()
        self._budget: ScanBudget | None = None
        self._queue: queue.Queue[ScanProgress] = queue.Queue()
        self._latest: ScanProgress | None = None
        self._request: ScanRequest | None = None
        self._robots: RobotsDecision | None = None
        self._report: ScanReport | None = None
        self._failure: Failure | None = None

    def snapshot(self) -> SessionSnapshot:
        """Read the current state without blocking the worker."""
        self._drain()
        with self._lock:
            return SessionSnapshot(
                state=self._state,
                progress=self._latest,
                report=self._report,
                failure=self._failure,
                robots=self._robots,
            )

    def start(self, request: ScanRequest) -> Failure | None:
        """Begin one scan. Returns a failure when a scan is already live."""
        with self._lock:
            if self._state in {SessionState.RUNNING, SessionState.AWAITING_ROBOTS}:
                return Failure(
                    code=FailureCode.BUSY,
                    operation=SESSION_OPERATION,
                    safe_detail=DETAIL_ALREADY_RUNNING,
                )
            self._reset_locked()
            # One budget per session start. `resume` deliberately reuses it.
            self._budget = ScanBudget(self._limits, self._clock, self._token)
            self._request = self._instrument(request)
            self._state = SessionState.RUNNING

        self._spawn(self._run_from_robots)
        return None

    def resume(self) -> Failure | None:
        """Continue a scan whose robots.txt could not be read, on the same budget."""
        with self._lock:
            if self._state is not SessionState.AWAITING_ROBOTS:
                return Failure(
                    code=FailureCode.BUSY,
                    operation=SESSION_OPERATION,
                    safe_detail=DETAIL_NOT_PAUSED,
                )
            if self._request is None or self._robots is None or self._budget is None:
                return Failure(
                    code=FailureCode.INTERNAL_ERROR,
                    operation=SESSION_OPERATION,
                    safe_detail=DETAIL_NOT_PAUSED,
                )
            # The approval is recorded on the request the worker will read.
            self._request = ScanRequest(
                page_url=self._request.page_url,
                acknowledged=self._request.acknowledged,
                robots_unavailable_approved=True,
                progress=self._request.progress,
            )
            self._state = SessionState.RUNNING

        self._spawn(self._run_from_page)
        return None

    def cancel(self) -> None:
        """Ask the running scan to stop. Safe to call in any state."""
        self._token.cancel()

    def reset(self) -> None:
        """Clear the session for a new scan, cancelling any live worker first."""
        self._token.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        with self._lock:
            self._reset_locked()

    # Worker bodies -----------------------------------------------------------------

    def _run_from_robots(self) -> None:
        request, budget = self._current()
        if request is None or budget is None:
            return

        decision = self._service.evaluate_robots(request, budget)
        if decision.failure is not None:
            self._finish_failed(decision.failure)
            return

        robots = decision.unwrap()
        with self._lock:
            self._robots = robots

        if robots.needs_user_decision and not request.robots_unavailable_approved:
            # Pause. The budget keeps running while the user decides.
            with self._lock:
                self._state = SessionState.AWAITING_ROBOTS
            return

        self._execute(request, budget, robots)

    def _run_from_page(self) -> None:
        request, budget = self._current()
        with self._lock:
            robots = self._robots
        if request is None or budget is None or robots is None:
            return
        self._execute(request, budget, robots)

    def _execute(self, request: ScanRequest, budget: ScanBudget, robots: RobotsDecision) -> None:
        outcome = self._service.run(request, budget, robots=robots)
        if outcome.failure is not None:
            self._finish_failed(outcome.failure)
            return
        with self._lock:
            self._report = outcome.unwrap()
            self._state = SessionState.FINISHED

    # Internals ---------------------------------------------------------------------

    def _spawn(self, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=self._guarded(target), name=WORKER_NAME, daemon=True)
        self._thread = thread
        thread.start()

    def _guarded(self, target: Callable[[], None]) -> Callable[[], None]:
        """Wrap a worker body so an unexpected error becomes a visible failed state."""

        def body() -> None:
            try:
                target()
            except Exception:  # noqa: BLE001 - a worker crash must not vanish silently
                self._finish_failed(
                    Failure(
                        code=FailureCode.INTERNAL_ERROR,
                        operation=SESSION_OPERATION,
                        safe_detail=DETAIL_WORKER_CRASHED,
                    )
                )

        return body

    def _instrument(self, request: ScanRequest) -> ScanRequest:
        """Attach the queue sink, preserving any sink the caller supplied."""
        caller_sink = request.progress

        def sink(snapshot: ScanProgress) -> None:
            self._queue.put(snapshot)
            if caller_sink is not None:
                caller_sink(snapshot)

        return ScanRequest(
            page_url=request.page_url,
            acknowledged=request.acknowledged,
            robots_unavailable_approved=request.robots_unavailable_approved,
            progress=sink,
        )

    def _current(self) -> tuple[ScanRequest | None, ScanBudget | None]:
        with self._lock:
            return self._request, self._budget

    def _finish_failed(self, failure: Failure) -> None:
        with self._lock:
            self._failure = failure
            self._state = SessionState.FAILED

    def _drain(self) -> None:
        """Move queued snapshots into the latest-known progress value."""
        while True:
            try:
                snapshot = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._latest = snapshot

    def _reset_locked(self) -> None:
        """Clear every per-scan value. The caller must hold the lock."""
        self._state = SessionState.IDLE
        self._token = CooperativeCancellationToken()
        self._budget = None
        self._queue = queue.Queue()
        self._latest = None
        self._request = None
        self._robots = None
        self._report = None
        self._failure = None

    @property
    def final_stage_reached(self) -> bool:
        """True once no further worker activity is expected."""
        with self._lock:
            return self._state in {SessionState.FINISHED, SessionState.FAILED}

    @property
    def last_stage(self) -> ScanStage | None:
        """Stage of the most recent progress snapshot."""
        with self._lock:
            return None if self._latest is None else self._latest.stage
