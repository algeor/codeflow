from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(".CodeFlow/config.yaml")


class ConfigError(RuntimeError):
    """Raised when CodeFlow configuration cannot be loaded."""


def resolve_config_path(path: str | None = None) -> Path:
    configured = path or os.getenv("CODEFLOW_CONFIG")
    return Path(configured) if configured else DEFAULT_CONFIG_PATH


def load_project_config(path: str | None = None) -> dict[str, Any]:
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        import yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise ConfigError("Install PyYAML or use the CodeFlow[runtime] extra to load YAML config.") from exc

    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {config_path}")
    return data


def env_settings() -> dict[str | Any, str | None]:
    keys = [
        "CODEFLOW_CONFIG",
        "CODEFLOW_GITHUB_REPO_URL",
        "CODEFLOW_GITHUB_OWNER",
        "CODEFLOW_GITHUB_REPO",
        "CODEFLOW_BASE_BRANCH",
        "CODEFLOW_LOCAL_REPO_PATH",
        "CODEFLOW_DATABASE_URL",
        "CODEFLOW_WORKTREE_ROOT",
        "CODEFLOW_AGENT_CLI",
        "CODEFLOW_ALLOWED_AGENT_CLIS",
        "CODEFLOW_COST_POLICY",
        "CODEFLOW_MAX_ITERATIONS",
        "CODEFLOW_MAX_RUNTIME_MINUTES",
        "CODEFLOW_BILLING_CURRENCY_CODE",
        "CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS",
        "CODEFLOW_POLL_INTERVAL_SECONDS",
        "CODEFLOW_REQUIRE_HUMAN_APPROVAL",
        "CODEFLOW_ENABLE_RAG",
        "CODEFLOW_ENABLE_PR_QUESTIONS",
        "CODEFLOW_DRY_RUN",
    ]
    return {key: value for key in keys if (value := os.getenv(key))}
