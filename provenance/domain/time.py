"""UTC timestamp codec and clock ports.

Wall-clock values are always aware UTC truncated to whole seconds and formatted as
``YYYY-MM-DDTHH:MM:SSZ``. Durations always use a monotonic clock.

Requirements: 3.2, 3.7, 6.12, 8.5, 18.7
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, NewType, Protocol, runtime_checkable

UtcTimestamp = NewType("UtcTimestamp", str)

TIMESTAMP_LENGTH: Final = 20
TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
_TIMESTAMP_PATTERN: Final = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", re.ASCII)


@runtime_checkable
class Clock(Protocol):
    """Wall-clock and monotonic time source."""

    def utc_now(self) -> datetime:
        """Return the current aware UTC time."""
        ...

    def monotonic(self) -> float:
        """Return a monotonic time in fractional seconds."""
        ...


def truncate_to_second(value: datetime) -> datetime:
    """Return the aware UTC value with microseconds discarded."""
    if value.tzinfo is None:
        message = "timestamps must be timezone aware"
        raise ValueError(message)
    return value.astimezone(UTC).replace(microsecond=0)


def format_utc_timestamp(value: datetime) -> UtcTimestamp:
    """Format an aware datetime as an exact 20-character UTC timestamp."""
    truncated = truncate_to_second(value)
    return UtcTimestamp(truncated.strftime(TIMESTAMP_FORMAT))


def parse_utc_timestamp(value: str) -> datetime | None:
    """Parse an exact UTC timestamp, returning None when it is not valid.

    Rejects any deviation in length, layout, or calendar validity, including
    invalid Gregorian days and out-of-range time components.
    """
    if len(value) != TIMESTAMP_LENGTH or _TIMESTAMP_PATTERN.match(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed


def is_valid_utc_timestamp(value: str) -> bool:
    """True when the value is an exact, calendar-valid UTC timestamp."""
    return parse_utc_timestamp(value) is not None


def now_timestamp(clock: Clock) -> UtcTimestamp:
    """Sample the clock once and format the result."""
    return format_utc_timestamp(clock.utc_now())
