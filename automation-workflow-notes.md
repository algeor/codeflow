# Automation Workflow Notes

This file is the project-owned memory for CodeFlow workflow decisions and gaps. Do not rely on chat history for these items.

## MVP v1 Status

- Proposal artifacts and plan PR creation are wired.
- Implementation is gated by an approved open GitHub PR from an allowed human reviewer.
- Implementation phases run as `implement-init -> implement-code-creation -> implement-validation`.
- Validated implementation can commit and push to the PR branch using `GH_TOKEN` or `GITHUB_TOKEN`.
- Review role detection and fakeable review execution are wired.
- `workflow-loop` now runs implementation, review, gate, and fix iterations until ready, failed, or escalated.
- Claude CLI and Codex CLI are both exposed as implementation choices.
- Review tasks can route to `auto`, Claude, or Codex.

## Current MVP Loop

```text
approved PR -> implementation -> commit/push -> review-run -> review-gate
  -> ready: stop
  -> fix_required: feed blocking findings into the next implementation run
  -> failed/escalate: stop for a human
```

## Remaining Gaps

- Persist full workflow loop lifecycle in Postgres, not only review runs/findings.
- Enforce max workflow budget from token usage and configured minor currency units.
- Store token usage per workflow, iteration, phase, review task, and agent invocation.
- Add real RAG indexing/retrieval so agents reuse relevant context instead of rereading broad repo context.
- Add PR comment/question flow for design questions and change-request follow-up.
- Add merge/finalization automation after the loop reaches `ready`.
- Add resume/abort support for interrupted loops.
- Add real e2e coverage against GitHub plus Claude/Codex CLIs behind a safe test repo.
- Persist test command results and test-integrity review outcomes.
- Improve review-result de-duplication across fix iterations.
