from __future__ import annotations

import unittest

from CodeFlow.review_routing import detect_review_plan


class ReviewRoutingTests(unittest.TestCase):
    def test_detect_review_plan_routes_by_configured_patterns(self) -> None:
        config = {
            "review": {
                "require_test_quality_review": True,
                "roles": {
                    "backend": {"patterns": ["**/*.py"]},
                    "docs": {"patterns": ["**/*.md", "docs/**"]},
                    "security": {"patterns": ["**/*token*"]},
                    "test_quality": {"patterns": ["tests/**", "**/*test*"]},
                },
            }
        }

        plan = detect_review_plan(
            ["CodeFlow/token_store.py", "README.md", "tests/test_token_store.py"],
            config,
        )

        result = plan.to_dict()
        self.assertEqual(result["required_roles"], ["backend", "docs", "security", "test_quality"])
        self.assertEqual(
            result["review_tasks"],
            ["code_review", "security_review", "test_quality_review", "final_blocking_review"],
        )

    def test_detect_review_plan_can_disable_test_quality_role(self) -> None:
        plan = detect_review_plan(
            ["tests/test_example.py"],
            {"review": {"require_test_quality_review": False, "roles": {"test_quality": {"patterns": ["tests/**"]}}}},
        )

        self.assertEqual(plan.to_dict()["required_roles"], [])
        self.assertEqual(plan.to_dict()["review_tasks"], ["code_review", "final_blocking_review"])

    def test_detect_review_plan_deduplicates_changed_files(self) -> None:
        plan = detect_review_plan(["README.md", "README.md", ""], {"review": {"roles": {"docs": {"patterns": ["**/*.md"]}}}})

        self.assertEqual(plan.changed_files, ["README.md"])
        self.assertEqual(plan.to_dict()["required_roles"], ["docs"])


if __name__ == "__main__":
    unittest.main()
