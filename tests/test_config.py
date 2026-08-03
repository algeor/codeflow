from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from CodeFlow.config import (
    allowed_github_reviewers,
    env_settings,
    github_cli_environment,
    github_settings,
    load_local_env,
    parse_env_file,
)


class ConfigTests(unittest.TestCase):
    def test_parse_env_file_handles_comments_export_and_quotes(self) -> None:
        values = parse_env_file(
            """
            # comment
            export CODEFLOW_GITHUB_OWNER=algeor
            CODEFLOW_GITHUB_REPO=codeflow # inline comment
            CODEFLOW_BASE_BRANCH="dev"
            EMPTY_VALUE=
            """
        )

        self.assertEqual(values["CODEFLOW_GITHUB_OWNER"], "algeor")
        self.assertEqual(values["CODEFLOW_GITHUB_REPO"], "codeflow")
        self.assertEqual(values["CODEFLOW_BASE_BRANCH"], "dev")
        self.assertEqual(values["EMPTY_VALUE"], "")

    def test_load_local_env_does_not_override_existing_values_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("CODEFLOW_BASE_BRANCH=dev\nCODEFLOW_GITHUB_REPO=codeflow\n")

            with patch.dict(os.environ, {"CODEFLOW_BASE_BRANCH": "main"}, clear=True):
                loaded = load_local_env(env_path)
                settings = env_settings()

        self.assertEqual(loaded["CODEFLOW_BASE_BRANCH"], "dev")
        self.assertEqual(settings["CODEFLOW_BASE_BRANCH"], "main")
        self.assertEqual(settings["CODEFLOW_GITHUB_REPO"], "codeflow")

    def test_env_settings_include_workflow_budget_minor_units(self) -> None:
        env = {
            "CODEFLOW_ENV_FILE": "/tmp/codeflow.env",
            "CODEFLOW_BILLING_CURRENCY_CODE": "EUR",
            "CODEFLOW_DATABASE_URL": "postgresql://user:secret@localhost:5432/codeflow",
            "CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS": "2500",
            "CODEFLOW_EMBEDDING_MODEL": "text-embedding-3-large",
            "CODEFLOW_MAX_COST_MINOR_UNITS": "9999",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = env_settings()

        self.assertEqual(settings["CODEFLOW_BILLING_CURRENCY_CODE"], "EUR")
        self.assertEqual(settings["CODEFLOW_DATABASE_URL"], "<redacted>")
        self.assertEqual(settings["CODEFLOW_ENV_FILE"], "/tmp/codeflow.env")
        self.assertEqual(settings["CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS"], "2500")
        self.assertEqual(settings["CODEFLOW_EMBEDDING_MODEL"], "text-embedding-3-large")
        self.assertNotIn("CODEFLOW_MAX_COST_MINOR_UNITS", settings)

    def test_github_settings_prefers_env_and_infers_repo_url(self) -> None:
        config = {"github": {"repo_url": "https://github.com/example-owner/example-repo.git", "base_branch": "main"}}
        env = {"CODEFLOW_BASE_BRANCH": "dev"}

        settings = github_settings(config, env=env)

        self.assertEqual(settings["owner"], "example-owner")
        self.assertEqual(settings["repo"], "example-repo")
        self.assertEqual(settings["base_branch"], "dev")
        self.assertEqual(settings["host"], "github.com")

    def test_github_cli_environment_sets_host_and_promotes_token(self) -> None:
        env = github_cli_environment({"GITHUB_TOKEN": "token-value"})

        self.assertEqual(env["GH_HOST"], "github.com")
        self.assertEqual(env["GH_TOKEN"], "token-value")

    def test_allowed_github_reviewers_prefers_env_csv(self) -> None:
        reviewers = allowed_github_reviewers(
            {"github": {"allowed_reviewers": ["config-reviewer"]}},
            env={"CODEFLOW_ALLOWED_REVIEWERS": "reviewer-one, reviewer-two"},
        )

        self.assertEqual(reviewers, ["reviewer-one", "reviewer-two"])


if __name__ == "__main__":
    unittest.main()
