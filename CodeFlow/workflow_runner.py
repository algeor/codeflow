from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .adapters.base import AgentRequest, CliAgentAdapter
from .adapters.claude_cli import ClaudeCliAdapter
from .adapters.codex_cli import CodexCliAdapter
from .model_router import route_task
from .orchestration import IMPLEMENTATION_PHASES, WorkflowPhase, concise_phase_summary, phase_succeeded


class PhaseAgent(Protocol):
    def run_phase(self, phase: WorkflowPhase, context: dict[str, Any]) -> dict[str, Any]:
        """Run one phase and return the phase JSON result."""


@dataclass(frozen=True)
class WorkflowRunResult:
    status: str
    phase_results: dict[str, dict[str, Any]]
    phase_order: list[str]
    summaries: dict[str, str]
    safe_to_commit: bool = False
    stopped_at: str | None = None
    reason: str | None = None


@dataclass
class FakePhaseAgent:
    responses: dict[str, dict[str, Any]]
    calls: list[str] = field(default_factory=list)
    contexts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def run_phase(self, phase: WorkflowPhase, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(phase.phase_id)
        self.contexts[phase.phase_id] = dict(context)
        return dict(self.responses[phase.phase_id])


class CliPhaseAgent:
    def __init__(
        self,
        adapter: CliAgentAdapter,
        *,
        config: dict[str, Any],
        work_dir: Path,
        timeout_seconds: int = 600,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.work_dir = Path(work_dir)
        self.timeout_seconds = timeout_seconds

    def run_phase(self, phase: WorkflowPhase, context: dict[str, Any]) -> dict[str, Any]:
        route = route_task(phase.task_type, self.config, pinned_cli=self.adapter.provider_cli)
        prompt_path = self.work_dir / "prompts" / f"{phase.phase_id}.md"
        output_path = self.work_dir / "outputs" / f"{phase.phase_id}.json"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(build_phase_prompt(phase, context))

        request = AgentRequest(
            task_type=phase.task_type,
            model=route.model_id,
            prompt_path=prompt_path,
            output_path=output_path,
            timeout_seconds=self.timeout_seconds,
            expected_schema={"type": "object", "required": ["status"]},
            metadata={
                "phase_id": phase.phase_id,
                "skill_name": phase.skill_name,
                "model_tier": route.model_tier,
                "route_reason": route.reason,
            },
        )
        result = self.adapter.invoke(request)
        if result.status != "succeeded":
            return failed_phase_result(phase, f"{self.adapter.provider_cli} CLI {result.status}: {result.stderr}".strip())

        try:
            phase_result = parse_phase_output(result.output_path.read_text())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return failed_phase_result(phase, f"could not parse {phase.phase_id} JSON result: {exc}")
        phase_result["agent_invocation"] = _agent_invocation_summary(result, request)
        return phase_result


class ClaudePhaseAgent(CliPhaseAgent):
    def __init__(self, *, config: dict[str, Any], work_dir: Path, timeout_seconds: int = 600) -> None:
        super().__init__(ClaudeCliAdapter(), config=config, work_dir=work_dir, timeout_seconds=timeout_seconds)


class CodexPhaseAgent(CliPhaseAgent):
    def __init__(self, *, config: dict[str, Any], work_dir: Path, timeout_seconds: int = 600) -> None:
        super().__init__(CodexCliAdapter(), config=config, work_dir=work_dir, timeout_seconds=timeout_seconds)


def run_implementation_workflow(agent: PhaseAgent, *, initial_context: dict[str, Any] | None = None) -> WorkflowRunResult:
    context: dict[str, Any] = dict(initial_context or {})
    phase_results: dict[str, dict[str, Any]] = {}
    summaries: dict[str, str] = {}
    phase_order: list[str] = []

    for phase in IMPLEMENTATION_PHASES:
        result = agent.run_phase(phase, context)
        phase_order.append(phase.phase_id)
        phase_results[phase.phase_id] = result

        status = str(result.get("status", ""))
        summaries[phase.phase_id] = concise_phase_summary(phase.phase_id, result)
        context[f"{phase.phase_id}_summary"] = summaries[phase.phase_id]

        if not phase_succeeded(phase.phase_id, status):
            return WorkflowRunResult(
                status="blocked",
                phase_results=phase_results,
                phase_order=phase_order,
                summaries=summaries,
                stopped_at=phase.phase_id,
                reason=str(result.get("reason") or status or "phase failed"),
            )

    validation = phase_results["validation"]
    safe_to_commit = bool(validation.get("safe_to_commit"))
    return WorkflowRunResult(
        status="completed" if safe_to_commit else "blocked",
        phase_results=phase_results,
        phase_order=phase_order,
        summaries=summaries,
        safe_to_commit=safe_to_commit,
        stopped_at=None if safe_to_commit else "validation",
        reason=None if safe_to_commit else "validation did not allow commit",
    )


def build_phase_prompt(phase: WorkflowPhase, context: dict[str, Any]) -> str:
    skill_path = (Path.cwd() / ".CodeFlow" / "skills" / phase.skill_name / "SKILL.md").resolve()
    context_json = json.dumps(context, indent=2, sort_keys=True)
    dry_run_rules = """
Dry-run mode is active:
- Do not edit files.
- Do not run shell commands.
- Do not configure git.
- Do not commit or push.
- Return what the phase can honestly determine from the provided context.
- If the phase cannot complete without mutation, return the phase's blocked or escalation JSON.
""" if context.get("dry_run") else ""
    return f"""Run the CodeFlow phase `{phase.phase_id}`.

Use the skill at:
{skill_path}

Rules:
- Read and follow that skill exactly.
- Return the phase result as a single JSON object.
- Do not add trailing commentary after the JSON.
{dry_run_rules}

Context JSON:
```json
{context_json}
```
"""


def parse_phase_output(raw_output: str) -> dict[str, Any]:
    stripped = raw_output.strip()
    if not stripped:
        raise ValueError("empty output")

    parsed = _loads_json_object(stripped)
    if parsed is not None:
        return parsed

    for line in reversed(stripped.splitlines()):
        parsed = _loads_json_object(line.strip())
        if parsed is not None:
            return parsed

    raise ValueError("no JSON object found")


def failed_phase_result(phase: WorkflowPhase, reason: str) -> dict[str, Any]:
    if phase.phase_id == "init":
        return {"status": "escalate", "reason": reason}
    if phase.phase_id == "code_creation":
        return {"status": "blocked", "reason": reason, "blocking_questions": []}
    if phase.phase_id == "validation":
        return {"status": "failed", "reason": reason, "safe_to_commit": False, "blocking_failures": [reason]}
    return {"status": "failed", "reason": reason}


def _agent_invocation_summary(result: Any, request: AgentRequest) -> dict[str, Any]:
    metadata = request.metadata
    summary = {
        "provider_cli": result.provider_cli,
        "model": result.model,
        "status": result.status,
        "return_code": result.return_code,
        "duration_ms": result.duration_ms,
        "task_type": request.task_type,
        "model_tier": metadata.get("model_tier"),
        "routing_reason": metadata.get("route_reason"),
        "token_usage": result.metadata.get("token_usage", {}),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _loads_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict) and "status" in parsed:
        return parsed
    if isinstance(parsed, dict):
        for key in ("result", "response", "text", "content"):
            value = parsed.get(key)
            if isinstance(value, str):
                nested = parse_phase_output(value)
                if nested is not None:
                    return nested
    return None
