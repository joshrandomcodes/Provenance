"""Headless dashboard smoke tests using Streamlit's AppTest.

These run the real script in process, so they catch wiring errors, missing widgets, and
exceptions during a render without needing a browser.

Requirements: 1.1, 6.10, 17.12, 19.1, 21.4, 21.5
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from provenance.settings import HOME_ENV_VAR
from provenance.ui.dashboard import TAB_LABELS
from provenance.ui.forge_view import SUBMIT_LABEL

pytestmark = pytest.mark.integration

APP_PATH = Path(__file__).resolve().parents[1] / "provenance" / "app.py"
RUN_TIMEOUT_SECONDS = 60


def _run(home: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv(HOME_ENV_VAR, str(home))
    app = AppTest.from_file(str(APP_PATH), default_timeout=RUN_TIMEOUT_SECONDS)
    app.run()
    return app


def test_the_dashboard_renders_without_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(tmp_path / "home", monkeypatch)

    assert not app.exception


def test_the_three_named_tabs_are_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(tmp_path / "home", monkeypatch)

    headers = [element.value for element in app.header]
    assert list(TAB_LABELS) == ["The Forge", "Web Radar", "Incident Triage"]
    for label in TAB_LABELS:
        assert label in headers


def test_the_forge_form_exposes_its_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(tmp_path / "home", monkeypatch)

    labels = [element.label for element in app.text_input]
    assert "Creator ID" in labels
    assert "Display name" in labels
    assert any(button.label == SUBMIT_LABEL for button in app.button)


def test_submitting_an_empty_form_reports_field_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(tmp_path / "home", monkeypatch)

    app.button[0].click().run()

    assert not app.exception
    rendered = " ".join(element.value for element in app.text)
    assert "Fix these fields" in rendered or "Choose an image" in rendered


def test_the_status_panel_shows_local_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    app = _run(home, monkeypatch)

    rendered = " ".join(element.value for element in app.text)
    assert "Registry writes: enabled" in rendered
    assert "Telemetry" in rendered
    assert "registry.sqlite3" in rendered
    # A local path is shown as plain text, never as a link.
    assert "://" not in rendered.split("Outbound user agent")[0]


def test_the_registry_is_created_under_the_configured_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"

    _run(home, monkeypatch)

    assert (home / "registry.sqlite3").exists()


def test_a_failed_registry_disables_saving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "registry.sqlite3").write_bytes(b"not a database")

    app = _run(home, monkeypatch)

    assert not app.exception
    rendered = " ".join(element.value for element in app.text)
    assert "Registry writes: disabled" in rendered
    assert any("startup checks" in message.value for message in app.error)
    assert any("Back up the registry" in warning.value for warning in app.warning)
