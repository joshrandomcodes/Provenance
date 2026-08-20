"""One-shot launcher for the local Provenance dashboard.

Starts Streamlit with privacy-preserving defaults, bound to loopback only.

Requirements: 1.1, 1.3, 17.1, 17.2
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "provenance" / "app.py"

PRIVACY_ENVIRONMENT = {
    "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
    "STREAMLIT_SERVER_HEADLESS": "true",
    "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
    "STREAMLIT_RUNNER_MAGIC_ENABLED": "false",
}


def main(argv: list[str] | None = None) -> int:
    """Run the dashboard and return the Streamlit exit code."""
    extra_args = [] if argv is None else list(argv)
    environment = dict(os.environ)
    environment.update(PRIVACY_ENVIRONMENT)
    command = [sys.executable, "-m", "streamlit", "run", str(APP_PATH), *extra_args]
    try:
        return subprocess.call(command, cwd=str(REPO_ROOT), env=environment)
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to stop the local dashboard.
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
