from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from CodeFlow.cli import REQUIRED_SKILLS, main


def fake_find_executable(executable: str) -> str | None:
    return f"/usr/bin/{executable}" if executable in {"gh", "claude"} else None


class DoctorTests(unittest.TestCase):
    def test_doctor_requires_implement_init_skill(self) -> None:
        self.assertIn("implement-init", REQUIRED_SKILLS)
        self.assertEqual(REQUIRED_SKILLS["implement-init"].as_posix(), ".CodeFlow/skills/implement-init/SKILL.md")

    def test_doctor_requires_implement_code_creation_skill(self) -> None:
        self.assertIn("implement-code-creation", REQUIRED_SKILLS)
        self.assertEqual(
            REQUIRED_SKILLS["implement-code-creation"].as_posix(),
            ".CodeFlow/skills/implement-code-creation/SKILL.md",
        )
        self.assertNotIn("implementation", REQUIRED_SKILLS)

    def test_doctor_requires_implement_validation_skill(self) -> None:
        self.assertIn("implement-validation", REQUIRED_SKILLS)
        self.assertEqual(
            REQUIRED_SKILLS["implement-validation"].as_posix(),
            ".CodeFlow/skills/implement-validation/SKILL.md",
        )

    def run_main(self, argv: list[str], *, load_env: bool = False) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        env_context = nullcontext() if load_env else patch("CodeFlow.cli.load_local_env", lambda: {})
        with env_context, redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_doctor_fails_without_database_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
            exit_code, _, stderr = self.run_main(["doctor", "--json"])

        self.assertEqual(exit_code, 1)
        log_lines = [json.loads(line) for line in stderr.splitlines()]
        self.assertTrue(all(line["event"] == "doctor_blocking_failure" for line in log_lines))
        self.assertIn("database", {line["check_name"] for line in log_lines})

    def test_doctor_passes_with_database_and_one_allowed_cli(self) -> None:
        env = {
            "CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow",
            "CODEFLOW_GITHUB_OWNER": "algeor",
            "CODEFLOW_GITHUB_REPO": "codeflow",
            "CODEFLOW_BASE_BRANCH": "dev",
            "CODEFLOW_ALLOWED_REVIEWERS": "reviewer-one",
        }
        with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
            exit_code, _, stderr = self.run_main(["doctor", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")

    def test_doctor_fails_without_allowed_reviewers_when_approval_required(self) -> None:
        env = {
            "CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow",
            "CODEFLOW_GITHUB_OWNER": "algeor",
            "CODEFLOW_GITHUB_REPO": "codeflow",
            "CODEFLOW_BASE_BRANCH": "dev",
        }
        with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
            exit_code, _, stderr = self.run_main(["doctor", "--json"])

        self.assertEqual(exit_code, 1)
        log_lines = [json.loads(line) for line in stderr.splitlines()]
        self.assertIn("allowed_reviewers", {line["check_name"] for line in log_lines})

    def test_doctor_fails_when_pinned_cli_is_unavailable(self) -> None:
        config = """
agent:
  default_cli: codex
  allowed_clis: [codex]
"""
        env = {
            "CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow",
            "CODEFLOW_GITHUB_OWNER": "algeor",
            "CODEFLOW_GITHUB_REPO": "codeflow",
            "CODEFLOW_BASE_BRANCH": "dev",
            "CODEFLOW_ALLOWED_REVIEWERS": "reviewer-one",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(config)

            with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
                exit_code, _, stderr = self.run_main(["--config", str(config_path), "doctor", "--json"])

        self.assertEqual(exit_code, 1)
        log_line = json.loads(stderr.strip())
        self.assertEqual(log_line["event"], "doctor_blocking_failure")
        self.assertEqual(log_line["check_name"], "agent_cli")

    def test_doctor_fails_when_model_profiles_are_invalid(self) -> None:
        config = """
agent:
  default_cli: claude
  allowed_clis: [claude]
  model_profiles:
    cheap:
      claude:
        model: haiku
    balanced:
      claude:
        model: sonnet
    strong:
      claude:
        model: ""
"""
        env = {
            "CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow",
            "CODEFLOW_GITHUB_OWNER": "algeor",
            "CODEFLOW_GITHUB_REPO": "codeflow",
            "CODEFLOW_BASE_BRANCH": "dev",
            "CODEFLOW_ALLOWED_REVIEWERS": "reviewer-one",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(config)

            with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
                exit_code, stdout, stderr = self.run_main(["--config", str(config_path), "doctor", "--json"])

        self.assertEqual(exit_code, 1)
        result = json.loads(stdout)
        model_check = next(check for check in result["checks"] if check["name"] == "model_profiles")
        self.assertFalse(model_check["ok"])
        self.assertEqual(model_check["errors"], ["agent.model_profiles.strong.claude.model is required"])
        log_line = json.loads(stderr.strip())
        self.assertEqual(log_line["check_name"], "model_profiles")

    def test_main_loads_local_env_before_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "CODEFLOW_DATABASE_URL=postgresql://localhost/CodeFlow",
                        "CODEFLOW_GITHUB_OWNER=algeor",
                        "CODEFLOW_GITHUB_REPO=codeflow",
                        "CODEFLOW_BASE_BRANCH=dev",
                        "CODEFLOW_ALLOWED_REVIEWERS=reviewer-one",
                    ]
                )
            )
            (root / ".CodeFlow" / "skills").mkdir(parents=True)
            for skill_name in REQUIRED_SKILLS:
                skill_path = root / REQUIRED_SKILLS[skill_name]
                skill_path.parent.mkdir(parents=True, exist_ok=True)
                skill_path.write_text("# skill\n")
            (root / ".CodeFlow" / "config.yaml").write_text("agent:\n  default_cli: claude\n")

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {}, clear=True), patch(
                    "CodeFlow.cli.find_executable", fake_find_executable
                ):
                    exit_code, stdout, stderr = self.run_main(["doctor", "--json"], load_env=True)
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        github_check = next(check for check in result["checks"] if check["name"] == "github_repo")
        self.assertEqual(github_check["owner"], "algeor")
        self.assertEqual(github_check["repo"], "codeflow")

    def test_propose_creates_artifacts_and_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                exit_code, stdout, stderr = self.run_main(
                    [
                        "propose",
                        "readme-documentation-plan",
                        "Enhance",
                        "the",
                        "README",
                        "documentation.",
                        "--json",
                    ]
                )
            finally:
                os.chdir(previous_cwd)

            proposal_path = Path(tmpdir) / ".CodeFlow" / "changes" / "readme-documentation-plan" / "proposal.md"
            proposal_json_path = proposal_path.with_name("proposal.json")
            proposal_exists = proposal_path.exists()
            proposal_json_exists = proposal_json_path.exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        output = json.loads(stdout)
        self.assertEqual(output["change_name"], "readme-documentation-plan")
        self.assertEqual(output["artifacts"]["proposal"], ".CodeFlow/changes/readme-documentation-plan/proposal.md")
        self.assertTrue(proposal_exists)
        self.assertTrue(proposal_json_exists)

    def test_propose_open_pr_includes_pull_request_output(self) -> None:
        class StubPullRequest:
            def to_dict(self):
                return {"branch": "plan/readme-documentation-plan", "url": "https://github.com/algeor/codeflow/pull/2"}

        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                with patch("CodeFlow.cli.load_project_config", lambda path=None: {"github": {}}), patch(
                    "CodeFlow.cli.create_plan_pull_request", lambda proposal, config: StubPullRequest()
                ):
                    exit_code, stdout, stderr = self.run_main(
                        [
                            "propose",
                            "readme-documentation-plan",
                            "Enhance",
                            "README",
                            "documentation.",
                            "--open-pr",
                            "--json",
                        ]
                    )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        output = json.loads(stdout)
        self.assertEqual(output["pull_request"]["branch"], "plan/readme-documentation-plan")
        self.assertEqual(output["pull_request"]["url"], "https://github.com/algeor/codeflow/pull/2")

    def test_status_requires_pr_number(self) -> None:
        exit_code, _, stderr = self.run_main(["status", "readme-documentation-plan"])

        self.assertEqual(exit_code, 2)
        self.assertIn("requires --pr-number", stderr)

    def test_status_reports_approval_gate_result(self) -> None:
        pr_data = {
            "number": 2,
            "url": "https://github.com/algeor/codeflow/pull/2",
            "state": "OPEN",
            "headRefName": "plan/readme-documentation-plan",
            "baseRefName": "dev",
            "author": {"login": "algeor"},
            "reviews": [{"author": {"login": "reviewer-one"}, "state": "APPROVED"}],
        }
        config = {"github": {"allowed_reviewers": ["reviewer-one"]}}

        with patch("CodeFlow.cli.load_project_config", lambda path=None: config), patch(
            "CodeFlow.cli.fetch_pull_request", lambda pr_number, config: pr_data
        ):
            exit_code, stdout, stderr = self.run_main(
                ["status", "readme-documentation-plan", "--pr-number", "2", "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        output = json.loads(stdout)
        self.assertTrue(output["implementation_allowed"])
        self.assertEqual(output["approval"]["approved_by"], ["reviewer-one"])

    def test_run_real_implementation_requires_pr_number(self) -> None:
        config = """
agent:
  default_cli: claude
  allowed_clis: [claude]
github:
  allowed_reviewers: [reviewer-one]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(config)

            exit_code, stdout, stderr = self.run_main(
                ["--config", str(config_path), "run", "budget-guard", "--agent", "claude", "--json"]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertFalse(result["implementation_allowed"])
        self.assertEqual(result["approval"]["blocking_reason"], "--pr-number is required for implementation")

    def test_run_real_implementation_blocks_without_approval(self) -> None:
        class StubClaudePhaseAgent:
            instances: list["StubClaudePhaseAgent"] = []

            def __init__(self, *, config, work_dir, timeout_seconds):
                self.instances.append(self)

        config = {"agent": {"default_cli": "claude", "allowed_clis": ["claude"]}, "github": {"allowed_reviewers": ["reviewer-one"]}}
        pr_data = {
            "number": 2,
            "url": "https://github.com/algeor/codeflow/pull/2",
            "state": "OPEN",
            "author": {"login": "algeor"},
            "reviews": [],
        }

        with patch("CodeFlow.cli.load_project_config", lambda path=None: config), patch(
            "CodeFlow.cli.fetch_pull_request", lambda pr_number, config: pr_data
        ), patch("CodeFlow.cli.ClaudePhaseAgent", StubClaudePhaseAgent):
            exit_code, stdout, stderr = self.run_main(
                ["run", "budget-guard", "--agent", "claude", "--pr-number", "2", "--json"]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertFalse(result["implementation_allowed"])
        self.assertEqual(result["approval"]["blocking_reason"], "no approval from an allowed human reviewer")
        self.assertEqual(StubClaudePhaseAgent.instances, [])

    def test_run_claude_dry_run_invokes_workflow_and_prints_json(self) -> None:
        class AvailableAdapter:
            def is_available(self) -> bool:
                return True

        class StubClaudePhaseAgent:
            instances: list["StubClaudePhaseAgent"] = []

            def __init__(self, *, config, work_dir, timeout_seconds):
                self.config = config
                self.work_dir = work_dir
                self.timeout_seconds = timeout_seconds
                self.adapter = AvailableAdapter()
                self.contexts = {}
                self.instances.append(self)

            def run_phase(self, phase, context):
                self.contexts[phase.phase_id] = dict(context)
                responses = {
                    "init": {"status": "ready", "validation_commands": []},
                    "code_creation": {
                        "status": "completed",
                        "logical_step": "budget-guard",
                        "files_changed": ["CodeFlow/budget.py"],
                        "precommit_run": {"status": "passed"},
                    },
                    "validation": {"status": "passed", "safe_to_commit": True, "commands_run": []},
                }
                return responses[phase.phase_id]

        config = """
agent:
  default_cli: claude
  allowed_clis: [claude]
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            work_dir = Path(tmpdir) / "run"
            config_path.write_text(config)

            with patch("CodeFlow.cli.ClaudePhaseAgent", StubClaudePhaseAgent):
                exit_code, stdout, stderr = self.run_main(
                    [
                        "--config",
                        str(config_path),
                        "run",
                        "budget-guard",
                        "--agent",
                        "claude",
                        "--dry-run",
                        "--work-dir",
                        str(work_dir),
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertEqual(result["phase_order"], ["init", "code_creation", "validation"])
        self.assertTrue(result["safe_to_commit"])
        agent = StubClaudePhaseAgent.instances[0]
        self.assertEqual(agent.work_dir, work_dir)
        self.assertTrue(agent.contexts["init"]["dry_run"])
        self.assertEqual(agent.contexts["init"]["change_name"], "budget-guard")

    def test_run_real_implementation_after_approved_pr_invokes_workflow(self) -> None:
        class AvailableAdapter:
            def is_available(self) -> bool:
                return True

        class StubClaudePhaseAgent:
            instances: list["StubClaudePhaseAgent"] = []

            def __init__(self, *, config, work_dir, timeout_seconds):
                self.config = config
                self.work_dir = work_dir
                self.timeout_seconds = timeout_seconds
                self.adapter = AvailableAdapter()
                self.contexts = {}
                self.instances.append(self)

            def run_phase(self, phase, context):
                self.contexts[phase.phase_id] = dict(context)
                responses = {
                    "init": {"status": "ready", "validation_commands": []},
                    "code_creation": {
                        "status": "completed",
                        "logical_step": "budget-guard",
                        "files_changed": ["CodeFlow/budget.py"],
                        "precommit_run": {"status": "passed"},
                    },
                    "validation": {"status": "passed", "safe_to_commit": True, "commands_run": []},
                }
                return responses[phase.phase_id]

        config = {"agent": {"default_cli": "claude", "allowed_clis": ["claude"]}, "github": {"allowed_reviewers": ["reviewer-one"]}}
        pr_data = {
            "number": 2,
            "url": "https://github.com/algeor/codeflow/pull/2",
            "state": "OPEN",
            "headRefName": "plan/budget-guard",
            "baseRefName": "dev",
            "author": {"login": "algeor"},
            "reviews": [{"author": {"login": "reviewer-one"}, "state": "APPROVED"}],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "run"
            with patch("CodeFlow.cli.load_project_config", lambda path=None: config), patch(
                "CodeFlow.cli.fetch_pull_request", lambda pr_number, config: pr_data
            ), patch("CodeFlow.cli.ClaudePhaseAgent", StubClaudePhaseAgent):
                exit_code, stdout, stderr = self.run_main(
                    [
                        "run",
                        "budget-guard",
                        "--agent",
                        "claude",
                        "--pr-number",
                        "2",
                        "--work-dir",
                        str(work_dir),
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        result = json.loads(stdout)
        self.assertTrue(result["safe_to_commit"])
        self.assertEqual(result["approval"]["approved_by"], ["reviewer-one"])
        self.assertEqual(result["pull_request"]["number"], 2)
        agent = StubClaudePhaseAgent.instances[0]
        self.assertFalse(agent.contexts["init"]["dry_run"])
        self.assertTrue(agent.contexts["init"]["implementation_allowed"])
        self.assertEqual(agent.contexts["init"]["pull_request"]["number"], 2)


if __name__ == "__main__":
    unittest.main()
