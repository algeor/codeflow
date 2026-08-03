from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class WorkflowLoopError(RuntimeError):
    """Raised when the workflow loop cannot safely continue."""


@dataclass(frozen=True)
class ImplementationRequest:
    fix_iteration: int
    review_result_file: str | None


@dataclass(frozen=True)
class ReviewRequest:
    fix_iteration: int
    implementation_result: dict[str, Any]
    output_file: Path


@dataclass(frozen=True)
class GateRequest:
    fix_iteration: int
    review_result: dict[str, Any]
    review_result_file: str


@dataclass(frozen=True)
class WorkflowLoopIteration:
    fix_iteration: int
    implementation: dict[str, Any]
    review: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    review_result_file: str | None = None
    status: str = "completed"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "fix_iteration": self.fix_iteration,
            "status": self.status,
            "implementation": self.implementation,
        }
        if self.review is not None:
            data["review"] = self.review
        if self.gate is not None:
            data["gate"] = self.gate
        if self.review_result_file:
            data["review_result_file"] = self.review_result_file
        if self.reason:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True)
class WorkflowLoopResult:
    change_name: str
    pr_number: int | None
    status: str
    max_iterations: int
    iterations: list[WorkflowLoopIteration]
    final_decision: dict[str, Any] | None = None
    reason: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "change_name": self.change_name,
            "pull_request_number": self.pr_number,
            "status": self.status,
            "max_iterations": self.max_iterations,
            "iterations": [iteration.to_dict() for iteration in self.iterations],
            "iterations_count": len(self.iterations),
            "token_usage": _loop_token_usage(self.iterations),
            "artifacts": self.artifacts,
        }
        if self.final_decision is not None:
            data["final_decision"] = self.final_decision
        if self.reason:
            data["reason"] = self.reason
        return data


ImplementationRunner = Callable[[ImplementationRequest], dict[str, Any]]
ReviewRunner = Callable[[ReviewRequest], dict[str, Any]]
GateRunner = Callable[[GateRequest], dict[str, Any]]


def run_workflow_loop(
    *,
    change_name: str,
    pr_number: int | None,
    max_iterations: int,
    review_results_dir: Path,
    implementation_runner: ImplementationRunner,
    review_runner: ReviewRunner,
    gate_runner: GateRunner,
) -> WorkflowLoopResult:
    safe_max_iterations = max(1, max_iterations)
    review_results_dir.mkdir(parents=True, exist_ok=True)
    iterations: list[WorkflowLoopIteration] = []

    fix_iteration = 0
    review_result_file: str | None = None
    while True:
        implementation = implementation_runner(
            ImplementationRequest(fix_iteration=fix_iteration, review_result_file=review_result_file)
        )
        if not _implementation_succeeded(implementation):
            reason = _implementation_failure_reason(implementation)
            iterations.append(
                WorkflowLoopIteration(
                    fix_iteration=fix_iteration,
                    implementation=implementation,
                    status="implementation_blocked",
                    reason=reason,
                )
            )
            return WorkflowLoopResult(
                change_name=change_name,
                pr_number=pr_number,
                status="blocked",
                max_iterations=safe_max_iterations,
                iterations=iterations,
                reason=reason,
                artifacts={"review_results_dir": str(review_results_dir)},
            )

        output_file = review_results_dir / f"review-iteration-{fix_iteration}.json"
        review = review_runner(
            ReviewRequest(
                fix_iteration=fix_iteration,
                implementation_result=implementation,
                output_file=output_file,
            )
        )
        _write_json_file(output_file, review)

        gate = gate_runner(
            GateRequest(
                fix_iteration=fix_iteration,
                review_result=review,
                review_result_file=str(output_file),
            )
        )
        iterations.append(
            WorkflowLoopIteration(
                fix_iteration=fix_iteration,
                implementation=implementation,
                review=review,
                gate=gate,
                review_result_file=str(output_file),
                status=str(gate.get("status") or "unknown"),
            )
        )

        status = str(gate.get("status") or "")
        if status == "ready":
            return WorkflowLoopResult(
                change_name=change_name,
                pr_number=pr_number,
                status="ready",
                max_iterations=safe_max_iterations,
                iterations=iterations,
                final_decision=gate,
                artifacts={"review_results_dir": str(review_results_dir)},
            )
        if status in {"failed", "escalate"}:
            return WorkflowLoopResult(
                change_name=change_name,
                pr_number=pr_number,
                status=status,
                max_iterations=safe_max_iterations,
                iterations=iterations,
                final_decision=gate,
                reason=str(gate.get("reason") or status),
                artifacts={"review_results_dir": str(review_results_dir)},
            )
        if status != "fix_required":
            reason = f"review gate returned unsupported status: {status or 'missing'}"
            return WorkflowLoopResult(
                change_name=change_name,
                pr_number=pr_number,
                status="failed",
                max_iterations=safe_max_iterations,
                iterations=iterations,
                final_decision=gate,
                reason=reason,
                artifacts={"review_results_dir": str(review_results_dir)},
            )

        next_iteration = _safe_int(gate.get("iteration"), default=fix_iteration + 1)
        if next_iteration <= fix_iteration:
            reason = "review gate did not advance the fix iteration"
            return WorkflowLoopResult(
                change_name=change_name,
                pr_number=pr_number,
                status="failed",
                max_iterations=safe_max_iterations,
                iterations=iterations,
                final_decision=gate,
                reason=reason,
                artifacts={"review_results_dir": str(review_results_dir)},
            )

        review_result_file = str(output_file)
        fix_iteration = next_iteration


def _implementation_succeeded(result: dict[str, Any]) -> bool:
    return result.get("status") == "completed" and bool(result.get("safe_to_commit"))


def _implementation_failure_reason(result: dict[str, Any]) -> str:
    approval = result.get("approval")
    if isinstance(approval, dict) and approval.get("blocking_reason"):
        return str(approval["blocking_reason"])
    return str(result.get("reason") or result.get("stopped_at") or "implementation did not complete safely")


def _loop_token_usage(iterations: list[WorkflowLoopIteration]) -> dict[str, Any]:
    total = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    by_iteration: dict[str, dict[str, Any]] = {}
    for iteration in iterations:
        usage = iteration.implementation.get("token_usage")
        if not isinstance(usage, dict):
            continue
        iteration_usage = usage.get("total")
        if not isinstance(iteration_usage, dict):
            continue
        normalized = {key: int(value) for key, value in iteration_usage.items() if key in total and isinstance(value, int)}
        if not normalized:
            continue
        by_iteration[str(iteration.fix_iteration)] = normalized
        for key, value in normalized.items():
            total[key] += value
    return {
        "total": {key: value for key, value in total.items() if value},
        "implementation_iterations": by_iteration,
    }


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
