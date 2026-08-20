"""Shared test configuration.

Registers Hypothesis profiles with the minimum example count the design requires
and isolates every test from the developer's real local Registry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

from provenance.settings import HOME_ENV_VAR

REPO_ROOT = Path(__file__).resolve().parents[1]

hypothesis_settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hypothesis_settings.register_profile(
    "thorough",
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hypothesis_settings.load_profile(os.environ.get("PROVENANCE_TEST_PROFILE", "default"))


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture
def provenance_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the application home directory at a temporary location."""
    home = tmp_path / "provenance-home"
    monkeypatch.setenv(HOME_ENV_VAR, str(home))
    return home
