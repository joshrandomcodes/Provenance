"""Local runtime settings and non-negotiable privacy defaults.

This module is framework-agnostic so every layer may depend on it. It resolves
filesystem locations outside the source package and states the privacy posture
that the rest of the application must honor.

Requirements: 1.3, 1.4, 8.13, 17.1, 17.2, 17.5, 17.12
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

APPLICATION_NAME: Final = "Provenance"
APPLICATION_VERSION: Final = "0.1.0"

# Project information URL published in the outbound user agent, so site operators can
# identify the scanner and find the project.
PROJECT_INFO_URL: Final = "https://github.com/joshrandomcodes/Provenance"
USER_AGENT: Final = f"{APPLICATION_NAME}/{APPLICATION_VERSION} (+{PROJECT_INFO_URL})"

HOME_ENV_VAR: Final = "PROVENANCE_HOME"
DIAGNOSTIC_LOG_ENV_VAR: Final = "PROVENANCE_ENABLE_LOCAL_DIAGNOSTIC_LOG"

REGISTRY_FILE_NAME: Final = "registry.sqlite3"
DIAGNOSTICS_DIRECTORY_NAME: Final = "diagnostics"

_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    """Privacy guarantees that no configuration switch may relax.

    Requirements: 17.1, 17.2, 17.5
    """

    telemetry_enabled: bool = False
    analytics_enabled: bool = False
    cloud_storage_enabled: bool = False
    remote_logging_enabled: bool = False
    inherit_environment_proxies: bool = False
    persist_scraped_image_bytes: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Resolved local paths, outbound identity, and privacy posture."""

    home_directory: Path
    registry_path: Path
    diagnostics_directory: Path
    local_diagnostic_log_enabled: bool
    user_agent: str = USER_AGENT
    project_info_url: str = PROJECT_INFO_URL
    privacy: PrivacyPolicy = field(default_factory=PrivacyPolicy)

    def ensure_directories(self) -> None:
        """Create the local home directory, and diagnostics only when enabled."""
        self.home_directory.mkdir(parents=True, exist_ok=True)
        if self.local_diagnostic_log_enabled:
            self.diagnostics_directory.mkdir(parents=True, exist_ok=True)


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUE_VALUES


def default_home_directory(env: Mapping[str, str] | None = None) -> Path:
    """Return the per-user data directory that holds the Registry.

    The location is always outside the source package so application data never
    mixes with code or version-controlled files.
    """
    environment = os.environ if env is None else env

    override = environment.get(HOME_ENV_VAR)
    if override is not None and override.strip() != "":
        return Path(override).expanduser()

    if os.name == "nt":
        local_app_data = environment.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / APPLICATION_NAME

    xdg_data_home = environment.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APPLICATION_NAME.lower()


def load_runtime_settings(env: Mapping[str, str] | None = None) -> RuntimeSettings:
    """Resolve runtime settings from the environment without creating files."""
    environment = os.environ if env is None else env
    home = default_home_directory(environment)
    return RuntimeSettings(
        home_directory=home,
        registry_path=home / REGISTRY_FILE_NAME,
        diagnostics_directory=home / DIAGNOSTICS_DIRECTORY_NAME,
        local_diagnostic_log_enabled=_is_truthy(environment.get(DIAGNOSTIC_LOG_ENV_VAR)),
    )
