from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from CodeFlow.git_workflow import CommandResult, GitWorkflowError, commit_and_push_validated_step


class GitWorkflowTests(unittest.TestCase):
    def test_commit_and_push_validated_step_runs_expected_git_commands(self) -> None:
        calls: list[tuple[list[str], dict[str, str], Path]] = []

        def runner(args, env, cwd):
            assert env is not None
            calls.append((list(args), env, cwd))
            if list(args) == ["git", "branch", "--show-current"]:
                return CommandResult(tuple(args), 0, "feature/readme\n", "")
            if list(args) == ["git", "rev-parse", "HEAD"]:
                return CommandResult(tuple(args), 0, "abc123\n", "")
            return CommandResult(tuple(args), 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"GH_TOKEN": "token-value"}, clear=True
        ):
            result = commit_and_push_validated_step(
                {
                    "logical_step": "update-readme",
                    "files_changed": ["README.md", "README.md"],
                    "tests_changed": ["tests/test_readme.py"],
                },
                {"safe_to_commit": True},
                {"head_ref": "feature/readme"},
                cwd=Path(tmpdir),
                runner=runner,
            )

        self.assertEqual(result.branch, "feature/readme")
        self.assertEqual(result.commit, "abc123")
        self.assertEqual(result.files_committed, ["README.md", "tests/test_readme.py"])
        self.assertEqual(calls[0][0], ["git", "branch", "--show-current"])
        self.assertEqual(calls[1][0], ["git", "add", "--", "README.md", "tests/test_readme.py"])
        self.assertEqual(calls[2][0], ["git", "commit", "-m", "Implement update-readme"])
        self.assertEqual(calls[3][0], ["git", "rev-parse", "HEAD"])
        self.assertEqual(calls[4][0], ["git", "push", "origin", "feature/readme"])
        self.assertEqual(calls[4][1]["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertNotIn("token-value", " ".join(calls[4][0]))

    def test_commit_and_push_validated_step_rejects_wrong_branch(self) -> None:
        def runner(args, env, cwd):
            return CommandResult(tuple(args), 0, "dev\n", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(GitWorkflowError, "does not match PR head branch"):
                commit_and_push_validated_step(
                    {"logical_step": "step", "files_changed": ["README.md"]},
                    {"safe_to_commit": True},
                    {"head_ref": "feature/readme"},
                    cwd=Path(tmpdir),
                    runner=runner,
                )

    def test_commit_and_push_validated_step_rejects_forbidden_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(GitWorkflowError, "refusing to commit"):
                commit_and_push_validated_step(
                    {"logical_step": "step", "files_changed": [".CodeFlow/runs/demo/output.json"]},
                    {"safe_to_commit": True},
                    {"head_ref": "feature/readme"},
                    cwd=Path(tmpdir),
                    runner=lambda args, env, cwd: CommandResult(tuple(args), 0, "", ""),
                )

    def test_commit_and_push_validated_step_rejects_when_validation_is_not_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(GitWorkflowError, "safe to commit"):
                commit_and_push_validated_step(
                    {"logical_step": "step", "files_changed": ["README.md"]},
                    {"safe_to_commit": False},
                    {"head_ref": "feature/readme"},
                    cwd=Path(tmpdir),
                    runner=lambda args, env, cwd: CommandResult(tuple(args), 0, "", ""),
                )

    def test_commit_and_push_validated_step_requires_github_token_for_push(self) -> None:
        def runner(args, env, cwd):
            if list(args) == ["git", "branch", "--show-current"]:
                return CommandResult(tuple(args), 0, "feature/readme\n", "")
            if list(args) == ["git", "rev-parse", "HEAD"]:
                return CommandResult(tuple(args), 0, "abc123\n", "")
            return CommandResult(tuple(args), 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(GitWorkflowError, "GH_TOKEN or GITHUB_TOKEN"):
                commit_and_push_validated_step(
                    {"logical_step": "step", "files_changed": ["README.md"]},
                    {"safe_to_commit": True},
                    {"head_ref": "feature/readme"},
                    cwd=Path(tmpdir),
                    runner=runner,
                )


if __name__ == "__main__":
    unittest.main()
