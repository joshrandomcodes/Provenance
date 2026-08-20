"""Project configuration checks: exact pins, privacy defaults, and local-only paths.

Requirements: 1.1, 1.2, 1.3, 1.4, 17.1, 17.2, 17.5
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from provenance import settings as settings_module
from provenance.settings import (
    PrivacyPolicy,
    RuntimeSettings,
    default_home_directory,
    load_runtime_settings,
)

REQUIRED_RUNTIME_PACKAGES = frozenset(
    {"streamlit", "pillow", "numpy", "requests", "beautifulsoup4", "python-whois"}
)
REQUIRED_DEV_PACKAGES = frozenset({"pytest", "hypothesis", "mypy", "ruff"})

pytestmark = pytest.mark.unit


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _parse_pins(lines: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if line == "" or line.startswith("#"):
            continue
        assert "==" in line, f"dependency is not pinned exactly: {line}"
        name, version = line.split("==", 1)
        pins[_normalize(name)] = version.strip()
    return pins


@pytest.fixture
def pyproject(repo_root: Path) -> dict[str, object]:
    return tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.fixture
def requirement_pins(repo_root: Path) -> dict[str, str]:
    text = (repo_root / "requirements.txt").read_text(encoding="utf-8")
    return _parse_pins(text.splitlines())


def test_python_version_is_constrained(pyproject: dict[str, object]) -> None:
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert project["requires-python"] == ">=3.13,<3.14"


def test_runtime_dependencies_are_pinned_and_complete(pyproject: dict[str, object]) -> None:
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    pins = _parse_pins([str(item) for item in dependencies])
    assert pins.keys() >= REQUIRED_RUNTIME_PACKAGES


def test_development_dependencies_are_pinned_and_complete(pyproject: dict[str, object]) -> None:
    project = pyproject["project"]
    assert isinstance(project, dict)
    optional = project["optional-dependencies"]
    assert isinstance(optional, dict)
    dev = optional["dev"]
    assert isinstance(dev, list)
    pins = _parse_pins([str(item) for item in dev])
    assert pins.keys() >= REQUIRED_DEV_PACKAGES


def test_declared_pins_match_requirements_file(
    pyproject: dict[str, object], requirement_pins: dict[str, str]
) -> None:
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    optional = project["optional-dependencies"]
    assert isinstance(dependencies, list)
    assert isinstance(optional, dict)
    dev = optional["dev"]
    assert isinstance(dev, list)

    declared = _parse_pins([str(item) for item in dependencies])
    declared.update(_parse_pins([str(item) for item in dev]))

    for name, version in declared.items():
        assert name in requirement_pins, f"{name} is missing from requirements.txt"
        assert requirement_pins[name] == version, (
            f"{name} is pinned to {version} in pyproject.toml "
            f"but {requirement_pins[name]} in requirements.txt"
        )


def test_pytest_markers_exclude_optional_suites_by_default(pyproject: dict[str, object]) -> None:
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    pytest_config = tool["pytest"]
    assert isinstance(pytest_config, dict)
    ini_options = pytest_config["ini_options"]
    assert isinstance(ini_options, dict)

    markers = ini_options["markers"]
    assert isinstance(markers, list)
    marker_names = {str(entry).split(":", 1)[0] for entry in markers}
    assert {"unit", "integration", "contract", "browser", "live"} <= marker_names

    addopts = str(ini_options["addopts"])
    assert "not live" in addopts
    assert "not browser" in addopts
    assert "--strict-markers" in addopts


def test_streamlit_config_disables_usage_stats_and_binds_loopback(repo_root: Path) -> None:
    config = tomllib.loads((repo_root / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    browser = config["browser"]
    server = config["server"]
    assert isinstance(browser, dict)
    assert isinstance(server, dict)
    assert browser["gatherUsageStats"] is False
    assert server["address"] == "127.0.0.1"
    assert server["headless"] is True


def test_privacy_defaults_are_all_disabled() -> None:
    policy = PrivacyPolicy()
    assert policy.telemetry_enabled is False
    assert policy.analytics_enabled is False
    assert policy.cloud_storage_enabled is False
    assert policy.remote_logging_enabled is False
    assert policy.inherit_environment_proxies is False
    assert policy.persist_scraped_image_bytes is False


def test_user_agent_contains_project_information_url() -> None:
    assert settings_module.PROJECT_INFO_URL in settings_module.USER_AGENT
    assert settings_module.PROJECT_INFO_URL.startswith("https://")


def test_settings_resolve_registry_outside_the_source_package(provenance_home: Path) -> None:
    settings = load_runtime_settings()
    package_directory = Path(settings_module.__file__).resolve().parent

    assert settings.home_directory == provenance_home
    assert settings.registry_path == provenance_home / "registry.sqlite3"
    assert package_directory not in settings.registry_path.resolve().parents
    assert settings.local_diagnostic_log_enabled is False


def test_loading_settings_creates_no_files(provenance_home: Path) -> None:
    load_runtime_settings()
    assert not provenance_home.exists()


def test_ensure_directories_creates_home_but_not_diagnostics(provenance_home: Path) -> None:
    settings = load_runtime_settings()
    settings.ensure_directories()

    assert settings.home_directory.is_dir()
    assert not settings.diagnostics_directory.exists()


def test_diagnostic_log_requires_explicit_opt_in(
    provenance_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(settings_module.DIAGNOSTIC_LOG_ENV_VAR, "true")
    settings = load_runtime_settings()
    settings.ensure_directories()

    assert settings.local_diagnostic_log_enabled is True
    assert settings.diagnostics_directory.is_dir()


def test_default_home_directory_uses_platform_data_location() -> None:
    windows_home = default_home_directory({"LOCALAPPDATA": r"C:\Users\example\AppData\Local"})
    posix_home = default_home_directory({"XDG_DATA_HOME": "/home/example/.local/share"})

    assert windows_home.name.lower() == "provenance"
    assert posix_home.name.lower() == "provenance"


def test_runtime_settings_are_immutable(provenance_home: Path) -> None:
    settings = load_runtime_settings()
    assert isinstance(settings, RuntimeSettings)
    with pytest.raises((AttributeError, TypeError)):
        settings.registry_path = Path("elsewhere.sqlite3")  # type: ignore[misc]
