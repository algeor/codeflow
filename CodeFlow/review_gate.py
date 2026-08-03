from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReviewLoopDecision:
    status: str
    next_action: str
    reason: str
    iteration: int
    max_iterations: int
    blocking_findings_count: int
    blocking_findings: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "next_action": self.next_action,
            "reason": self.reason,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "blocking_findings_count": self.blocking_findings_count,
            "blocking_findings": self.blocking_findings,
        }


def decide_review_loop(
    review_result: Mapping[str, Any],
    *,
    iteration: int,
    max_iterations: int,
) -> ReviewLoopDecision:
    safe_iteration = max(0, iteration)
    safe_max_iterations = max(1, max_iterations)
    status = str(review_result.get("status") or "")
    blocking_findings = _blocking_findings(review_result)
    blocking_count = len(blocking_findings)

    if status == "failed":
        return ReviewLoopDecision(
            status="failed",
            next_action="stop",
            reason="review run failed",
            iteration=safe_iteration,
            max_iterations=safe_max_iterations,
            blocking_findings_count=blocking_count,
            blocking_findings=blocking_findings,
        )

    if blocking_count == 0:
        return ReviewLoopDecision(
            status="ready",
            next_action="ready_for_finalization",
            reason="zero blocking findings",
            iteration=safe_iteration,
            max_iterations=safe_max_iterations,
            blocking_findings_count=0,
            blocking_findings=[],
        )

    if safe_iteration >= safe_max_iterations:
        return ReviewLoopDecision(
            status="escalate",
            next_action="stop",
            reason="maximum fix iterations reached with blocking findings still open",
            iteration=safe_iteration,
            max_iterations=safe_max_iterations,
            blocking_findings_count=blocking_count,
            blocking_findings=blocking_findings,
        )

    return ReviewLoopDecision(
        status="fix_required",
        next_action="run_fix_iteration",
        reason="blocking findings remain",
        iteration=safe_iteration + 1,
        max_iterations=safe_max_iterations,
        blocking_findings_count=blocking_count,
        blocking_findings=blocking_findings,
    )


def configured_max_iterations(config: Mapping[str, Any] | None, *, default: int = 8) -> int:
    workflow_config = config.get("workflow", {}) if isinstance(config, Mapping) else {}
    if not isinstance(workflow_config, Mapping):
        return default
    try:
        value = int(workflow_config.get("max_iterations", default))
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _blocking_findings(review_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = review_result.get("findings", [])
    if not isinstance(findings, list):
        return []
    return [dict(finding) for finding in findings if isinstance(finding, Mapping) and bool(finding.get("blocking"))]
