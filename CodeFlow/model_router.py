from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_discovery import find_executable


@dataclass(frozen=True)
class ModelRoute:
    provider_cli: str
    model_tier: str
    cost_policy: str
    reason: str


STRONG_TASKS = {"security_review", "test_quality_review", "final_blocking_review"}
CHEAP_TASKS = {"implement_validation", "summarization", "rag_indexing", "status"}


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
        return ModelRoute(provider_cli, pinned_model_tier, "user_pinned", "user-pinned model tier")

    if task_type in CHEAP_TASKS and cost_policy != "max_quality":
        return ModelRoute(provider_cli, "cheap", cost_policy, "low-risk support task")

    if task_type in STRONG_TASKS or security_sensitive:
        return ModelRoute(provider_cli, "strong", cost_policy, "high-risk review task")

    if failed_attempts > 0:
        return ModelRoute(provider_cli, "strong", cost_policy, "escalated after failed attempt")

    if cost_policy == "min_cost":
        return ModelRoute(provider_cli, "cheap", cost_policy, "minimum-cost policy")

    if cost_policy == "max_quality":
        return ModelRoute(provider_cli, "strong", cost_policy, "maximum-quality policy")

    return ModelRoute(provider_cli, "balanced", cost_policy, "default balanced route")


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
