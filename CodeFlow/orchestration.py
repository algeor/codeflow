from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class WorkflowPhase:
    phase_id: str
    skill_name: str
    task_type: str
    success_statuses: frozenset[str]


IMPLEMENTATION_PHASES = (
    WorkflowPhase("init", "implement-init", "implement_init", frozenset({"ready"})),
    WorkflowPhase("code_creation", "implement-code-creation", "implement_code_creation", frozenset({"completed"})),
    WorkflowPhase("validation", "implement-validation", "implement_validation", frozenset({"passed"})),
)

PHASES_BY_ID = {phase.phase_id: phase for phase in IMPLEMENTATION_PHASES}

SUMMARY_FIELDS = {
    "init": (
        "status",
        "repo_instructions_read",
        "existing_worktree_changes",
        "relevant_files",
        "relevant_tests",
        "validation_commands",
        "implementation_plan",
        "risks",
        "reason",
    ),
    "code_creation": (
        "status",
        "logical_step",
        "summary",
        "files_changed",
        "tests_changed",
        "tests_to_run",
        "review_roles_suggested",
        "blocking_questions",
        "reason",
    ),
    "validation": (
        "status",
        "logical_step",
        "safe_to_commit",
        "commands_run",
        "test_integrity",
        "blocking_failures",
        "warnings",
        "reason",
    ),
}


def phase_succeeded(phase_id: str, status: str) -> bool:
    phase = PHASES_BY_ID[phase_id]
    return status in phase.success_statuses


def next_implementation_phase(statuses: Mapping[str, str]) -> WorkflowPhase | None:
    """Return the next phase only when all prior phases succeeded."""
    for phase in IMPLEMENTATION_PHASES:
        status = statuses.get(phase.phase_id)
        if status is None:
            return phase
        if not phase_succeeded(phase.phase_id, status):
            return None
    return None


def concise_phase_summary(phase_id: str, result: Mapping[str, Any], *, max_chars: int = 1200) -> str:
    fields = SUMMARY_FIELDS[phase_id]
    compact = {field: result[field] for field in fields if field in result}
    summary = json.dumps(compact, sort_keys=True, separators=(",", ":"))
    if len(summary) <= max_chars:
        return summary
    return summary[: max(0, max_chars - 3)] + "..."
