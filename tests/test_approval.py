from __future__ import annotations

import unittest

from CodeFlow.approval import evaluate_pull_request_approval


class ApprovalTests(unittest.TestCase):
    def test_allowed_human_approval_allows_code_generation(self) -> None:
        decision = evaluate_pull_request_approval(
            {
                "author": {"login": "algeor"},
                "reviews": [{"author": {"login": "reviewer-one"}, "state": "APPROVED"}],
            },
            ["reviewer-one"],
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.approved_by, ["reviewer-one"])
        self.assertIsNone(decision.blocking_reason)

    def test_self_approval_is_rejected(self) -> None:
        decision = evaluate_pull_request_approval(
            {
                "author": {"login": "algeor"},
                "reviews": [{"author": {"login": "algeor"}, "state": "APPROVED"}],
            },
            ["algeor"],
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.blocking_reason, "pull request author cannot approve their own PR")

    def test_changes_requested_by_allowed_reviewer_blocks(self) -> None:
        decision = evaluate_pull_request_approval(
            {
                "author": {"login": "algeor"},
                "reviews": [
                    {"author": {"login": "reviewer-one"}, "state": "APPROVED"},
                    {"author": {"login": "reviewer-two"}, "state": "CHANGES_REQUESTED"},
                ],
            },
            ["reviewer-one", "reviewer-two"],
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.approved_by, ["reviewer-one"])
        self.assertEqual(decision.changes_requested_by, ["reviewer-two"])
        self.assertEqual(decision.blocking_reason, "changes requested by allowed reviewer")

    def test_empty_allowed_reviewers_blocks(self) -> None:
        decision = evaluate_pull_request_approval(
            {
                "author": {"login": "algeor"},
                "reviews": [{"author": {"login": "reviewer-one"}, "state": "APPROVED"}],
            },
            [],
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.blocking_reason, "no allowed human reviewers are configured")

    def test_latest_review_per_reviewer_wins(self) -> None:
        decision = evaluate_pull_request_approval(
            {
                "author": {"login": "algeor"},
                "reviews": [
                    {"author": {"login": "reviewer-one"}, "state": "CHANGES_REQUESTED"},
                    {"author": {"login": "reviewer-one"}, "state": "APPROVED"},
                ],
            },
            ["reviewer-one"],
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.approved_by, ["reviewer-one"])


if __name__ == "__main__":
    unittest.main()
