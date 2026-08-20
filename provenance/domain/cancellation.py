"""Cooperative cancellation for bounded, user-initiated operations.

Requirements: 8.10, 8.11, 18.1, 18.9
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from provenance.domain.errors import Failure, FailureCode


@runtime_checkable
class CancellationToken(Protocol):
    """Read-only view of a cancellation request."""

    @property
    def is_cancelled(self) -> bool:
        """True once cancellation has been requested."""
        ...


class CooperativeCancellationToken:
    """Thread-safe cancellation token owned by one operation."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        """True once :meth:`cancel` has been called."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation. Safe to call repeatedly and from any thread."""
        self._event.set()


class NeverCancelled:
    """Token for operations that cannot be cancelled."""

    __slots__ = ()

    @property
    def is_cancelled(self) -> bool:
        """Always False."""
        return False


def cancellation_failure(operation: str) -> Failure:
    """Build the canonical failure for a cancelled operation."""
    return Failure(code=FailureCode.CANCELLED, operation=operation)
