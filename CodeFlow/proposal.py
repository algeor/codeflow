from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CHANGE_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProposalError(ValueError):
    """Raised when proposal artifacts cannot be created safely."""


@dataclass(frozen=True)
class ProposalArtifactResult:
    change_name: str
    title: str
    summary: str
    artifacts: dict[str, str]
    open_questions: list[dict[str, Any]]
    risk_areas: list[str]
    expected_review_roles: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_name": self.change_name,
            "title": self.title,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "open_questions": self.open_questions,
            "risk_areas": self.risk_areas,
            "expected_review_roles": self.expected_review_roles,
        }


def create_proposal_artifacts(
    change_name: str,
    request_text: str,
    *,
    root: Path | str = Path("."),
    overwrite: bool = False,
) -> ProposalArtifactResult:
    normalized_change_name = change_name.strip()
    normalized_request = request_text.strip()
    if not _CHANGE_NAME_RE.fullmatch(normalized_change_name):
        raise ProposalError("change_name must be kebab-case using lowercase letters, numbers, and hyphens")
    if not normalized_request:
        raise ProposalError("proposal request text is required")

    root_path = Path(root)
    change_dir = root_path / ".CodeFlow" / "changes" / normalized_change_name
    paths = {
        "proposal": change_dir / "proposal.md",
        "design": change_dir / "design.md",
        "tasks": change_dir / "tasks.md",
        "questions": change_dir / "questions.md",
        "proposal_json": change_dir / "proposal.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        existing_list = ", ".join(str(path) for path in existing)
        raise ProposalError(f"proposal artifacts already exist: {existing_list}")

    title = _title_from_change_name(normalized_change_name)
    summary = f"Plan implementation work for: {normalized_request}"
    artifacts = {
        "proposal": _relative_artifact_path(normalized_change_name, "proposal.md"),
        "design": _relative_artifact_path(normalized_change_name, "design.md"),
        "tasks": _relative_artifact_path(normalized_change_name, "tasks.md"),
        "questions": _relative_artifact_path(normalized_change_name, "questions.md"),
    }
    result = ProposalArtifactResult(
        change_name=normalized_change_name,
        title=title,
        summary=summary,
        artifacts=artifacts,
        open_questions=[],
        risk_areas=["docs", "tests"],
        expected_review_roles=["docs", "test_quality"],
    )

    change_dir.mkdir(parents=True, exist_ok=True)
    paths["proposal"].write_text(_proposal_markdown(title, normalized_request), encoding="utf-8")
    paths["design"].write_text(_design_markdown(normalized_request), encoding="utf-8")
    paths["tasks"].write_text(_tasks_markdown(), encoding="utf-8")
    paths["questions"].write_text("No open questions.\n", encoding="utf-8")
    paths["proposal_json"].write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _relative_artifact_path(change_name: str, filename: str) -> str:
    return f".CodeFlow/changes/{change_name}/{filename}"


def _title_from_change_name(change_name: str) -> str:
    return " ".join(part.capitalize() for part in change_name.split("-"))


def _proposal_markdown(title: str, request_text: str) -> str:
    return f"""# {title}

## What

{request_text}

## Why

Document the intended change before implementation starts so reviewers can approve the direction in GitHub.

## Non-Goals

- Do not implement code in the proposal phase.
- Do not bypass required human approval.

## User Impact

The implementation phase will use this approved plan to make a focused, reviewable change.
"""


def _design_markdown(request_text: str) -> str:
    return f"""# Design

## Request

{request_text}

## Approach

- Keep the change scoped to the approved request.
- Follow existing repository instructions and local style.
- Update tests or validation commands when behavior changes.

## Risks

- Documentation can become stale if it describes unsupported behavior.
- Tests can be weak if they only prove files exist instead of validating meaningful behavior.
"""


def _tasks_markdown() -> str:
    return """# Tasks

1. Inspect current repository instructions and documentation structure.
2. Implement the approved documentation change in one meaningful step.
3. Run targeted validation for changed files.
4. Run test-quality review when tests are added or changed.
5. Commit only after validation passes.
"""
