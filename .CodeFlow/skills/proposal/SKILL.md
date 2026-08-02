# Proposal Skill

## Purpose

Create a PR-ready change plan before implementation starts.

This skill writes proposal artifacts only. It does not implement product code.

## Inputs

- User request or change description.
- Repository context retrieved by the runner.
- Existing project conventions and relevant files.
- Optional human answers from PR comments.

## Required Outputs

Write artifacts under `.CodeFlow/changes/<change_name>/`:

- `proposal.md`: what, why, non-goals, user impact.
- `design.md`: architecture, tradeoffs, data/control flow, risk areas.
- `tasks.md`: ordered implementation steps with test expectations.
- `questions.md`: specific human questions, or `No open questions.`
- `proposal.json`: machine-readable summary.

`proposal.json` schema:

```json
{
  "change_name": "kebab-case-name",
  "title": "short PR title",
  "summary": "one-paragraph summary",
  "artifacts": {
    "proposal": ".CodeFlow/changes/<change_name>/proposal.md",
    "design": ".CodeFlow/changes/<change_name>/design.md",
    "tasks": ".CodeFlow/changes/<change_name>/tasks.md",
    "questions": ".CodeFlow/changes/<change_name>/questions.md"
  },
  "open_questions": [
    {
      "id": "q1",
      "question": "specific question for humans",
      "why_it_matters": "decision impact",
      "blocks_implementation": true
    }
  ],
  "risk_areas": ["security", "tests"],
  "expected_review_roles": ["backend", "test_quality"]
}
```

## Rules

- Ask targeted questions when requirements or design choices are ambiguous.
- Do not hide assumptions. Put them in `design.md`.
- Include test expectations for every implementation task.
- Mark questions as blocking only when implementation should not proceed without an answer.
- Do not include archival instructions.

