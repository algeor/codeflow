from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from . import __version__
from .command_discovery import find_executable
from .config import ConfigError, env_settings, load_project_config, resolve_config_path
from .structured_logs import log_doctor_blocking_failures

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
    return _scaffold_notice("propose", args.change_name)


def command_run(args: argparse.Namespace) -> int:
    return _scaffold_notice("run", args.change_name)


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

    propose = subparsers.add_parser("propose", help="Create plan artifacts and open a plan PR")
    propose.add_argument("change_name")
    propose.add_argument("request", nargs="*", help="Change request text")
    propose.set_defaults(func=command_propose)

    run = subparsers.add_parser("run", help="Run approved implementation workflow")
    run.add_argument("change_name")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
