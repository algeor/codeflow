from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(".CodeFlow/config.yaml")
DEFAULT_ENV_PATH = Path(".env")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REDACTED_ENV_KEYS = {"CODEFLOW_DATABASE_URL"}


class ConfigError(RuntimeError):
    """Raised when CodeFlow configuration cannot be loaded."""


def load_local_env(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    env_path = Path(path or os.getenv("CODEFLOW_ENV_FILE") or DEFAULT_ENV_PATH)
    if not env_path.exists():
        return {}
    if not env_path.is_file():
        raise ConfigError(f"Environment file is not a file: {env_path}")

    values = parse_env_file(env_path.read_text())
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def parse_env_file(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        parsed = _parse_env_line(raw_line, line_number)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def resolve_config_path(path: str | None = None) -> Path:
    configured = path or os.getenv("CODEFLOW_CONFIG")
    return Path(configured) if configured else DEFAULT_CONFIG_PATH


def github_settings(config: dict[str, Any] | None, env: dict[str, str] | None = None) -> dict[str, str]:
    environment = env if env is not None else os.environ
    github_config = config.get("github", {}) if isinstance(config, dict) else {}
    if not isinstance(github_config, dict):
        github_config = {}

    repo_url = str(environment.get("CODEFLOW_GITHUB_REPO_URL") or github_config.get("repo_url") or "").strip()
    owner = str(environment.get("CODEFLOW_GITHUB_OWNER") or github_config.get("owner") or "").strip()
    repo = str(environment.get("CODEFLOW_GITHUB_REPO") or github_config.get("repo") or "").strip()
    base_branch = str(environment.get("CODEFLOW_BASE_BRANCH") or github_config.get("base_branch") or "main").strip()

    if repo_url and (not owner or not repo):
        inferred_owner, inferred_repo = _github_repo_parts_from_url(repo_url)
        owner = owner or inferred_owner
        repo = repo or inferred_repo

    return {
        "repo_url": repo_url,
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "host": str(environment.get("GH_HOST") or "github.com"),
    }


def github_cli_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(env if env is not None else os.environ)
    environment["GH_HOST"] = environment.get("GH_HOST") or "github.com"
    if not environment.get("GH_TOKEN") and environment.get("GITHUB_TOKEN"):
        environment["GH_TOKEN"] = environment["GITHUB_TOKEN"]
    return environment


def allowed_github_reviewers(config: dict[str, Any] | None, env: dict[str, str] | None = None) -> list[str]:
    environment = env if env is not None else os.environ
    configured_env = environment.get("CODEFLOW_ALLOWED_REVIEWERS")
    if configured_env is not None:
        return _split_csv(configured_env)

    github_config = config.get("github", {}) if isinstance(config, dict) else {}
    if not isinstance(github_config, dict):
        return []
    reviewers = github_config.get("allowed_reviewers") or []
    if not isinstance(reviewers, list):
        return []
    return [str(reviewer).strip() for reviewer in reviewers if str(reviewer).strip()]


def workflow_requires_human_approval(config: dict[str, Any] | None, env: dict[str, str] | None = None) -> bool:
    environment = env if env is not None else os.environ
    configured_env = environment.get("CODEFLOW_REQUIRE_HUMAN_APPROVAL")
    if configured_env is not None:
        return _parse_bool(configured_env, default=True)

    workflow_config = config.get("workflow", {}) if isinstance(config, dict) else {}
    if not isinstance(workflow_config, dict):
        return True
    return _parse_bool(workflow_config.get("require_human_approval"), default=True)


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
        "CODEFLOW_ENV_FILE",
        "CODEFLOW_CONFIG",
        "CODEFLOW_GITHUB_REPO_URL",
        "CODEFLOW_GITHUB_OWNER",
        "CODEFLOW_GITHUB_REPO",
        "CODEFLOW_ALLOWED_REVIEWERS",
        "CODEFLOW_BASE_BRANCH",
        "CODEFLOW_LOCAL_REPO_PATH",
        "CODEFLOW_DATABASE_URL",
        "CODEFLOW_WORKTREE_ROOT",
        "CODEFLOW_AGENT_CLI",
        "CODEFLOW_ALLOWED_AGENT_CLIS",
        "CODEFLOW_COST_POLICY",
        "CODEFLOW_EMBEDDING_MODEL",
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
    return {key: _display_env_value(key, value) for key in keys if (value := os.getenv(key))}


def _parse_env_line(raw_line: str, line_number: int) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()

    key, separator, value = line.partition("=")
    if not separator:
        raise ConfigError(f"Invalid .env line {line_number}: missing '='")

    key = key.strip()
    if not _ENV_KEY_RE.fullmatch(key):
        raise ConfigError(f"Invalid .env line {line_number}: invalid key {key!r}")

    return key, _parse_env_value(value.strip())


def _display_env_value(key: str, value: str) -> str:
    if key in _REDACTED_ENV_KEYS:
        return "<redacted>"
    return value


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return _strip_inline_comment(value).strip()


def _strip_inline_comment(value: str) -> str:
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def _github_repo_parts_from_url(repo_url: str) -> tuple[str, str]:
    normalized = repo_url.removesuffix(".git").rstrip("/")
    ssh_match = re.match(r"git@github\.com:([^/]+)/(.+)$", normalized)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    https_match = re.match(r"https://github\.com/([^/]+)/(.+)$", normalized)
    if https_match:
        return https_match.group(1), https_match.group(2)

    return "", ""
