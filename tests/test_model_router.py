from __future__ import annotations

import unittest
from unittest.mock import patch

from CodeFlow.model_router import route_task


class ModelRouterTests(unittest.TestCase):
    def test_auto_cli_resolves_to_available_allowed_cli(self) -> None:
        config = {"agent": {"default_cli": "auto", "allowed_clis": ["claude", "codex"]}}

        with patch("CodeFlow.model_router.find_executable", lambda name: "/usr/bin/claude" if name == "claude" else None):
            route = route_task("coding", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "balanced")

    def test_high_risk_review_uses_strong_tier(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("security_review", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "strong")

    def test_implement_validation_uses_cheap_tier(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("implement_validation", config)

        self.assertEqual(route.provider_cli, "claude")
        self.assertEqual(route.model_tier, "cheap")

    def test_auto_cli_does_not_use_disallowed_available_cli(self) -> None:
        config = {"agent": {"default_cli": "auto", "allowed_clis": ["codex"]}}

        with patch("CodeFlow.model_router.find_executable", lambda name: "/usr/bin/claude" if name == "claude" else None):
            route = route_task("coding", config)

        self.assertEqual(route.provider_cli, "codex")

    def test_pinned_model_tier_marks_cost_policy_as_user_pinned(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "balanced"}}

        route = route_task("coding", config, pinned_model_tier="strong")

        self.assertEqual(route.model_tier, "strong")
        self.assertEqual(route.cost_policy, "user_pinned")

    def test_invalid_cost_policy_defaults_to_balanced(self) -> None:
        config = {"agent": {"default_cli": "claude", "cost_policy": "invalid"}}

        route = route_task("coding", config)

        self.assertEqual(route.cost_policy, "balanced")


if __name__ == "__main__":
    unittest.main()
