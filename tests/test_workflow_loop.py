from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from CodeFlow.review_gate import decide_review_loop
from CodeFlow.workflow_loop import GateRequest, ImplementationRequest, ReviewRequest, run_workflow_loop


def completed_implementation(logical_step: str = "step") -> dict[str, object]:
    return {
        "status": "completed",
        "safe_to_commit": True,
        "phase_order": ["init", "code_creation", "validation"],
        "phase_results": {},
        "logical_step": logical_step,
        "token_usage": {"total": {"input_tokens": 10, "output_tokens": 4}},
    }


def gate_runner(max_iterations: int):
    def run(request: GateRequest) -> dict[str, object]:
        return decide_review_loop(
            request.review_result,
            iteration=request.fix_iteration,
            max_iterations=max_iterations,
        ).to_dict()

    return run


class WorkflowLoopTests(unittest.TestCase):
    def test_loop_stops_ready_after_review_passes(self) -> None:
        implementation_calls: list[ImplementationRequest] = []
        review_calls: list[ReviewRequest] = []

        def implementation_runner(request: ImplementationRequest) -> dict[str, object]:
            implementation_calls.append(request)
            return completed_implementation()

        def review_runner(request: ReviewRequest) -> dict[str, object]:
            review_calls.append(request)
            return {"status": "passed", "findings": [], "blocking_findings_count": 0}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_workflow_loop(
                change_name="docs",
                pr_number=12,
                max_iterations=3,
                review_results_dir=Path(tmpdir) / "reviews",
                implementation_runner=implementation_runner,
                review_runner=review_runner,
                gate_runner=gate_runner(3),
            )
            review_file = Path(result.iterations[0].review_result_file or "")
            persisted_review = json.loads(review_file.read_text())

        self.assertEqual(result.status, "ready")
        self.assertEqual(len(result.iterations), 1)
        self.assertEqual([call.fix_iteration for call in implementation_calls], [0])
        self.assertEqual([call.fix_iteration for call in review_calls], [0])
        self.assertEqual(persisted_review["status"], "passed")
        self.assertEqual(result.to_dict()["token_usage"]["total"], {"input_tokens": 10, "output_tokens": 4})

    def test_loop_runs_fix_iteration_when_review_blocks(self) -> None:
        implementation_calls: list[ImplementationRequest] = []
        review_results = [
            {"status": "blocked", "findings": [{"blocking": True, "summary": "bug"}], "blocking_findings_count": 1},
            {"status": "passed", "findings": [], "blocking_findings_count": 0},
        ]

        def implementation_runner(request: ImplementationRequest) -> dict[str, object]:
            implementation_calls.append(request)
            return completed_implementation(f"step-{request.fix_iteration}")

        def review_runner(request: ReviewRequest) -> dict[str, object]:
            return review_results.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_workflow_loop(
                change_name="bugfix",
                pr_number=12,
                max_iterations=3,
                review_results_dir=Path(tmpdir) / "reviews",
                implementation_runner=implementation_runner,
                review_runner=review_runner,
                gate_runner=gate_runner(3),
            )

        self.assertEqual(result.status, "ready")
        self.assertEqual([call.fix_iteration for call in implementation_calls], [0, 1])
        self.assertIsNone(implementation_calls[0].review_result_file)
        self.assertTrue(str(implementation_calls[1].review_result_file).endswith("review-iteration-0.json"))

    def test_loop_escalates_after_max_fix_iterations(self) -> None:
        implementation_calls: list[ImplementationRequest] = []

        def implementation_runner(request: ImplementationRequest) -> dict[str, object]:
            implementation_calls.append(request)
            return completed_implementation(f"step-{request.fix_iteration}")

        def review_runner(request: ReviewRequest) -> dict[str, object]:
            return {"status": "blocked", "findings": [{"blocking": True, "summary": "bug"}], "blocking_findings_count": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_workflow_loop(
                change_name="stubborn-bug",
                pr_number=12,
                max_iterations=1,
                review_results_dir=Path(tmpdir) / "reviews",
                implementation_runner=implementation_runner,
                review_runner=review_runner,
                gate_runner=gate_runner(1),
            )

        self.assertEqual(result.status, "escalate")
        self.assertEqual([call.fix_iteration for call in implementation_calls], [0, 1])
        self.assertEqual(result.final_decision["reason"], "maximum fix iterations reached with blocking findings still open")

    def test_loop_stops_when_implementation_is_not_safe_to_commit(self) -> None:
        review_called = False

        def implementation_runner(request: ImplementationRequest) -> dict[str, object]:
            return {"status": "blocked", "safe_to_commit": False, "reason": "validation failed"}

        def review_runner(request: ReviewRequest) -> dict[str, object]:
            nonlocal review_called
            review_called = True
            return {"status": "passed", "findings": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_workflow_loop(
                change_name="blocked",
                pr_number=12,
                max_iterations=3,
                review_results_dir=Path(tmpdir) / "reviews",
                implementation_runner=implementation_runner,
                review_runner=review_runner,
                gate_runner=gate_runner(3),
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "validation failed")
        self.assertFalse(review_called)


if __name__ == "__main__":
    unittest.main()
