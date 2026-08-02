from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from CodeFlow.adapters.base import AgentRequest, AgentResult, CliAgentAdapter
from CodeFlow.orchestration import IMPLEMENTATION_PHASES
from CodeFlow.workflow_runner import (
    CliPhaseAgent,
    FakePhaseAgent,
    build_phase_prompt,
    parse_phase_output,
    run_implementation_workflow,
)


READY_INIT = {
    "status": "ready",
    "repo_instructions_read": ["AGENTS.md"],
    "relevant_files": ["CodeFlow/budget.py"],
    "relevant_tests": ["tests/test_budget.py"],
    "validation_commands": ["python -m unittest tests.test_budget"],
    "implementation_plan": ["add budget guard"],
    "risks": [],
}

COMPLETED_CODE = {
    "status": "completed",
    "change_name": "budget-guard",
    "logical_step": "add-budget-guard",
    "summary": "Added budget guard.",
    "files_changed": ["CodeFlow/budget.py"],
    "tests_changed": ["tests/test_budget.py"],
    "tests_to_run": ["python -m unittest tests.test_budget"],
    "precommit_run": {"command": "pre-commit run --files CodeFlow/budget.py", "status": "passed"},
    "review_roles_suggested": ["backend", "test_quality"],
    "blocking_questions": [],
}

PASSED_VALIDATION = {
    "status": "passed",
    "logical_step": "add-budget-guard",
    "safe_to_commit": True,
    "commands_run": [{"command": "python -m unittest tests.test_budget", "status": "passed"}],
    "test_integrity": {"checked": True, "status": "passed"},
    "blocking_failures": [],
    "warnings": [],
}


class ScriptedClaudeAdapter(CliAgentAdapter):
    provider_cli = "claude"
    executable = "claude"

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.requests: list[AgentRequest] = []

    def build_command(self, request: AgentRequest, prompt: str) -> list[str]:
        return ["claude"]

    def invoke(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        phase_id = str(request.metadata["phase_id"])
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_text(self.responses[phase_id])
        return AgentResult(
            provider_cli=self.provider_cli,
            model=request.model,
            status="succeeded",
            return_code=0,
            output_path=request.output_path,
        )


class WorkflowRunnerTests(unittest.TestCase):
    def test_happy_path_runs_all_phases_and_allows_commit(self) -> None:
        agent = FakePhaseAgent(
            {"init": READY_INIT, "code_creation": COMPLETED_CODE, "validation": PASSED_VALIDATION}
        )

        result = run_implementation_workflow(agent, initial_context={"change_name": "budget-guard"})

        self.assertEqual(agent.calls, ["init", "code_creation", "validation"])
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.safe_to_commit)

    def test_init_escalation_stops_before_code_creation(self) -> None:
        agent = FakePhaseAgent({"init": {"status": "escalate", "reason": "missing GIT_EMAIL"}})

        result = run_implementation_workflow(agent)

        self.assertEqual(agent.calls, ["init"])
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.stopped_at, "init")
        self.assertFalse(result.safe_to_commit)

    def test_code_creation_blocked_stops_before_validation(self) -> None:
        blocked_code = {
            **COMPLETED_CODE,
            "status": "blocked",
            "reason": "ambiguous requirement",
            "blocking_questions": ["Which behavior should win?"],
        }
        agent = FakePhaseAgent({"init": READY_INIT, "code_creation": blocked_code})

        result = run_implementation_workflow(agent)

        self.assertEqual(agent.calls, ["init", "code_creation"])
        self.assertEqual(result.stopped_at, "code_creation")
        self.assertFalse(result.safe_to_commit)

    def test_precommit_failure_stops_before_validation(self) -> None:
        precommit_failed_code = {
            **COMPLETED_CODE,
            "status": "blocked",
            "reason": "pre-commit failed",
            "precommit_run": {"command": "pre-commit run --files CodeFlow/budget.py", "status": "failed"},
        }
        agent = FakePhaseAgent({"init": READY_INIT, "code_creation": precommit_failed_code})

        result = run_implementation_workflow(agent)

        self.assertEqual(agent.calls, ["init", "code_creation"])
        self.assertEqual(result.reason, "pre-commit failed")
        self.assertFalse(result.safe_to_commit)

    def test_validation_failure_does_not_allow_commit(self) -> None:
        failed_validation = {
            **PASSED_VALIDATION,
            "status": "failed",
            "safe_to_commit": False,
            "blocking_failures": ["test failed"],
        }
        agent = FakePhaseAgent({"init": READY_INIT, "code_creation": COMPLETED_CODE, "validation": failed_validation})

        result = run_implementation_workflow(agent)

        self.assertEqual(agent.calls, ["init", "code_creation", "validation"])
        self.assertEqual(result.stopped_at, "validation")
        self.assertFalse(result.safe_to_commit)

    def test_phase_summaries_are_passed_to_next_phase(self) -> None:
        agent = FakePhaseAgent(
            {"init": READY_INIT, "code_creation": COMPLETED_CODE, "validation": PASSED_VALIDATION}
        )

        run_implementation_workflow(agent)

        code_context = agent.contexts["code_creation"]
        validation_context = agent.contexts["validation"]
        self.assertEqual(json.loads(code_context["init_summary"])["status"], "ready")
        self.assertEqual(json.loads(validation_context["code_creation_summary"])["status"], "completed")

    def test_cli_phase_agent_invokes_claude_with_routed_models(self) -> None:
        adapter = ScriptedClaudeAdapter(
            {
                "init": json.dumps(READY_INIT),
                "code_creation": json.dumps(COMPLETED_CODE),
                "validation": json.dumps(PASSED_VALIDATION),
            }
        )
        config = {
            "agent": {
                "default_cli": "claude",
                "models_by_task": {
                    "implement_init": "balanced",
                    "implement_code_creation": "balanced",
                    "implement_validation": "cheap",
                },
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = CliPhaseAgent(adapter, config=config, work_dir=Path(tmpdir))
            result = run_implementation_workflow(agent, initial_context={"change_name": "budget-guard"})

        self.assertTrue(result.safe_to_commit)
        self.assertEqual([request.model for request in adapter.requests], ["sonnet", "sonnet", "haiku"])
        self.assertEqual([request.task_type for request in adapter.requests], [phase.task_type for phase in IMPLEMENTATION_PHASES])

    def test_cli_phase_agent_prompt_points_to_skill_and_context(self) -> None:
        adapter = ScriptedClaudeAdapter({"init": json.dumps(READY_INIT)})

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = CliPhaseAgent(adapter, config={"agent": {"default_cli": "claude"}}, work_dir=Path(tmpdir))
            result = agent.run_phase(IMPLEMENTATION_PHASES[0], {"change_name": "budget-guard"})

            prompt = adapter.requests[0].prompt_path.read_text()

        self.assertEqual(result["status"], "ready")
        self.assertIn("implement-init/SKILL.md", prompt)
        self.assertIn('"change_name": "budget-guard"', prompt)

    def test_parse_phase_output_accepts_claude_result_wrapper(self) -> None:
        raw = json.dumps({"result": '{"status":"ready","relevant_files":[]}'})

        result = parse_phase_output(raw)

        self.assertEqual(result["status"], "ready")

    def test_dry_run_prompt_forbids_mutation(self) -> None:
        prompt = build_phase_prompt(IMPLEMENTATION_PHASES[0], {"dry_run": True})

        self.assertIn("Dry-run mode is active", prompt)
        self.assertIn("Do not edit files", prompt)
        self.assertIn("Do not run shell commands", prompt)
        self.assertIn("Do not commit or push", prompt)


if __name__ == "__main__":
    unittest.main()
