from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from CodeFlow.proposal import create_proposal_artifacts
from CodeFlow.pull_request import (
    CommandResult,
    PullRequestError,
    create_plan_pull_request,
    fetch_pull_request,
    fetch_pull_request_changed_files,
)


class PullRequestTests(unittest.TestCase):
    def test_create_plan_pull_request_runs_git_and_gh_sequence(self) -> None:
        calls: list[tuple[list[str], dict[str, str], Path]] = []

        def runner(args, env, cwd):
            assert env is not None
            calls.append((list(args), env, cwd))
            if list(args[:3]) == ["gh", "pr", "create"]:
                return CommandResult(tuple(args), 0, "https://github.com/algeor/codeflow/pull/2\n", "")
            return CommandResult(tuple(args), 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proposal = create_proposal_artifacts("readme-documentation-plan", "Improve README", root=root)
            config = {
                "github": {
                    "owner": "algeor",
                    "repo": "codeflow",
                    "base_branch": "dev",
                    "plan_label": "plan-only",
                }
            }

            with patch.dict(os.environ, {"GITHUB_TOKEN": "token-value"}, clear=True):
                result = create_plan_pull_request(proposal, config=config, cwd=root, runner=runner)

        self.assertEqual(result.branch, "plan/readme-documentation-plan")
        self.assertEqual(result.url, "https://github.com/algeor/codeflow/pull/2")
        self.assertEqual(calls[0][0], ["git", "switch", "-c", "plan/readme-documentation-plan"])
        self.assertEqual(calls[1][0][0:2], ["git", "add"])
        self.assertIn(".CodeFlow/changes/readme-documentation-plan/proposal.json", calls[1][0])
        self.assertEqual(calls[2][0], ["git", "commit", "-m", "Plan Readme Documentation Plan"])
        self.assertEqual(calls[3][0], ["git", "push", "-u", "origin", "plan/readme-documentation-plan"])
        self.assertEqual(calls[4][0][0:5], ["gh", "pr", "create", "--repo", "algeor/codeflow"])
        self.assertIn("--label", calls[4][0])
        self.assertEqual(calls[4][1]["GH_HOST"], "github.com")
        self.assertEqual(calls[4][1]["GH_TOKEN"], "token-value")

    def test_create_plan_pull_request_requires_github_repo_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proposal = create_proposal_artifacts("readme-plan", "Improve README", root=root)

            with self.assertRaisesRegex(PullRequestError, "owner, repo, and base branch"):
                create_plan_pull_request(proposal, config={}, cwd=root, runner=lambda args, env, cwd: CommandResult(tuple(args), 0))

    def test_create_plan_pull_request_reports_command_failure(self) -> None:
        def runner(args, env, cwd):
            return CommandResult(tuple(args), 1, "", "branch already exists")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proposal = create_proposal_artifacts("readme-plan", "Improve README", root=root)
            config = {"github": {"owner": "algeor", "repo": "codeflow", "base_branch": "dev"}}

            with self.assertRaisesRegex(PullRequestError, "branch already exists"):
                create_plan_pull_request(proposal, config=config, cwd=root, runner=runner)

    def test_fetch_pull_request_runs_gh_pr_view(self) -> None:
        calls: list[list[str]] = []

        def runner(args, env, cwd):
            calls.append(list(args))
            return CommandResult(
                tuple(args),
                0,
                '{"number":2,"url":"https://github.com/algeor/codeflow/pull/2","state":"OPEN","reviews":[]}',
                "",
            )

        config = {"github": {"owner": "algeor", "repo": "codeflow", "base_branch": "dev"}}

        result = fetch_pull_request(2, config=config, runner=runner)

        self.assertEqual(result["number"], 2)
        self.assertEqual(
            calls[0],
            [
                "gh",
                "pr",
                "view",
                "2",
                "--repo",
                "algeor/codeflow",
                "--json",
                "number,url,state,reviewDecision,reviews,author,headRefName,baseRefName",
            ],
        )

    def test_fetch_pull_request_changed_files_runs_gh_pr_diff(self) -> None:
        calls: list[list[str]] = []

        def runner(args, env, cwd):
            calls.append(list(args))
            return CommandResult(tuple(args), 0, "README.md\nCodeFlow/cli.py\n", "")

        config = {"github": {"owner": "algeor", "repo": "codeflow", "base_branch": "dev"}}

        files = fetch_pull_request_changed_files(2, config=config, runner=runner)

        self.assertEqual(files, ["README.md", "CodeFlow/cli.py"])
        self.assertEqual(calls[0], ["gh", "pr", "diff", "2", "--repo", "algeor/codeflow", "--name-only"])


if __name__ == "__main__":
    unittest.main()
