from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model_router import route_task
from .review_routing import ReviewPlan


class ReviewRunError(RuntimeError):
    """Raised when review execution input cannot be normalized."""


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_task": self.review_task,
            "provider_cli": self.provider_cli,
            "model_tier": self.model_tier,
            "model_id": self.model_id,
            "routing_reason": self.routing_reason,
            "status": self.status,
            "findings_count": self.findings_count,
            "blocking_findings_count": self.blocking_findings_count,
        }


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
) -> ReviewRunResult:
    findings = normalize_findings(
        raw_findings or [],
        default_review_task=_default_review_task(review_plan),
        blocking_severities=_configured_blocking_severities(config),
    )
    task_runs = [_task_run(review_task, config, pinned_cli, findings) for review_task in review_plan.review_tasks]
    return ReviewRunResult(
        change_name=change_name,
        pull_request_number=pull_request_number,
        review_plan=review_plan,
        task_runs=task_runs,
        findings=findings,
        diff_source=diff_source,
        diff_text=diff_text,
    )


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
    config: dict[str, Any],
    pinned_cli: str | None,
    findings: list[ReviewFinding],
) -> ReviewTaskRun:
    route = route_task(review_task, config, pinned_cli=pinned_cli)
    task_findings = [finding for finding in findings if finding.review_task == review_task]
    blocking_findings = [finding for finding in task_findings if finding.blocking]
    return ReviewTaskRun(
        review_task=review_task,
        provider_cli=route.provider_cli,
        model_tier=route.model_tier,
        model_id=route.model_id,
        routing_reason=route.reason,
        status="findings_opened" if blocking_findings else "passed",
        findings_count=len(task_findings),
        blocking_findings_count=len(blocking_findings),
    )


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
