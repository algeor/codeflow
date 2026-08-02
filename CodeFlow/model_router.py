from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_discovery import find_executable


@dataclass(frozen=True)
class ModelRoute:
    provider_cli: str
    model_tier: str
    model_id: str
    cost_policy: str
    reason: str


VALID_MODEL_TIERS = {"cheap", "balanced", "strong"}
STRONG_TASKS = {"security_review", "test_quality_review", "final_blocking_review"}
CHEAP_TASKS = {"implement_validation", "summarization", "rag_indexing", "status"}
DEFAULT_MODEL_PROFILES: dict[str, dict[str, str]] = {
    "cheap": {"codex": "gpt-5-mini", "claude": "haiku"},
    "balanced": {"codex": "gpt-5", "claude": "sonnet"},
    "strong": {"codex": "gpt-5", "claude": "opus"},
}


def route_task(
    task_type: str,
    config: dict[str, Any],
    *,
    pinned_cli: str | None = None,
    pinned_model_tier: str | None = None,
    failed_attempts: int = 0,
    security_sensitive: bool = False,
) -> ModelRoute:
    agent_config = config.get("agent", {}) if isinstance(config.get("agent", {}), dict) else {}
    cost_policy = _configured_cost_policy(agent_config)
    provider_cli = _select_provider_cli(agent_config, pinned_cli=pinned_cli)

    if pinned_model_tier:
        model_tier = pinned_model_tier
        return ModelRoute(
            provider_cli,
            model_tier,
            _select_model_id(agent_config, model_tier, provider_cli),
            "user_pinned",
            "user-pinned model tier",
        )

    if task_type in STRONG_TASKS or security_sensitive:
        return _route(provider_cli, "strong", agent_config, cost_policy, "high-risk review task")

    if failed_attempts > 0:
        return _route(provider_cli, "strong", agent_config, cost_policy, "escalated after failed attempt")

    if cost_policy == "min_cost":
        return _route(provider_cli, "cheap", agent_config, cost_policy, "minimum-cost policy")

    if cost_policy == "max_quality":
        return _route(provider_cli, "strong", agent_config, cost_policy, "maximum-quality policy")

    configured_tier = _configured_task_tier(agent_config, task_type)
    if configured_tier:
        return _route(provider_cli, configured_tier, agent_config, cost_policy, "configured task route")

    if task_type in CHEAP_TASKS:
        return _route(provider_cli, "cheap", agent_config, cost_policy, "low-risk support task")

    return _route(provider_cli, "balanced", agent_config, cost_policy, "default balanced route")


def validate_model_config(config: dict[str, Any]) -> list[str]:
    agent_config = config.get("agent", {}) if isinstance(config.get("agent", {}), dict) else {}
    errors: list[str] = []

    models_by_task = agent_config.get("models_by_task", {})
    if models_by_task is not None and not isinstance(models_by_task, dict):
        errors.append("agent.models_by_task must be a mapping")
    elif isinstance(models_by_task, dict):
        for task_type, tier in models_by_task.items():
            if str(tier) not in VALID_MODEL_TIERS:
                errors.append(f"agent.models_by_task.{task_type} must be one of cheap, balanced, strong")

    model_profiles = agent_config.get("model_profiles")
    if model_profiles is None:
        return errors
    if not isinstance(model_profiles, dict):
        return [*errors, "agent.model_profiles must be a mapping"]

    allowed = agent_config.get("allowed_clis", ["claude", "codex"])
    if not isinstance(allowed, list):
        allowed = ["claude", "codex"]
    allowed_clis = {str(cli_name) for cli_name in allowed if str(cli_name) in {"claude", "codex"}} or {"claude", "codex"}

    for tier in sorted(VALID_MODEL_TIERS):
        profile = model_profiles.get(tier)
        if not isinstance(profile, dict):
            errors.append(f"agent.model_profiles.{tier} must be a mapping")
            continue
        for cli_name in sorted(allowed_clis):
            model_id = _profile_model_id(profile.get(cli_name))
            if not model_id:
                errors.append(f"agent.model_profiles.{tier}.{cli_name}.model is required")

    return errors


def _route(
    provider_cli: str,
    model_tier: str,
    agent_config: dict[str, Any],
    cost_policy: str,
    reason: str,
) -> ModelRoute:
    return ModelRoute(provider_cli, model_tier, _select_model_id(agent_config, model_tier, provider_cli), cost_policy, reason)


def _select_provider_cli(agent_config: dict[str, Any], *, pinned_cli: str | None = None) -> str:
    if pinned_cli and pinned_cli != "auto":
        return pinned_cli

    configured = str(agent_config.get("default_cli", "auto"))
    if configured in {"claude", "codex"}:
        return configured

    allowed = agent_config.get("allowed_clis", ["claude", "codex"])
    if not isinstance(allowed, list):
        allowed = ["claude", "codex"]

    valid_allowed = [str(cli_name) for cli_name in allowed if str(cli_name) in {"claude", "codex"}]
    if not valid_allowed:
        valid_allowed = ["claude", "codex"]

    for cli_name in valid_allowed:
        if find_executable(cli_name):
            return cli_name

    return valid_allowed[0]


def _configured_cost_policy(agent_config: dict[str, Any]) -> str:
    cost_policy = str(agent_config.get("cost_policy", "balanced"))
    return cost_policy if cost_policy in {"min_cost", "balanced", "max_quality"} else "balanced"


def _configured_task_tier(agent_config: dict[str, Any], task_type: str) -> str | None:
    models_by_task = agent_config.get("models_by_task", {})
    if not isinstance(models_by_task, dict):
        return None
    tier = str(models_by_task.get(task_type, ""))
    return tier if tier in VALID_MODEL_TIERS else None


def _select_model_id(agent_config: dict[str, Any], model_tier: str, provider_cli: str) -> str:
    model_profiles = agent_config.get("model_profiles")
    if isinstance(model_profiles, dict):
        profile = model_profiles.get(model_tier)
        if isinstance(profile, dict):
            configured = _profile_model_id(profile.get(provider_cli))
            if configured:
                return configured
    defaults = DEFAULT_MODEL_PROFILES.get(model_tier, DEFAULT_MODEL_PROFILES["balanced"])
    return defaults.get(provider_cli, defaults["codex"])


def _profile_model_id(raw_profile: Any) -> str | None:
    if isinstance(raw_profile, str):
        model_id = raw_profile.strip()
    elif isinstance(raw_profile, dict):
        model_id = str(raw_profile.get("model", "")).strip()
    else:
        return None
    return model_id or None
