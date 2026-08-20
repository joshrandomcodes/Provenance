"""Diagnostics port.

Only allow-listed, non-sensitive values may be recorded, and only locally.

Requirements: 17.1, 17.6, 17.11, 17.12
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

SafeValue = str | int | float | bool | None


class DiagnosticLogger(Protocol):
    """Local, redacting diagnostics sink."""

    def record(self, event: str, fields: Mapping[str, SafeValue] | None = None) -> None:
        """Record one allow-listed diagnostic event."""
        ...
