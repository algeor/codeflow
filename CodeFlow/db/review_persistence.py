from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from CodeFlow.review_runner import ReviewFinding, ReviewRunResult, ReviewTaskRun


@dataclass(frozen=True)
class ReviewPersistenceResult:
    review_run_ids: list[str]
    review_finding_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_run_ids": self.review_run_ids,
            "review_finding_ids": self.review_finding_ids,
            "review_runs_count": len(self.review_run_ids),
            "review_findings_count": len(self.review_finding_ids),
        }


def persist_review_result(
    connection: Any,
    *,
    workflow_run_id: str,
    result: ReviewRunResult,
    commit_sha: str | None = None,
) -> ReviewPersistenceResult:
    review_run_ids_by_task: dict[str, str] = {}
    for task_run in result.task_runs:
        review_run_ids_by_task[task_run.review_task] = _insert_review_run(
            connection,
            workflow_run_id=workflow_run_id,
            task_run=task_run,
            result=result,
            commit_sha=commit_sha,
        )

    finding_ids = [
        _upsert_review_finding(
            connection,
            workflow_run_id=workflow_run_id,
            review_run_id=review_run_ids_by_task.get(finding.review_task),
            finding=finding,
            commit_sha=commit_sha,
        )
        for finding in result.findings
    ]
    return ReviewPersistenceResult(review_run_ids=list(review_run_ids_by_task.values()), review_finding_ids=finding_ids)


def _insert_review_run(
    connection: Any,
    *,
    workflow_run_id: str,
    task_run: ReviewTaskRun,
    result: ReviewRunResult,
    commit_sha: str | None,
) -> str:
    row = connection.execute(
        """
        insert into review_runs (
          workflow_run_id,
          commit_sha,
          reviewer_role,
          source,
          model,
          scope,
          status,
          files_reviewed,
          findings_count,
          blocking_findings_count,
          prompt_path,
          output_path,
          duration_ms,
          completed_at,
          metadata
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s::jsonb)
        returning id
        """,
        (
            workflow_run_id,
            commit_sha,
            task_run.review_task,
            "local_agent",
            task_run.model_id,
            "changed_files",
            task_run.status,
            result.review_plan.changed_files,
            task_run.findings_count,
            task_run.blocking_findings_count,
            task_run.prompt_path,
            task_run.output_path,
            task_run.duration_ms,
            json.dumps(
                {
                    "provider_cli": task_run.provider_cli,
                    "model_tier": task_run.model_tier,
                    "routing_reason": task_run.routing_reason,
                    "review_backend": result.review_backend,
                    "diff_source": result.diff_source,
                    "reason": task_run.reason,
                },
                sort_keys=True,
            ),
        ),
    ).fetchone()
    return str(row[0])


def _upsert_review_finding(
    connection: Any,
    *,
    workflow_run_id: str,
    review_run_id: str | None,
    finding: ReviewFinding,
    commit_sha: str | None,
) -> str:
    fingerprint = _finding_fingerprint(finding)
    row = connection.execute(
        """
        insert into review_findings (
          workflow_run_id,
          review_run_id,
          source,
          reviewer_role,
          category,
          severity,
          blocking,
          status,
          title,
          explanation,
          suggested_fix,
          file_path,
          line_start,
          line_end,
          fingerprint,
          introduced_by_commit_sha,
          metadata
        ) values (%s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        on conflict (workflow_run_id, fingerprint) do update set
          review_run_id = excluded.review_run_id,
          severity = excluded.severity,
          blocking = excluded.blocking,
          title = excluded.title,
          explanation = excluded.explanation,
          suggested_fix = excluded.suggested_fix,
          file_path = excluded.file_path,
          line_start = excluded.line_start,
          line_end = excluded.line_end,
          metadata = excluded.metadata,
          updated_at = now()
        returning id
        """,
        (
            workflow_run_id,
            review_run_id,
            "local_agent",
            finding.review_task,
            finding.review_task,
            finding.severity,
            finding.blocking,
            finding.summary,
            finding.summary,
            finding.recommendation or None,
            finding.file_path,
            finding.line,
            finding.line,
            fingerprint,
            commit_sha,
            json.dumps({"review_task": finding.review_task}, sort_keys=True),
        ),
    ).fetchone()
    return str(row[0])


def _finding_fingerprint(finding: ReviewFinding) -> str:
    raw = "|".join(
        [
            finding.review_task,
            finding.file_path or "",
            str(finding.line or ""),
            finding.severity,
            finding.summary,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
