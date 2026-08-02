from __future__ import annotations

import json
import unittest

from CodeFlow.orchestration import IMPLEMENTATION_PHASES, concise_phase_summary, next_implementation_phase


class OrchestrationTests(unittest.TestCase):
    def test_phase_order_matches_first_code_validation_run(self) -> None:
        self.assertEqual(
            [phase.skill_name for phase in IMPLEMENTATION_PHASES],
            ["implement-init", "implement-code-creation", "implement-validation"],
        )

    def test_next_phase_waits_for_previous_success(self) -> None:
        self.assertEqual(next_implementation_phase({}).phase_id, "init")
        self.assertIsNone(next_implementation_phase({"init": "escalate"}))

        self.assertEqual(next_implementation_phase({"init": "ready"}).phase_id, "code_creation")
        self.assertIsNone(next_implementation_phase({"init": "ready", "code_creation": "blocked"}))

        self.assertEqual(
            next_implementation_phase({"init": "ready", "code_creation": "completed"}).phase_id,
            "validation",
        )
        self.assertIsNone(
            next_implementation_phase({"init": "ready", "code_creation": "completed", "validation": "failed"})
        )
        self.assertIsNone(
            next_implementation_phase({"init": "ready", "code_creation": "completed", "validation": "passed"})
        )

    def test_concise_summary_keeps_only_phase_handoff_fields(self) -> None:
        summary = concise_phase_summary(
            "code_creation",
            {
                "status": "completed",
                "logical_step": "add-budget-check",
                "summary": "Added budget guard.",
                "files_changed": ["CodeFlow/budget.py"],
                "large_internal_trace": "ignored",
            },
        )

        parsed = json.loads(summary)
        self.assertEqual(parsed["logical_step"], "add-budget-check")
        self.assertEqual(parsed["files_changed"], ["CodeFlow/budget.py"])
        self.assertNotIn("large_internal_trace", parsed)


if __name__ == "__main__":
    unittest.main()
