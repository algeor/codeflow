from __future__ import annotations

import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import github_cli_environment, github_settings
from .proposal import ProposalArtifactResult


class PullRequestError(RuntimeError):
    """Raised when the plan pull request lifecycle cannot complete."""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    return_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class PlanPullRequestResult:
    branch: str
    url: str
    commands_run: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {"branch": self.branch, "url": self.url, "commands_run": self.commands_run}


CommandRunner = Callable[[Sequence[str], dict[str, str] | None, Path], CommandResult]


def create_plan_pull_request(
    proposal: ProposalArtifactResult,
    *,
    config: dict[str, Any],
    cwd: Path | str = Path("."),
    runner: CommandRunner | None = None,
) -> PlanPullRequestResult:
    settings = github_settings(config)
    owner = settings["owner"]
    repo = settings["repo"]
    base_branch = settings["base_branch"]
    if not owner or not repo or not base_branch:
        raise PullRequestError("GitHub owner, repo, and base branch are required to create a PR")

    github_config = config.get("github", {}) if isinstance(config.get("github", {}), dict) else {}
    plan_label = str(github_config.get("plan_label") or "plan-only").strip()
    branch = f"plan/{proposal.change_name}"
    repo_slug = f"{owner}/{repo}"
    root = Path(cwd)
    command_runner = runner or run_command
    env = github_cli_environment()
    env["GH_HOST"] = settings["host"]
    commands_run: list[list[str]] = []

    for command in _plan_pr_commands(proposal, branch):
        result = command_runner(command, env, root)
        commands_run.append(list(command))
        if result.return_code != 0:
            message = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise PullRequestError(f"command failed ({' '.join(command)}): {message}")

    pr_create_command = _pr_create_command(proposal, branch, repo_slug, base_branch, plan_label)
    pr_create_output = command_runner(pr_create_command, env, root)
    commands_run.append(list(pr_create_command))
    if pr_create_output.return_code != 0:
        message = pr_create_output.stderr.strip() or pr_create_output.stdout.strip() or "gh pr create failed"
        raise PullRequestError(f"command failed ({' '.join(pr_create_command)}): {message}")

    return PlanPullRequestResult(branch=branch, url=_extract_pr_url(pr_create_output.stdout), commands_run=commands_run)


def fetch_pull_request(
    pr_number: int,
    *,
    config: dict[str, Any],
    cwd: Path | str = Path("."),
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    settings = github_settings(config)
    owner = settings["owner"]
    repo = settings["repo"]
    if not owner or not repo:
        raise PullRequestError("GitHub owner and repo are required to read a PR")

    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "number,url,state,reviewDecision,reviews,author,headRefName,baseRefName",
    ]
    env = github_cli_environment()
    env["GH_HOST"] = settings["host"]
    result = (runner or run_command)(command, env, Path(cwd))
    if result.return_code != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh pr view failed"
        raise PullRequestError(f"command failed ({' '.join(command)}): {message}")

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PullRequestError(f"gh pr view returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PullRequestError("gh pr view JSON must be an object")
    return parsed


def fetch_pull_request_changed_files(
    pr_number: int,
    *,
    config: dict[str, Any],
    cwd: Path | str = Path("."),
    runner: CommandRunner | None = None,
) -> list[str]:
    settings = github_settings(config)
    owner = settings["owner"]
    repo = settings["repo"]
    if not owner or not repo:
        raise PullRequestError("GitHub owner and repo are required to read PR changed files")

    command = ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}", "--name-only"]
    env = github_cli_environment()
    env["GH_HOST"] = settings["host"]
    result = (runner or run_command)(command, env, Path(cwd))
    if result.return_code != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh pr diff failed"
        raise PullRequestError(f"command failed ({' '.join(command)}): {message}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def fetch_pull_request_diff(
    pr_number: int,
    *,
    config: dict[str, Any],
    cwd: Path | str = Path("."),
    runner: CommandRunner | None = None,
) -> str:
    settings = github_settings(config)
    owner = settings["owner"]
    repo = settings["repo"]
    if not owner or not repo:
        raise PullRequestError("GitHub owner and repo are required to read PR diff")

    command = ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}"]
    env = github_cli_environment()
    env["GH_HOST"] = settings["host"]
    result = (runner or run_command)(command, env, Path(cwd))
    if result.return_code != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh pr diff failed"
        raise PullRequestError(f"command failed ({' '.join(command)}): {message}")
    return result.stdout


def run_command(args: Sequence[str], env: dict[str, str] | None, cwd: Path) -> CommandResult:
    completed = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env, check=False)
    return CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


def _plan_pr_commands(
    proposal: ProposalArtifactResult,
    branch: str,
) -> list[list[str]]:
    return [
        ["git", "switch", "-c", branch],
        ["git", "add", *_proposal_artifact_paths(proposal)],
        ["git", "commit", "-m", f"Plan {proposal.title}"],
        ["git", "push", "-u", "origin", branch],
    ]


def _pr_create_command(
    proposal: ProposalArtifactResult,
    branch: str,
    repo_slug: str,
    base_branch: str,
    plan_label: str,
) -> list[str]:
    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        repo_slug,
        "--base",
        base_branch,
        "--head",
        branch,
        "--title",
        proposal.title,
        "--body-file",
        proposal.artifacts["proposal"],
    ]
    if plan_label:
        command.extend(["--label", plan_label])
    return command


def _proposal_artifact_paths(proposal: ProposalArtifactResult) -> list[str]:
    base = f".CodeFlow/changes/{proposal.change_name}"
    return [
        proposal.artifacts["proposal"],
        proposal.artifacts["design"],
        proposal.artifacts["tasks"],
        proposal.artifacts["questions"],
        f"{base}/proposal.json",
    ]


def _extract_pr_url(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("https://"):
            return stripped
    raise PullRequestError("gh pr create did not return a PR URL")
