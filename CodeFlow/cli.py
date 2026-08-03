from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .approval import evaluate_pull_request_approval
from .command_discovery import find_executable
from .config import (
    ConfigError,
    allowed_github_reviewers,
    env_settings,
    github_settings,
    load_local_env,
    load_project_config,
    resolve_config_path,
    workflow_requires_human_approval,
)
from .db.connection import DatabaseError, connect_database
from .db.review_persistence import persist_review_result
from .git_workflow import GitWorkflowError, commit_and_push_validated_step
from .model_router import validate_model_config
from .proposal import ProposalError, create_proposal_artifacts
from .pull_request import (
    PullRequestError,
    create_plan_pull_request,
    fetch_pull_request,
    fetch_pull_request_changed_files,
    fetch_pull_request_diff,
)
from .review_gate import configured_max_iterations, decide_review_loop
from .review_runner import CliReviewAgent, ReviewRunError, load_review_diff_file, load_review_finding_file, run_review_plan
from .review_routing import detect_review_plan
from .structured_logs import log_doctor_blocking_failures
from .workflow_runner import ClaudePhaseAgent, CodexPhaseAgent, WorkflowRunResult, run_implementation_workflow

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
    allowed_reviewers = allowed_github_reviewers(config)
    approval_required = workflow_requires_human_approval(config)
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
    checks.append(
        _check(
            "allowed_reviewers",
            bool(allowed_reviewers) or not approval_required,
            approval_required=approval_required,
            reviewer_count=len(allowed_reviewers),
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
    if args.open_pr:
        try:
            config = load_project_config(args.config)
            pull_request = create_plan_pull_request(result, config=config)
        except (ConfigError, PullRequestError) as exc:
            print(f"proposal pr error: {exc}", file=sys.stderr)
            return 1
        result_data["pull_request"] = pull_request.to_dict()

    if args.json:
        _print_json(result_data)
    else:
        print(f"CodeFlow propose {args.change_name}: created proposal artifacts")
        for label, path in result.artifacts.items():
            print(f"{label}: {path}")
        if args.open_pr:
            print(f"pull_request: {result_data['pull_request']['url']}")
        else:
            print("Next implementation step: create the plan branch and open the GitHub PR.")
    return 0


def command_run(args: argparse.Namespace) -> int:
    if args.agent is None:
        return _scaffold_notice("run", args.change_name)

    try:
        config = load_project_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    model_config_errors = validate_model_config(config)
    if model_config_errors:
        print("model config error: " + "; ".join(model_config_errors), file=sys.stderr)
        return 1

    approval_context: dict[str, Any] = {}
    if not args.dry_run:
        approval_result = _approved_implementation_context(args, config)
        if not approval_result["implementation_allowed"]:
            if args.json:
                _print_json(approval_result)
            else:
                print(f"CodeFlow run {args.change_name}: blocked before implementation")
                print(f"blocking_reason: {approval_result['approval'].get('blocking_reason')}")
            return 1
        approval_context = approval_result

    phase_agent_class = ClaudePhaseAgent if args.agent == "claude" else CodexPhaseAgent
    agent = phase_agent_class(
        config=config,
        work_dir=Path(args.work_dir) if args.work_dir else Path(".CodeFlow/runs") / args.change_name,
        timeout_seconds=args.timeout_seconds,
    )
    if not agent.adapter.is_available():
        print(f"{args.agent} CLI is not available on PATH", file=sys.stderr)
        return 1

    try:
        initial_context = _run_initial_context(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"run context error: {exc}", file=sys.stderr)
        return 1

    result = run_implementation_workflow(agent, initial_context={**initial_context, **approval_context})
    result_data = _workflow_result_to_dict(result)
    if approval_context:
        result_data["approval"] = approval_context["approval"]
        result_data["pull_request"] = approval_context["pull_request"]
    if approval_context and result.status == "completed" and result.safe_to_commit:
        try:
            commit_result = commit_and_push_validated_step(
                result.phase_results["code_creation"],
                result.phase_results["validation"],
                approval_context["pull_request"],
            )
        except GitWorkflowError as exc:
            result_data["status"] = "blocked"
            result_data["stopped_at"] = "commit_push"
            result_data["reason"] = str(exc)
            if args.json:
                _print_json(result_data)
            else:
                print(f"CodeFlow run {args.change_name}: blocked during commit/push")
                print(f"reason: {exc}")
            return 1
        result_data["commit"] = commit_result.to_dict()

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


def _approved_implementation_context(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.pr_number is None:
        return {
            "change_name": args.change_name,
            "implementation_allowed": False,
            "approval": {"approved": False, "approved_by": [], "changes_requested_by": [], "blocking_reason": "--pr-number is required for implementation"},
            "pull_request": {},
        }

    try:
        pull_request = fetch_pull_request(args.pr_number, config=config)
    except PullRequestError as exc:
        return {
            "change_name": args.change_name,
            "implementation_allowed": False,
            "approval": {"approved": False, "approved_by": [], "changes_requested_by": [], "blocking_reason": str(exc)},
            "pull_request": {},
        }

    approval = evaluate_pull_request_approval(pull_request, allowed_github_reviewers(config))
    pr_summary = _pull_request_summary(pull_request)
    implementation_allowed = bool(approval.approved and pull_request.get("state") == "OPEN")
    blocking_reason = approval.blocking_reason
    if approval.approved and pull_request.get("state") != "OPEN":
        blocking_reason = f"pull request is not open: {pull_request.get('state')}"

    approval_data = approval.to_dict()
    approval_data["blocking_reason"] = blocking_reason
    return {
        "change_name": args.change_name,
        "implementation_allowed": implementation_allowed,
        "approval": approval_data,
        "pull_request": pr_summary,
    }


def _run_initial_context(args: argparse.Namespace) -> dict[str, Any]:
    change_dir = Path(".CodeFlow/changes") / args.change_name
    context = {
        "change_name": args.change_name,
        "dry_run": bool(args.dry_run),
        "proposal_path": str(change_dir / "proposal.md"),
        "design_path": str(change_dir / "design.md"),
        "tasks_path": str(change_dir / "tasks.md"),
    }
    if args.review_result_file:
        review_result = _load_json_file(args.review_result_file)
        context["fix_iteration"] = args.fix_iteration
        context["review_result_status"] = review_result.get("status")
        context["open_review_findings"] = [
            finding
            for finding in review_result.get("findings", [])
            if isinstance(finding, dict) and finding.get("blocking")
        ]
    return context


def _pull_request_summary(pull_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pull_request.get("number"),
        "url": pull_request.get("url"),
        "state": pull_request.get("state"),
        "head_ref": pull_request.get("headRefName"),
        "base_ref": pull_request.get("baseRefName"),
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
    if args.pr_number is None:
        print("CodeFlow status currently requires --pr-number.", file=sys.stderr)
        return 2

    try:
        config = load_project_config(args.config)
        pull_request = fetch_pull_request(args.pr_number, config=config)
    except (ConfigError, PullRequestError) as exc:
        print(f"status error: {exc}", file=sys.stderr)
        return 1

    approval = evaluate_pull_request_approval(pull_request, allowed_github_reviewers(config))
    result = {
        "change_name": args.change_name,
        "pull_request": {
            "number": pull_request.get("number"),
            "url": pull_request.get("url"),
            "state": pull_request.get("state"),
            "head_ref": pull_request.get("headRefName"),
            "base_ref": pull_request.get("baseRefName"),
        },
        "approval": approval.to_dict(),
        "implementation_allowed": approval.approved and pull_request.get("state") == "OPEN",
    }
    if args.json:
        _print_json(result)
    else:
        print(f"CodeFlow status {args.change_name}: PR {pull_request.get('number')} {pull_request.get('state')}")
        print(f"implementation_allowed: {str(result['implementation_allowed']).lower()}")
        if approval.blocking_reason:
            print(f"blocking_reason: {approval.blocking_reason}")
    return 0 if result["implementation_allowed"] else 1


def command_review_plan(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        changed_files = _review_changed_files(args, config, "review-plan")
    except (ConfigError, PullRequestError) as exc:
        print(f"review-plan error: {exc}", file=sys.stderr)
        return 1

    review_plan = detect_review_plan(changed_files, config)
    result = {
        "change_name": args.change_name,
        "pull_request_number": args.pr_number,
        **review_plan.to_dict(),
    }
    if args.json:
        _print_json(result)
    else:
        print(f"CodeFlow review-plan {args.change_name}: {', '.join(result['required_roles']) or 'no roles'}")
        print(f"review_tasks: {', '.join(result['review_tasks']) or 'none'}")
    return 0


def command_review_run(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        changed_files = _review_changed_files(args, config, "review-run")
        diff_text, diff_source = _review_diff(args, config)
        raw_findings = load_review_finding_file(args.finding_file) if args.finding_file else []
    except (ConfigError, PullRequestError, ReviewRunError) as exc:
        print(f"review-run error: {exc}", file=sys.stderr)
        return 1

    review_plan = detect_review_plan(changed_files, config)
    agent_runner = None
    if args.real_agent:
        agent_runner = CliReviewAgent(
            work_dir=Path(args.work_dir) if args.work_dir else Path(".CodeFlow/runs") / args.change_name / "reviews",
            timeout_seconds=args.timeout_seconds,
        )
    review_result = run_review_plan(
        change_name=args.change_name,
        pull_request_number=args.pr_number,
        review_plan=review_plan,
        config=config,
        raw_findings=raw_findings,
        diff_text=diff_text,
        diff_source=diff_source,
        pinned_cli=None if args.agent == "auto" else args.agent,
        agent_runner=agent_runner,
    )
    result = review_result.to_dict()
    if args.workflow_run_id:
        try:
            persistence = _persist_review_run_result(
                workflow_run_id=args.workflow_run_id,
                result=review_result,
                commit_sha=args.commit_sha,
            )
        except (DatabaseError, RuntimeError) as exc:
            print(f"review-run persistence error: {exc}", file=sys.stderr)
            return 1
        result["persistence"] = persistence.to_dict()
    if args.output_file:
        try:
            _write_json_file(args.output_file, result)
        except OSError as exc:
            print(f"review-run output error: {exc}", file=sys.stderr)
            return 1
    if args.json:
        _print_json(result)
    else:
        print(f"CodeFlow review-run {args.change_name}: {result['status']}")
        print(f"findings: {result['findings_count']} total, {result['blocking_findings_count']} blocking")
        print(f"review_backend: {result['review_backend']}")
    return 0 if result["status"] in {"passed", "skipped"} else 1


def command_review_gate(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.config)
        review_result = _load_json_file(args.review_result_file)
    except (ConfigError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"review-gate error: {exc}", file=sys.stderr)
        return 1

    max_iterations = args.max_iterations or configured_max_iterations(config)
    decision = decide_review_loop(review_result, iteration=args.iteration, max_iterations=max_iterations).to_dict()
    decision["change_name"] = args.change_name
    if args.json:
        _print_json(decision)
    else:
        print(f"CodeFlow review-gate {args.change_name}: {decision['status']}")
        print(f"next_action: {decision['next_action']}")
        print(f"blocking_findings: {decision['blocking_findings_count']}")
    return 0 if decision["status"] in {"ready", "fix_required"} else 1


def _review_changed_files(args: argparse.Namespace, config: dict[str, Any], command_name: str) -> list[str]:
    if args.files:
        return list(args.files)
    if args.pr_number is None:
        raise PullRequestError(f"{command_name} requires --pr-number or at least one --file")
    return fetch_pull_request_changed_files(args.pr_number, config=config)


def _review_diff(args: argparse.Namespace, config: dict[str, Any]) -> tuple[str | None, str]:
    if args.diff_file:
        return load_review_diff_file(args.diff_file), "file"
    if args.pr_number is not None:
        return fetch_pull_request_diff(args.pr_number, config=config), "github_pr"
    return None, "unavailable"


def _persist_review_run_result(*, workflow_run_id: str, result: Any, commit_sha: str | None) -> Any:
    connection = connect_database()
    try:
        persistence = persist_review_result(connection, workflow_run_id=workflow_run_id, result=result, commit_sha=commit_sha)
        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()
        return persistence
    except Exception:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()
        raise
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _load_json_file(path: str) -> dict[str, Any]:
    parsed = json.loads(Path(path).read_text())
    if not isinstance(parsed, dict):
        raise ValueError("JSON file must contain an object")
    return parsed


def _write_json_file(path: str, data: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
    propose.add_argument("--open-pr", action="store_true", help="Create branch, commit artifacts, push, and open PR")
    propose.add_argument("--json", action="store_true", help="Print machine-readable proposal artifact output")
    propose.set_defaults(func=command_propose)

    run = subparsers.add_parser("run", help="Run approved implementation workflow")
    run.add_argument("change_name")
    run.add_argument("--agent", choices=["claude", "codex"], help="Agent backend to invoke for the workflow")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Invoke the agent in no-mutation smoke mode; no edits, commands, commits, or pushes are allowed.",
    )
    run.add_argument("--work-dir", help="Directory for generated phase prompts and outputs")
    run.add_argument("--timeout-seconds", type=int, default=600, help="Per-phase agent timeout")
    run.add_argument("--pr-number", type=int, help="Approved GitHub PR number required for non-dry-run implementation")
    run.add_argument("--review-result-file", help="review-run JSON file whose blocking findings should drive a fix iteration")
    run.add_argument("--fix-iteration", type=int, default=0, help="Fix iteration number for review-driven implementation")
    run.add_argument("--json", action="store_true", help="Print machine-readable workflow result")
    run.set_defaults(func=command_run)

    status = subparsers.add_parser("status", help="Show workflow status")
    status.add_argument("change_name")
    status.add_argument("--pr-number", type=int, help="GitHub PR number to inspect")
    status.add_argument("--json", action="store_true", help="Print machine-readable status output")
    status.set_defaults(func=command_status)

    review_plan = subparsers.add_parser("review-plan", help="Detect required review roles for a PR or file list")
    review_plan.add_argument("change_name")
    review_plan.add_argument("--pr-number", type=int, help="GitHub PR number to inspect")
    review_plan.add_argument("--file", dest="files", action="append", default=[], help="Changed file path to classify")
    review_plan.add_argument("--json", action="store_true", help="Print machine-readable review plan output")
    review_plan.set_defaults(func=command_review_plan)

    review_run = subparsers.add_parser("review-run", help="Run local review tasks for a PR or file list")
    review_run.add_argument("change_name")
    review_run.add_argument("--pr-number", type=int, help="GitHub PR number to inspect")
    review_run.add_argument("--file", dest="files", action="append", default=[], help="Changed file path to review")
    review_run.add_argument("--agent", choices=["auto", "claude", "codex"], default="auto", help="Agent CLI to route review tasks to")
    review_run.add_argument("--diff-file", help="Fake PR diff text file for local harness tests")
    review_run.add_argument("--finding-file", help="Fake review findings JSON file for local harness tests")
    review_run.add_argument("--real-agent", action="store_true", help="Invoke Claude/Codex CLI review agents instead of fake local findings only")
    review_run.add_argument("--work-dir", help="Directory for generated review prompts and outputs")
    review_run.add_argument("--timeout-seconds", type=int, default=600, help="Per-review-task agent timeout")
    review_run.add_argument("--workflow-run-id", help="Persist review runs and findings to this workflow_runs.id")
    review_run.add_argument("--commit-sha", help="Commit SHA reviewed by this review run")
    review_run.add_argument("--output-file", help="Write review-run JSON output to this file for review-gate or fix runs")
    review_run.add_argument("--json", action="store_true", help="Print machine-readable review run output")
    review_run.set_defaults(func=command_review_run)

    review_gate = subparsers.add_parser("review-gate", help="Decide whether review output is ready or needs another fix iteration")
    review_gate.add_argument("change_name")
    review_gate.add_argument("--review-result-file", required=True, help="Path to review-run JSON output")
    review_gate.add_argument("--iteration", type=int, default=0, help="Current completed fix iteration count")
    review_gate.add_argument("--max-iterations", type=int, help="Maximum allowed fix iterations")
    review_gate.add_argument("--json", action="store_true", help="Print machine-readable review gate output")
    review_gate.set_defaults(func=command_review_gate)

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
