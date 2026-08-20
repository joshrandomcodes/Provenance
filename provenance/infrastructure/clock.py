"""System clock adapter.

Requirements: 6.12, 8.5
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    """Aware UTC wall-clock plus a monotonic duration source."""

    __slots__ = ()

    def utc_now(self) -> datetime:
        """Current aware UTC time."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Monotonic seconds, unaffected by wall-clock adjustments."""
        return time.monotonic()
