from __future__ import annotations

import unittest

from CodeFlow.review_gate import configured_max_iterations, decide_review_loop


class ReviewGateTests(unittest.TestCase):
    def test_zero_blocking_findings_is_ready(self) -> None:
        decision = decide_review_loop({"status": "passed", "findings": []}, iteration=0, max_iterations=3)

        self.assertEqual(decision.to_dict()["status"], "ready")
        self.assertEqual(decision.to_dict()["next_action"], "ready_for_finalization")

    def test_blocking_findings_require_fix_iteration(self) -> None:
        decision = decide_review_loop(
            {"status": "blocked", "findings": [{"blocking": True, "summary": "bug"}]},
            iteration=0,
            max_iterations=3,
        )

        self.assertEqual(decision.to_dict()["status"], "fix_required")
        self.assertEqual(decision.to_dict()["next_action"], "run_fix_iteration")
        self.assertEqual(decision.to_dict()["iteration"], 1)
        self.assertEqual(decision.to_dict()["blocking_findings_count"], 1)

    def test_blocking_findings_after_max_iterations_escalate(self) -> None:
        decision = decide_review_loop(
            {"status": "blocked", "findings": [{"blocking": True, "summary": "bug"}]},
            iteration=3,
            max_iterations=3,
        )

        self.assertEqual(decision.to_dict()["status"], "escalate")
        self.assertEqual(decision.to_dict()["next_action"], "stop")

    def test_failed_review_stops(self) -> None:
        decision = decide_review_loop({"status": "failed", "findings": []}, iteration=0, max_iterations=3)

        self.assertEqual(decision.to_dict()["status"], "failed")
        self.assertEqual(decision.to_dict()["reason"], "review run failed")

    def test_configured_max_iterations_reads_workflow_config(self) -> None:
        self.assertEqual(configured_max_iterations({"workflow": {"max_iterations": 5}}), 5)
        self.assertEqual(configured_max_iterations({"workflow": {"max_iterations": "bad"}}), 8)


if __name__ == "__main__":
    unittest.main()
