from __future__ import annotations

import unittest

from CodeFlow.db.review_persistence import persist_review_result
from CodeFlow.review_routing import detect_review_plan
from CodeFlow.review_runner import run_review_plan


class FakeQueryResult:
    def __init__(self, row_id: str) -> None:
        self.row_id = row_id

    def fetchone(self) -> tuple[str]:
        return (self.row_id,)


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...]) -> FakeQueryResult:
        self.calls.append((sql, params))
        return FakeQueryResult(f"row-{len(self.calls)}")


class ReviewPersistenceTests(unittest.TestCase):
    def test_persist_review_result_inserts_runs_and_upserts_findings(self) -> None:
        config = {"agent": {"default_cli": "claude"}, "review": {"roles": {"docs": {"patterns": ["**/*.md"]}}}}
        plan = detect_review_plan(["README.md"], config)
        review_result = run_review_plan(
            change_name="docs",
            pull_request_number=7,
            review_plan=plan,
            config=config,
            raw_findings=[
                {
                    "severity": "medium",
                    "file_path": "README.md",
                    "line": 12,
                    "summary": "Missing real behavior assertion.",
                    "recommendation": "Add a failing test for the wrong behavior.",
                    "review_task": "code_review",
                }
            ],
        )
        connection = FakeConnection()

        persisted = persist_review_result(
            connection,
            workflow_run_id="00000000-0000-0000-0000-000000000001",
            result=review_result,
            commit_sha="abc123",
        )

        self.assertEqual(persisted.review_run_ids, ["row-1", "row-2"])
        self.assertEqual(persisted.review_finding_ids, ["row-3"])
        self.assertIn("insert into review_runs", connection.calls[0][0])
        self.assertIn("insert into review_findings", connection.calls[2][0])
        self.assertIn("on conflict (workflow_run_id, fingerprint)", connection.calls[2][0])
        finding_params = connection.calls[2][1]
        self.assertEqual(finding_params[0], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(finding_params[1], "row-1")
        self.assertEqual(finding_params[5], "medium")
        self.assertEqual(finding_params[9], "Add a failing test for the wrong behavior.")


if __name__ == "__main__":
    unittest.main()
