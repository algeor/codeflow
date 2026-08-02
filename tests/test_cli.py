from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_doctor_fails_without_database_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
            exit_code, _, stderr = self.run_main(["doctor", "--json"])

        self.assertEqual(exit_code, 1)
        log_line = json.loads(stderr.strip())
        self.assertEqual(log_line["event"], "doctor_blocking_failure")
        self.assertEqual(log_line["check_name"], "database")

    def test_doctor_passes_with_database_and_one_allowed_cli(self) -> None:
        env = {"CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow"}
        with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
            exit_code, _, stderr = self.run_main(["doctor", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")

    def test_doctor_fails_when_pinned_cli_is_unavailable(self) -> None:
        config = """
agent:
  default_cli: codex
  allowed_clis: [codex]
"""
        env = {"CODEFLOW_DATABASE_URL": "postgresql://localhost/CodeFlow"}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(config)

            with patch.dict(os.environ, env, clear=True), patch("CodeFlow.cli.find_executable", fake_find_executable):
                exit_code, _, stderr = self.run_main(["--config", str(config_path), "doctor", "--json"])

        self.assertEqual(exit_code, 1)
        log_line = json.loads(stderr.strip())
        self.assertEqual(log_line["event"], "doctor_blocking_failure")
        self.assertEqual(log_line["check_name"], "agent_cli")


if __name__ == "__main__":
    unittest.main()
