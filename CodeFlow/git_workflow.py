from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from .config import github_cli_environment


class GitWorkflowError(RuntimeError):
    """Raised when a workflow-owned git operation cannot safely complete."""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    return_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class StepCommitResult:
    branch: str
    commit: str
    files_committed: list[str]
    commands_run: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "commit": self.commit,
            "files_committed": self.files_committed,
            "commands_run": self.commands_run,
        }


CommandRunner = Callable[[Sequence[str], dict[str, str] | None, Path], CommandResult]

_FORBIDDEN_PATH_PREFIXES = (
    ".agents",
    ".claude",
    ".mcp",
    ".mcps",
    ".CodeFlow/runs",
)


def commit_and_push_validated_step(
    code_creation_result: dict[str, Any],
    validation_result: dict[str, Any],
    pull_request: dict[str, Any],
    *,
    cwd: Path | str = Path("."),
    runner: CommandRunner | None = None,
) -> StepCommitResult:
    if not validation_result.get("safe_to_commit"):
        raise GitWorkflowError("validation did not mark the step safe to commit")

    branch = str(pull_request.get("head_ref") or pull_request.get("headRefName") or "").strip()
    if not branch:
        raise GitWorkflowError("pull request head branch is required before committing")

    files_to_commit = _validated_changed_files(code_creation_result)
    root = Path(cwd)
    command_runner = runner or run_command
    env = _git_environment()
    commands_run: list[list[str]] = []

    current_branch = _run_required(command_runner, ["git", "branch", "--show-current"], env, root, commands_run).stdout.strip()
    if current_branch != branch:
        raise GitWorkflowError(f"current branch {current_branch!r} does not match PR head branch {branch!r}")

    _run_required(command_runner, ["git", "add", "--", *files_to_commit], env, root, commands_run)
    _run_required(command_runner, ["git", "commit", "-m", _commit_message(code_creation_result)], env, root, commands_run)
    commit_sha = _run_required(command_runner, ["git", "rev-parse", "HEAD"], env, root, commands_run).stdout.strip()
    _run_required(command_runner, ["git", "push", "origin", branch], _git_push_environment(env), root, commands_run)

    return StepCommitResult(branch=branch, commit=commit_sha, files_committed=files_to_commit, commands_run=commands_run)


def run_command(args: Sequence[str], env: dict[str, str] | None, cwd: Path) -> CommandResult:
    completed = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env, check=False)
    return CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


def _run_required(
    runner: CommandRunner,
    args: list[str],
    env: dict[str, str],
    cwd: Path,
    commands_run: list[list[str]],
) -> CommandResult:
    result = runner(args, env, cwd)
    commands_run.append(list(args))
    if result.return_code != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise GitWorkflowError(f"command failed ({' '.join(args)}): {message}")
    return result


def _validated_changed_files(code_creation_result: dict[str, Any]) -> list[str]:
    raw_files = [
        *_as_string_list(code_creation_result.get("files_changed")),
        *_as_string_list(code_creation_result.get("tests_changed")),
    ]
    files: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_files:
        path = _validate_commit_path(raw_path)
        if path not in seen:
            seen.add(path)
            files.append(path)
    if not files:
        raise GitWorkflowError("code creation did not report files to commit")
    return files


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _validate_commit_path(raw_path: str) -> str:
    normalized = raw_path.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized in {"", "."}:
        raise GitWorkflowError(f"unsafe commit path: {raw_path}")
    if any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in _FORBIDDEN_PATH_PREFIXES):
        raise GitWorkflowError(f"refusing to commit workflow/local configuration path: {raw_path}")
    return normalized


def _commit_message(code_creation_result: dict[str, Any]) -> str:
    logical_step = str(code_creation_result.get("logical_step") or "implementation-step").strip()
    return f"Implement {logical_step}"


def _git_environment() -> dict[str, str]:
    return github_cli_environment()


def _git_push_environment(env: dict[str, str]) -> dict[str, str]:
    push_env = dict(env)
    token = push_env.get("GH_TOKEN") or push_env.get("GITHUB_TOKEN")
    if not token:
        raise GitWorkflowError("GH_TOKEN or GITHUB_TOKEN is required to push workflow commits")
    auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    push_env["GIT_CONFIG_COUNT"] = "1"
    push_env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    push_env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: Basic {auth}"
    return push_env
