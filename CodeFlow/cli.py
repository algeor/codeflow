from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .command_discovery import find_executable
from .config import ConfigError, env_settings, github_settings, load_local_env, load_project_config, resolve_config_path
from .model_router import validate_model_config
from .proposal import ProposalError, create_proposal_artifacts
from .structured_logs import log_doctor_blocking_failures
from .workflow_runner import ClaudePhaseAgent, WorkflowRunResult, run_implementation_workflow

REQUIRED_SKILLS = {
    "implement-init": Path(".CodeFlow/skills/implement-init/SKILL.md"),
    "implement-code-creation": Path(".CodeFlow/skills/implement-code-creation/SKILL.md"),
    "implement-validation": Path(".CodeFlow/skills/implement-validation/SKILL.md"),
    "proposal": Path(".CodeFlow/skills/proposal/SKILL.md"),
    "exploration": Path(".CodeFlow/skills/exploration/SKILL.md"),
}


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _check(name: str, ok: bool, *, required: bool = True, **details: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "required": required, **details}


def _configured_allowed_clis(config: dict[str, Any] | None) -> list[str]:
    agent_config = config.get("agent", {}) if isinstance(config, dict) else {}
    if not isinstance(agent_config, dict):
        return ["claude", "codex"]

    allowed = agent_config.get("allowed_clis", ["claude", "codex"])
    if not isinstance(allowed, list):
        return ["claude", "codex"]

    normalized = [str(item) for item in allowed if str(item) in {"claude", "codex"}]
    return normalized or ["claude", "codex"]


def _configured_default_cli(config: dict[str, Any] | None) -> str:
    agent_config = config.get("agent", {}) if isinstance(config, dict) else {}
    if not isinstance(agent_config, dict):
        return "auto"
    default_cli = str(agent_config.get("default_cli", "auto"))
    return default_cli if default_cli in {"auto", "claude", "codex"} else "invalid"


def command_doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []

    config_path = resolve_config_path(args.config)
    checks.append(_check("config", config_path.exists(), path=str(config_path)))

    for skill_name, path in REQUIRED_SKILLS.items():
        checks.append(_check(f"skill:{skill_name}", path.exists(), path=str(path)))

    gh_path = find_executable("gh")
    checks.append(_check("gh", gh_path is not None, path=gh_path))
    checks.append(_check("database", bool(os.getenv("CODEFLOW_DATABASE_URL")), env="CODEFLOW_DATABASE_URL"))

    try:
        config = load_project_config(args.config)
        config_error = None
    except ConfigError as exc:
        config = None
        config_error = str(exc)

    if config is not None:
        model_config_errors = validate_model_config(config)
        checks.append(_check("model_profiles", not model_config_errors, errors=model_config_errors))

    github = github_settings(config)
    checks.append(
        _check(
            "github_repo",
            bool(github["owner"] and github["repo"] and github["base_branch"]),
            owner=github["owner"] or None,
            repo=github["repo"] or None,
            base_branch=github["base_branch"] or None,
            host=github["host"],
        )
    )

    allowed_clis = _configured_allowed_clis(config)
    default_cli = _configured_default_cli(config)
    available_allowed_clis: list[str] = []
    for cli_name in ("claude", "codex"):
        path = find_executable(cli_name)
        is_allowed = cli_name in allowed_clis
        if is_allowed and path:
            available_allowed_clis.append(cli_name)
        checks.append(_check(cli_name, path is not None, required=False, path=path, allowed=is_allowed))

    if default_cli in {"claude", "codex"}:
        agent_cli_ok = default_cli in available_allowed_clis
    elif default_cli == "auto":
        agent_cli_ok = bool(available_allowed_clis)
    else:
        agent_cli_ok = False

    checks.append(
        _check(
            "agent_cli",
            agent_cli_ok,
            default_cli=default_cli,
            allowed_clis=allowed_clis,
            available_allowed_clis=available_allowed_clis,
        )
    )

    result = {
        "version": __version__,
        "checks": checks,
        "config_error": config_error,
        "env": env_settings(),
    }

    log_doctor_blocking_failures(checks, config_error)

    if args.json:
        _print_json(result)
    else:
        print(f"CodeFlow {__version__}")
        for check in checks:
            marker = "ok" if check["ok"] else "missing"
            detail = check.get("path") or check.get("env") or ""
            optional = " (optional)" if not check.get("required", True) else ""
            print(f"{marker:8} {check['name']}{optional} {detail}")
        if config_error:
            print(f"config   {config_error}")

    required_ok = config_error is None and all(check["ok"] for check in checks if check.get("required", True))
    return 0 if required_ok else 1


def _scaffold_notice(command: str, change_name: str | None = None) -> int:
    suffix = f" for {change_name}" if change_name else ""
    print(f"CodeFlow {command}{suffix}: scaffold command is wired, runner implementation is pending.")
    print("Next implementation step: connect this command to Postgres state and the LangGraph runner.")
    return 0


def command_propose(args: argparse.Namespace) -> int:
    try:
        result = create_proposal_artifacts(
            args.change_name,
            " ".join(args.request),
            overwrite=args.overwrite,
        )
    except ProposalError as exc:
        print(f"proposal error: {exc}", file=sys.stderr)
        return 1

    result_data = result.to_dict()
    if args.json:
        _print_json(result_data)
    else:
        print(f"CodeFlow propose {args.change_name}: created proposal artifacts")
        for label, path in result.artifacts.items():
            print(f"{label}: {path}")
        print("Next implementation step: create the plan branch and open the GitHub PR.")
    return 0


def command_run(args: argparse.Namespace) -> int:
    if args.agent is None:
        return _scaffold_notice("run", args.change_name)

    if args.agent != "claude":
        print(f"unsupported run agent: {args.agent}", file=sys.stderr)
        return 2

    if not args.dry_run:
        print("CodeFlow run --agent claude currently requires --dry-run.", file=sys.stderr)
        return 2

    try:
        config = load_project_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    model_config_errors = validate_model_config(config)
    if model_config_errors:
        print("model config error: " + "; ".join(model_config_errors), file=sys.stderr)
        return 1

    agent = ClaudePhaseAgent(
        config=config,
        work_dir=Path(args.work_dir) if args.work_dir else Path(".CodeFlow/runs") / args.change_name,
        timeout_seconds=args.timeout_seconds,
    )
    if not agent.adapter.is_available():
        print("claude CLI is not available on PATH", file=sys.stderr)
        return 1

    result = run_implementation_workflow(agent, initial_context=_run_initial_context(args))
    result_data = _workflow_result_to_dict(result)

    if args.json:
        _print_json(result_data)
    else:
        print(f"CodeFlow run {args.change_name}: {result.status}")
        print(f"phases: {', '.join(result.phase_order)}")
        print(f"safe_to_commit: {str(result.safe_to_commit).lower()}")
        if result.stopped_at:
            print(f"stopped_at: {result.stopped_at}")
        if result.reason:
            print(f"reason: {result.reason}")

    return 0 if result.status == "completed" and result.safe_to_commit else 1


def _run_initial_context(args: argparse.Namespace) -> dict[str, Any]:
    change_dir = Path(".CodeFlow/changes") / args.change_name
    return {
        "change_name": args.change_name,
        "dry_run": bool(args.dry_run),
        "proposal_path": str(change_dir / "proposal.md"),
        "design_path": str(change_dir / "design.md"),
        "tasks_path": str(change_dir / "tasks.md"),
    }


def _workflow_result_to_dict(result: WorkflowRunResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "phase_order": result.phase_order,
        "safe_to_commit": result.safe_to_commit,
        "stopped_at": result.stopped_at,
        "reason": result.reason,
        "phase_results": result.phase_results,
        "summaries": result.summaries,
    }


def command_status(args: argparse.Namespace) -> int:
    return _scaffold_notice("status", args.change_name)


def command_resume(args: argparse.Namespace) -> int:
    return _scaffold_notice("resume", args.change_name)


def command_abort(args: argparse.Namespace) -> int:
    return _scaffold_notice("abort", args.change_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="CodeFlow")
    parser.add_argument("--version", action="version", version=f"CodeFlow {__version__}")
    parser.add_argument("--config", help="Path to .CodeFlow/config.yaml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local CodeFlow prerequisites")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable output")
    doctor.set_defaults(func=command_doctor)

    propose = subparsers.add_parser("propose", help="Create proposal artifacts for a plan PR")
    propose.add_argument("change_name")
    propose.add_argument("request", nargs="*", help="Change request text")
    propose.add_argument("--overwrite", action="store_true", help="Overwrite existing proposal artifacts")
    propose.add_argument("--json", action="store_true", help="Print machine-readable proposal artifact output")
    propose.set_defaults(func=command_propose)

    run = subparsers.add_parser("run", help="Run approved implementation workflow")
    run.add_argument("change_name")
    run.add_argument("--agent", choices=["claude"], help="Agent backend to invoke for the workflow")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Invoke the agent in no-mutation smoke mode; no edits, commands, commits, or pushes are allowed.",
    )
    run.add_argument("--work-dir", help="Directory for generated phase prompts and outputs")
    run.add_argument("--timeout-seconds", type=int, default=600, help="Per-phase agent timeout")
    run.add_argument("--json", action="store_true", help="Print machine-readable workflow result")
    run.set_defaults(func=command_run)

    status = subparsers.add_parser("status", help="Show workflow status")
    status.add_argument("change_name")
    status.set_defaults(func=command_status)

    resume = subparsers.add_parser("resume", help="Resume an interrupted workflow")
    resume.add_argument("change_name")
    resume.set_defaults(func=command_resume)

    abort = subparsers.add_parser("abort", help="Abort a workflow without deleting artifacts")
    abort.add_argument("change_name")
    abort.set_defaults(func=command_abort)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        load_local_env()
    except ConfigError as exc:
        print(f"env error: {exc}", file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
