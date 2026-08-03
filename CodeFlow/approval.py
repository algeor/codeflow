from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approved_by: list[str]
    changes_requested_by: list[str]
    blocking_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "approved_by": self.approved_by,
            "changes_requested_by": self.changes_requested_by,
            "blocking_reason": self.blocking_reason,
        }


def evaluate_pull_request_approval(pr_data: dict[str, Any], allowed_reviewers: Iterable[str]) -> ApprovalDecision:
    allowed = {reviewer.lower() for reviewer in allowed_reviewers if reviewer}
    if not allowed:
        return ApprovalDecision(False, [], [], "no allowed human reviewers are configured")

    author = _login(pr_data.get("author")).lower()
    latest_reviews = _latest_reviews_by_author(_reviews(pr_data))
    changes_requested_by: list[str] = []
    approved_by: list[str] = []
    ignored_self_approvals: list[str] = []

    for reviewer, review in sorted(latest_reviews.items()):
        state = str(review.get("state") or "").upper()
        if reviewer not in allowed:
            continue
        if reviewer == author:
            if state == "APPROVED":
                ignored_self_approvals.append(reviewer)
            continue
        if state == "CHANGES_REQUESTED":
            changes_requested_by.append(reviewer)
        elif state == "APPROVED":
            approved_by.append(reviewer)

    if changes_requested_by:
        return ApprovalDecision(False, approved_by, changes_requested_by, "changes requested by allowed reviewer")
    if approved_by:
        return ApprovalDecision(True, approved_by, [], None)
    if ignored_self_approvals:
        return ApprovalDecision(False, [], [], "pull request author cannot approve their own PR")
    return ApprovalDecision(False, [], [], "no approval from an allowed human reviewer")


def _reviews(pr_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_reviews = pr_data.get("latestReviews") or pr_data.get("reviews") or []
    return [review for review in raw_reviews if isinstance(review, dict)]


def _latest_reviews_by_author(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        reviewer = _login(review.get("author")).lower()
        if not reviewer:
            continue
        latest[reviewer] = review
    return latest


def _login(raw_author: Any) -> str:
    if isinstance(raw_author, dict):
        return str(raw_author.get("login") or "")
    return ""
