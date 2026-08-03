from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .adapters.base import AgentRequest, CliAgentAdapter
from .adapters.claude_cli import ClaudeCliAdapter
from .adapters.codex_cli import CodexCliAdapter
from .model_router import ModelRoute, route_task
from .review_routing import ReviewPlan


class ReviewRunError(RuntimeError):
    """Raised when review execution input cannot be normalized."""


class ReviewAgentRunner(Protocol):
    def run_review_task(
        self,
        *,
        review_task: str,
        review_plan: ReviewPlan,
        diff_text: str | None,
        route: ModelRoute,
    ) -> "ReviewAgentTaskResult":
        """Run one review task and return raw findings from the agent."""


@dataclass(frozen=True)
class ReviewAgentTaskResult:
    status: str
    raw_findings: list[dict[str, Any]]
    reason: str | None = None
    prompt_path: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class ReviewFinding:
    blocking: bool
    severity: str
    file_path: str | None
    line: int | None
    summary: str
    recommendation: str
    review_task: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "severity": self.severity,
            "file_path": self.file_path,
            "line": self.line,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "review_task": self.review_task,
        }


@dataclass(frozen=True)
class ReviewTaskRun:
    review_task: str
    provider_cli: str
    model_tier: str
    model_id: str
    routing_reason: str
    status: str
    findings_count: int
    blocking_findings_count: int
    reason: str | None = None
    prompt_path: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "review_task": self.review_task,
            "provider_cli": self.provider_cli,
            "model_tier": self.model_tier,
            "model_id": self.model_id,
            "routing_reason": self.routing_reason,
            "status": self.status,
            "findings_count": self.findings_count,
            "blocking_findings_count": self.blocking_findings_count,
        }
        if self.reason:
            data["reason"] = self.reason
        if self.prompt_path:
            data["prompt_path"] = self.prompt_path
        if self.output_path:
            data["output_path"] = self.output_path
        if self.duration_ms is not None:
            data["duration_ms"] = self.duration_ms
        return data


@dataclass(frozen=True)
class ReviewRunResult:
    change_name: str
    pull_request_number: int | None
    review_plan: ReviewPlan
    task_runs: list[ReviewTaskRun]
    findings: list[ReviewFinding]
    diff_source: str = "unavailable"
    diff_text: str | None = None
    review_backend: str = "local-fake"
    real_agent_review: bool = False

    @property
    def blocking_findings(self) -> list[ReviewFinding]:
        return [finding for finding in self.findings if finding.blocking]

    @property
    def status(self) -> str:
        if any(task_run.status == "failed" for task_run in self.task_runs):
            return "failed"
        if not self.review_plan.review_tasks:
            return "skipped"
        return "blocked" if self.blocking_findings else "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_name": self.change_name,
            "pull_request_number": self.pull_request_number,
            "status": self.status,
            "review_backend": self.review_backend,
            "real_agent_review": self.real_agent_review,
            "review_input": {
                "diff_source": self.diff_source,
                "diff_line_count": len((self.diff_text or "").splitlines()),
            },
            "review_plan": self.review_plan.to_dict(),
            "task_runs": [task_run.to_dict() for task_run in self.task_runs],
            "findings": [finding.to_dict() for finding in self.findings],
            "findings_count": len(self.findings),
            "blocking_findings_count": len(self.blocking_findings),
        }


def run_review_plan(
    *,
    change_name: str,
    pull_request_number: int | None,
    review_plan: ReviewPlan,
    config: dict[str, Any],
    raw_findings: list[dict[str, Any]] | None = None,
    diff_text: str | None = None,
    diff_source: str = "unavailable",
    pinned_cli: str | None = None,
    agent_runner: ReviewAgentRunner | None = None,
) -> ReviewRunResult:
    agent_results: dict[str, ReviewAgentTaskResult] = {}
    task_routes = {review_task: route_task(review_task, config, pinned_cli=pinned_cli) for review_task in review_plan.review_tasks}
    all_raw_findings = list(raw_findings or [])
    if agent_runner is not None:
        for review_task, route in task_routes.items():
            agent_result = agent_runner.run_review_task(
                review_task=review_task,
                review_plan=review_plan,
                diff_text=diff_text,
                route=route,
            )
            agent_results[review_task] = agent_result
            if agent_result.status == "succeeded":
                all_raw_findings.extend(agent_result.raw_findings)

    findings = normalize_findings(
        all_raw_findings,
        default_review_task=_default_review_task(review_plan),
        blocking_severities=_configured_blocking_severities(config),
    )
    task_runs = [
        _task_run(review_task, task_routes[review_task], findings, agent_results.get(review_task))
        for review_task in review_plan.review_tasks
    ]
    return ReviewRunResult(
        change_name=change_name,
        pull_request_number=pull_request_number,
        review_plan=review_plan,
        task_runs=task_runs,
        findings=findings,
        diff_source=diff_source,
        diff_text=diff_text,
        review_backend="local-agent" if agent_runner is not None else "local-fake",
        real_agent_review=agent_runner is not None,
    )


class CliReviewAgent:
    def __init__(self, *, work_dir: Path, timeout_seconds: int = 600) -> None:
        self.work_dir = Path(work_dir)
        self.timeout_seconds = timeout_seconds

    def run_review_task(
        self,
        *,
        review_task: str,
        review_plan: ReviewPlan,
        diff_text: str | None,
        route: ModelRoute,
    ) -> ReviewAgentTaskResult:
        adapter = _adapter_for_cli(route.provider_cli)
        if not adapter.is_available():
            return ReviewAgentTaskResult(
                status="failed",
                raw_findings=[],
                reason=f"{route.provider_cli} CLI is not available on PATH",
            )

        prompt_path = self.work_dir / "prompts" / f"{review_task}.md"
        output_path = self.work_dir / "outputs" / f"{review_task}.json"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(_build_review_prompt(review_task, review_plan, diff_text))

        request = AgentRequest(
            task_type=review_task,
            model=route.model_id,
            prompt_path=prompt_path,
            output_path=output_path,
            timeout_seconds=self.timeout_seconds,
            expected_schema={"type": "object", "required": ["findings"]},
            metadata={"model_tier": route.model_tier, "routing_reason": route.reason},
        )
        result = adapter.invoke(request)
        if result.status != "succeeded":
            return ReviewAgentTaskResult(
                status="failed",
                raw_findings=[],
                reason=f"{route.provider_cli} CLI {result.status}: {result.stderr}".strip(),
                prompt_path=str(prompt_path),
                output_path=str(output_path),
                duration_ms=result.duration_ms,
            )

        try:
            raw_findings = parse_review_agent_output(output_path.read_text())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return ReviewAgentTaskResult(
                status="failed",
                raw_findings=[],
                reason=f"could not parse review agent output: {exc}",
                prompt_path=str(prompt_path),
                output_path=str(output_path),
                duration_ms=result.duration_ms,
            )
        return ReviewAgentTaskResult(
            status="succeeded",
            raw_findings=raw_findings,
            prompt_path=str(prompt_path),
            output_path=str(output_path),
            duration_ms=result.duration_ms,
        )


def parse_review_agent_output(raw_output: str) -> list[dict[str, Any]]:
    parsed = _loads_review_json(raw_output.strip())
    if parsed is None:
        for line in reversed(raw_output.strip().splitlines()):
            parsed = _loads_review_json(line.strip())
            if parsed is not None:
                break
    if parsed is None:
        raise ValueError("no review JSON object found")
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        return _ensure_object_list(parsed["findings"])
    if isinstance(parsed, list):
        return _ensure_object_list(parsed)
    raise ValueError("review JSON must be a list or an object with findings")


def load_review_diff_file(path: str | Path) -> str:
    diff_path = Path(path)
    try:
        return diff_path.read_text()
    except OSError as exc:
        raise ReviewRunError(f"could not read diff file: {exc}") from exc


def load_review_finding_file(path: str | Path) -> list[dict[str, Any]]:
    finding_path = Path(path)
    try:
        parsed = json.loads(finding_path.read_text())
    except OSError as exc:
        raise ReviewRunError(f"could not read finding file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewRunError(f"finding file is not valid JSON: {exc}") from exc

    if isinstance(parsed, list):
        return _ensure_object_list(parsed)
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        return _ensure_object_list(parsed["findings"])
    raise ReviewRunError("finding file must contain a JSON list or an object with a findings list")


def normalize_findings(
    raw_findings: list[dict[str, Any]],
    *,
    default_review_task: str,
    blocking_severities: set[str],
) -> list[ReviewFinding]:
    return [normalize_finding(raw, default_review_task=default_review_task, blocking_severities=blocking_severities) for raw in raw_findings]


def normalize_finding(
    raw: dict[str, Any],
    *,
    default_review_task: str,
    blocking_severities: set[str],
) -> ReviewFinding:
    severity = _normalized_severity(raw.get("severity"))
    explicit_blocking = raw.get("blocking") if isinstance(raw.get("blocking"), bool) else False
    review_task = str(raw.get("review_task") or raw.get("task") or default_review_task).strip() or default_review_task
    return ReviewFinding(
        blocking=bool(explicit_blocking or severity in blocking_severities),
        severity=severity,
        file_path=_optional_text(raw.get("file_path") or raw.get("path") or raw.get("file")),
        line=_optional_line(raw.get("line") or raw.get("line_start")),
        summary=str(raw.get("summary") or raw.get("title") or "Review finding").strip(),
        recommendation=str(raw.get("recommendation") or raw.get("suggested_fix") or "").strip(),
        review_task=review_task,
    )


def _task_run(
    review_task: str,
    route: ModelRoute,
    findings: list[ReviewFinding],
    agent_result: ReviewAgentTaskResult | None,
) -> ReviewTaskRun:
    task_findings = [finding for finding in findings if finding.review_task == review_task]
    blocking_findings = [finding for finding in task_findings if finding.blocking]
    status = "findings_opened" if blocking_findings else "passed"
    if agent_result is not None and agent_result.status != "succeeded":
        status = "failed"
    return ReviewTaskRun(
        review_task=review_task,
        provider_cli=route.provider_cli,
        model_tier=route.model_tier,
        model_id=route.model_id,
        routing_reason=route.reason,
        status=status,
        findings_count=len(task_findings),
        blocking_findings_count=len(blocking_findings),
        reason=agent_result.reason if agent_result else None,
        prompt_path=agent_result.prompt_path if agent_result else None,
        output_path=agent_result.output_path if agent_result else None,
        duration_ms=agent_result.duration_ms if agent_result else None,
    )


def _adapter_for_cli(provider_cli: str) -> CliAgentAdapter:
    if provider_cli == "claude":
        return ClaudeCliAdapter()
    if provider_cli == "codex":
        return CodexCliAdapter()
    raise ReviewRunError(f"unsupported review agent CLI: {provider_cli}")


def _build_review_prompt(review_task: str, review_plan: ReviewPlan, diff_text: str | None) -> str:
    return f"""Run CodeFlow review task `{review_task}`.

Review only the provided pull request diff and changed-file plan.
Return one JSON object with this exact top-level shape:

```json
{{"findings": []}}
```

Each finding must use these fields:
- blocking: boolean
- severity: critical, high, medium, low, or info
- file_path: string or null
- line: integer or null
- summary: short string
- recommendation: short string
- review_task: "{review_task}"

Changed-file plan:
```json
{json.dumps(review_plan.to_dict(), indent=2, sort_keys=True)}
```

Pull request diff:
```diff
{diff_text or ""}
```
"""


def _loads_review_json(raw: str) -> dict[str, Any] | list[Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (list, dict)):
        for key in ("result", "response", "text", "content"):
            if isinstance(parsed, dict) and isinstance(parsed.get(key), str):
                nested = _loads_review_json(parsed[key])
                if nested is not None:
                    return nested
        return parsed
    return None


def _configured_blocking_severities(config: dict[str, Any]) -> set[str]:
    review_config = config.get("review", {}) if isinstance(config.get("review", {}), dict) else {}
    raw_severities = review_config.get("blocking_severities", ["critical", "high", "medium"])
    if not isinstance(raw_severities, list):
        return {"critical", "high", "medium"}
    severities = {_normalized_severity(value) for value in raw_severities}
    return {severity for severity in severities if severity != "info"} or {"critical", "high", "medium"}


def _default_review_task(review_plan: ReviewPlan) -> str:
    if "final_blocking_review" in review_plan.review_tasks:
        return "final_blocking_review"
    if review_plan.review_tasks:
        return review_plan.review_tasks[-1]
    return "code_review"


def _ensure_object_list(values: list[Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ReviewRunError(f"finding at index {index} must be an object")
        findings.append(value)
    return findings


def _normalized_severity(value: Any) -> str:
    severity = str(value or "medium").strip().lower()
    return severity if severity in {"critical", "high", "medium", "low", "info"} else "medium"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_line(value: Any) -> int | None:
    try:
        line = int(value)
    except (TypeError, ValueError):
        return None
    return line if line > 0 else None
