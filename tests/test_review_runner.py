from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from CodeFlow.review_routing import detect_review_plan
from CodeFlow.review_runner import ReviewAgentTaskResult, load_review_finding_file, normalize_finding, parse_review_agent_output, run_review_plan


class ReviewRunnerTests(unittest.TestCase):
    def test_run_review_plan_routes_tasks_and_blocks_on_configured_severity(self) -> None:
        config = {
            "agent": {
                "default_cli": "codex",
                "model_profiles": {
                    "balanced": {"codex": {"model": "gpt-5"}},
                    "strong": {"codex": {"model": "gpt-5-high"}},
                },
            },
            "review": {
                "blocking_severities": ["critical", "high", "medium"],
                "roles": {
                    "backend": {"patterns": ["**/*.py"]},
                    "security": {"patterns": ["**/*token*"]},
                    "test_quality": {"patterns": ["tests/**"]},
                },
            },
        }
        plan = detect_review_plan(["CodeFlow/token_store.py", "tests/test_token_store.py"], config)

        result = run_review_plan(
            change_name="token-store",
            pull_request_number=4,
            review_plan=plan,
            config=config,
            raw_findings=[
                {
                    "severity": "high",
                    "file_path": "CodeFlow/token_store.py",
                    "line": 42,
                    "summary": "Token is logged before redaction.",
                    "recommendation": "Redact the value before logging.",
                    "review_task": "security_review",
                }
            ],
        )

        data = result.to_dict()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["blocking_findings_count"], 1)
        self.assertEqual(data["findings"][0]["review_task"], "security_review")
        security_run = next(task_run for task_run in data["task_runs"] if task_run["review_task"] == "security_review")
        self.assertEqual(security_run["provider_cli"], "codex")
        self.assertEqual(security_run["model_tier"], "strong")
        self.assertEqual(security_run["model_id"], "gpt-5-high")
        self.assertEqual(security_run["status"], "findings_opened")

    def test_run_review_plan_passes_with_no_fake_findings(self) -> None:
        config = {"agent": {"default_cli": "claude"}, "review": {"roles": {"docs": {"patterns": ["**/*.md"]}}}}
        plan = detect_review_plan(["README.md"], config)

        result = run_review_plan(change_name="docs", pull_request_number=None, review_plan=plan, config=config)

        self.assertEqual(result.to_dict()["status"], "passed")
        self.assertFalse(result.to_dict()["real_agent_review"])
        self.assertEqual(result.to_dict()["review_backend"], "local-fake")

    def test_run_review_plan_invokes_agent_runner_and_normalizes_findings(self) -> None:
        class StubAgentRunner:
            calls: list[tuple[str, str]] = []

            def run_review_task(self, *, review_task, review_plan, diff_text, route):
                self.calls.append((review_task, route.provider_cli))
                if review_task == "code_review":
                    return ReviewAgentTaskResult(
                        status="succeeded",
                        raw_findings=[
                            {
                                "severity": "medium",
                                "file_path": "README.md",
                                "summary": "Behavior is undocumented.",
                                "recommendation": "Document the new behavior.",
                                "review_task": review_task,
                            }
                        ],
                        prompt_path="prompts/code_review.md",
                        output_path="outputs/code_review.json",
                        duration_ms=10,
                    )
                return ReviewAgentTaskResult(status="succeeded", raw_findings=[])

        config = {"agent": {"default_cli": "codex"}, "review": {"roles": {"docs": {"patterns": ["**/*.md"]}}}}
        plan = detect_review_plan(["README.md"], config)
        agent_runner = StubAgentRunner()

        result = run_review_plan(
            change_name="docs",
            pull_request_number=3,
            review_plan=plan,
            config=config,
            diff_text="diff --git a/README.md b/README.md\n",
            diff_source="github_pr",
            agent_runner=agent_runner,
        )

        data = result.to_dict()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["blocking_findings_count"], 1)
        self.assertIn(("code_review", "codex"), agent_runner.calls)
        code_review = next(task for task in data["task_runs"] if task["review_task"] == "code_review")
        self.assertEqual(code_review["status"], "findings_opened")
        self.assertEqual(code_review["prompt_path"], "prompts/code_review.md")
        self.assertEqual(code_review["duration_ms"], 10)

    def test_run_review_plan_reports_failed_agent_task(self) -> None:
        class FailingAgentRunner:
            def run_review_task(self, *, review_task, review_plan, diff_text, route):
                return ReviewAgentTaskResult(status="failed", raw_findings=[], reason="agent unavailable")

        config = {"agent": {"default_cli": "claude"}, "review": {"roles": {"docs": {"patterns": ["**/*.md"]}}}}
        plan = detect_review_plan(["README.md"], config)

        result = run_review_plan(
            change_name="docs",
            pull_request_number=3,
            review_plan=plan,
            config=config,
            agent_runner=FailingAgentRunner(),
        )

        data = result.to_dict()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["task_runs"][0]["status"], "failed")
        self.assertEqual(data["task_runs"][0]["reason"], "agent unavailable")

    def test_normalize_finding_uses_default_task_and_schema_fields(self) -> None:
        finding = normalize_finding(
            {"severity": "low", "path": "README.md", "line_start": "3", "title": "Wording nit"},
            default_review_task="final_blocking_review",
            blocking_severities={"critical", "high", "medium"},
        )

        self.assertEqual(
            finding.to_dict(),
            {
                "blocking": False,
                "severity": "low",
                "file_path": "README.md",
                "line": 3,
                "summary": "Wording nit",
                "recommendation": "",
                "review_task": "final_blocking_review",
            },
        )

    def test_load_review_finding_file_accepts_object_with_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "findings.json"
            path.write_text(json.dumps({"findings": [{"severity": "medium", "summary": "Missing assertion"}]}))

            findings = load_review_finding_file(path)

        self.assertEqual(findings, [{"severity": "medium", "summary": "Missing assertion"}])

    def test_parse_review_agent_output_accepts_result_wrapper(self) -> None:
        findings = parse_review_agent_output(
            json.dumps({"result": json.dumps({"findings": [{"severity": "low", "summary": "nit"}]})})
        )

        self.assertEqual(findings, [{"severity": "low", "summary": "nit"}])


if __name__ == "__main__":
    unittest.main()
