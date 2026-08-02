from __future__ import annotations

import unittest
from unittest.mock import patch

from CodeFlow.model_router import route_task, validate_model_config


class ModelRouterTests(unittest.TestCase):
    def test_auto_cli_resolves_to_available_allowed_cli(self) -> None:
        config = {"agent": {"default_cli": "auto", "allowed_clis": ["claude", "codex"]}}

        with patch("CodeFlow.model_router.find_executable", lambda name: "/usr/bin/claude" if name == "claude" else None):
            route = route_task("coding", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "balanced")
        self.assertEqual(route.model_id, "sonnet")

    def test_high_risk_review_uses_strong_tier(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("security_review", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "strong")
        self.assertEqual(route.model_id, "opus")

    def test_implement_validation_uses_cheap_tier(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("implement_validation", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "cheap")
        self.assertEqual(route.model_id, "haiku")

    def test_configured_profile_selects_concrete_model_id(self) -> None:
        config = {
            "agent": {
                "default_cli": "codex",
                "models_by_task": {"implement_code_creation": "balanced"},
                "model_profiles": {
                    "cheap": {"codex": {"model": "gpt-5-mini"}, "claude": {"model": "haiku"}},
                    "balanced": {"codex": {"model": "gpt-5"}, "claude": {"model": "sonnet"}},
                    "strong": {"codex": {"model": "gpt-5-pro"}, "claude": {"model": "opus"}},
                },
            }
        }

        route = route_task("implement_code_creation", config)

        self.assertEqual(route.provider_cli, "codex")
        self.assertEqual(route.model_tier, "balanced")
        self.assertEqual(route.model_id, "gpt-5")
        self.assertEqual(route.reason, "configured task route")

    def test_risk_routing_overrides_configured_cheap_tier(self) -> None:
        config = {"agent": {"default_cli": "codex", "models_by_task": {"security_review": "cheap"}}}

        route = route_task("security_review", config)

        self.assertEqual(route.model_tier, "strong")
        self.assertEqual(route.model_id, "gpt-5")

    def test_auto_cli_does_not_use_disallowed_available_cli(self) -> None:
        config = {"agent": {"default_cli": "auto", "allowed_clis": ["codex"]}}

        with patch("CodeFlow.model_router.find_executable", lambda name: "/usr/bin/claude" if name == "claude" else None):
            route = route_task("coding", config)

        self.assertEqual(route.provider_cli, "codex")

    def test_pinned_model_tier_marks_cost_policy_as_user_pinned(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("coding", config, pinned_model_tier="strong")

        self.assertEqual(route.model_tier, "strong")
        self.assertEqual(route.model_id, "opus")
        self.assertEqual(route.cost_policy, "user_pinned")

    def test_invalid_cost_policy_defaults_to_balanced(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "invalid"}}

        route = route_task("coding", config)

        self.assertEqual(route.cost_policy, "balanced")

    def test_validate_model_config_rejects_missing_allowed_cli_profile(self) -> None:
        config = {
            "agent": {
                "allowed_clis": ["claude", "codex"],
                "model_profiles": {
                    "cheap": {"codex": {"model": "gpt-5-mini"}},
                    "balanced": {"codex": {"model": "gpt-5"}, "claude": {"model": "sonnet"}},
                    "strong": {"codex": {"model": "gpt-5"}, "claude": {"model": "opus"}},
                },
            }
        }

        errors = validate_model_config(config)

        self.assertEqual(errors, ["agent.model_profiles.cheap.claude.model is required"])

    def test_validate_model_config_rejects_invalid_task_tier(self) -> None:
        config = {"agent": {"models_by_task": {"coding": "expensive"}}}

        errors = validate_model_config(config)

        self.assertEqual(errors, ["agent.models_by_task.coding must be one of cheap, balanced, strong"])


if __name__ == "__main__":
    unittest.main()
