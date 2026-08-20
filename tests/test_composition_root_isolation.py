"""The production composition root must import only production code.

Importing ``provenance.app`` may not pull in test fixtures, fake clocks, mock
resolvers, synthetic evidence providers, or development-only tooling, and may not
touch the filesystem.

Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 17.1, 17.2
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from provenance.settings import HOME_ENV_VAR
from provenance.ui.dashboard import TAB_LABELS

FORBIDDEN_TOP_LEVEL_MODULES = frozenset(
    {
        "hypothesis",
        "playwright",
        "pytest",
        "_pytest",
        "tests",
        "unittest",
        "smtplib",
        "ftplib",
        "telnetlib",
    }
)
FORBIDDEN_NAME_FRAGMENTS = ("fake", "mock", "stub", "fixture", "synthetic", "dummy", "sample_data")

# Third-party packages may legitimately ship internally named helpers, for example
# streamlit.runtime.caching.storage.dummy_cache_storage. The fragment scan targets
# first-party modules, where a test double would actually be a defect, and skips
# vendor internals listed here.
VENDOR_ALLOWED_PREFIXES = ("streamlit.", "altair.", "pandas.", "numpy.", "PIL.", "urllib3.")
FIRST_PARTY_ROOT = "provenance"

_IMPORT_PROBE = """
import json, sys
import provenance.app
print(json.dumps(sorted(sys.modules)))
"""

pytestmark = pytest.mark.unit


def _imported_modules(repo_root: Path, home: Path) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={
            "PATH": "",
            "SYSTEMROOT": "C:\\Windows",
            HOME_ENV_VAR: str(home),
            "PYTHONPATH": str(repo_root),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert isinstance(payload, list)
    return [str(name) for name in payload]


@pytest.fixture(scope="module")
def imported_modules(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path_factory.mktemp("import-probe-home")
    return _imported_modules(repo_root, home)


def test_composition_root_imports_successfully(imported_modules: list[str]) -> None:
    assert "provenance.app" in imported_modules
    assert "provenance.ui.dashboard" in imported_modules
    assert "streamlit" in imported_modules


def test_composition_root_excludes_test_and_development_modules(
    imported_modules: list[str],
) -> None:
    top_level = {name.split(".", 1)[0] for name in imported_modules}
    assert not (top_level & FORBIDDEN_TOP_LEVEL_MODULES)


def _contains_forbidden_fragment(name: str) -> bool:
    lowered = name.lower()
    return any(fragment in lowered for fragment in FORBIDDEN_NAME_FRAGMENTS)


def test_first_party_modules_contain_no_simulated_evidence_providers(
    imported_modules: list[str],
) -> None:
    first_party = [
        name
        for name in imported_modules
        if name == FIRST_PARTY_ROOT or name.startswith(f"{FIRST_PARTY_ROOT}.")
    ]
    assert first_party, "the probe did not import any first-party modules"

    offenders = [name for name in first_party if _contains_forbidden_fragment(name)]
    assert offenders == []


def test_no_unexpected_third_party_test_doubles_are_imported(
    imported_modules: list[str],
) -> None:
    offenders = [
        name
        for name in imported_modules
        if _contains_forbidden_fragment(name)
        and not name.startswith(VENDOR_ALLOWED_PREFIXES)
        and not (name == FIRST_PARTY_ROOT or name.startswith(f"{FIRST_PARTY_ROOT}."))
    ]
    assert offenders == []


def test_importing_app_touches_no_local_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "untouched-home"

    _imported_modules(repo_root, home)

    assert not home.exists()


def test_dashboard_declares_the_three_required_tabs() -> None:
    assert TAB_LABELS == ("The Forge", "Web Radar", "Incident Triage")
