"""One-shot deterministic validation for Provenance.

Runs lint, formatting check, strict type checking, and the deterministic test
suite. Browser and live-network suites stay opt-in and are excluded here.

Requirements: 1.2, 1.5, 1.6, 17.1, 20.1-20.20
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECKS: tuple[tuple[str, list[str]], ...] = (
    ("ruff lint", [sys.executable, "-m", "ruff", "check", "."]),
    ("ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
    ("mypy", [sys.executable, "-m", "mypy"]),
    ("pytest", [sys.executable, "-m", "pytest"]),
)


def main() -> int:
    """Run every check, report a summary, and return a non-zero code on failure."""
    failures: list[str] = []
    for name, command in CHECKS:
        print(f"\n=== {name} ===", flush=True)
        if subprocess.call(command, cwd=str(REPO_ROOT)) != 0:
            failures.append(name)

    print("\n=== summary ===", flush=True)
    if failures:
        print("failed: " + ", ".join(failures), flush=True)
        return 1
    print("all checks passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
