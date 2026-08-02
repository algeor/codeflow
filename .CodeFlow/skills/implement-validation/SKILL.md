---
name: implement-validation
description: Lightweight validation gate for CodeFlow implementation workflows. Use before every workflow-created commit after a logical implementation step to select and run minimal relevant checks, verify obvious test integrity, and emit a JSON safe-to-commit decision without staging, committing, or pushing.
---

# Implement: Validation Gate

Run this phase before every workflow-created commit.

Goal: decide whether the just-finished logical step is safe for the runner to commit.

Keep this phase lightweight. Prefer targeted checks over full-suite checks unless repo instructions, risk, or changed files require broader validation.

## Hard Rules

- Do not run `git add`, `git commit`, or `git push`.
- Do not stage files, create commits, create branches, push branches, or open PRs.
- Do not edit production code, tests, docs, or workflow files.
- Do not fix failures in this phase. Report failures so the implementation phase can fix them.
- Do not accept fake, quota-filling, or always-passing tests as satisfying validation.
- Read the full output of every command before deciding pass or fail.
- End with the required JSON object as the final line, with no trailing commentary.

## Inputs

Use runner-provided context first:

- implementation step result JSON
- `implement-init` handoff JSON
- logical step name and summary
- changed files for this step
- tests requested by the implementation phase
- validation commands discovered by `implement-init`
- repo instruction summaries already gathered by `implement-init`

Only rediscover context when an input is missing or stale.

## Lightweight Scope Discovery

1. Confirm the logical step is named and the changed-file list is known.
2. If changed files were not provided, inspect only current change scope with lightweight git commands such as:

```bash
git status --short
git diff --name-only
git diff --cached --name-only
```

3. If any git command reports an error or unexpected state, stop and return `escalate`.
4. Do not inspect unrelated repository areas.

## Command Selection

Choose the smallest defensible validation set:

1. Run tests explicitly listed by the implementation step when present.
2. Add targeted commands from the init handoff's `validation_commands` when they apply to the changed files.
3. Run mandatory pre-commit or pre-push checks only when repo instructions require them before each commit.
4. If no commands are provided, infer a targeted command from local project files, for example:
   - `pytest <relevant tests>` for Python tests
   - `python -m unittest <relevant tests>` for unittest projects
   - `npm test -- <relevant tests>` for JavaScript projects
5. Run a full suite only when:
   - repo instructions require it,
   - shared/core files changed,
   - migrations or public contracts changed,
   - targeted coverage cannot reasonably validate the step,
   - the implementation step is a final pre-ready validation.

If no test infrastructure exists and no relevant command can be inferred, mark validation as passed with a warning and `commands_run` containing a skipped entry.

## Test Integrity Quick Check

When tests were added or changed in this logical step, inspect those test files just enough to catch obvious invalid tests.

Fail validation if tests are clearly:

- assertions-free for behavior that should assert outcomes
- asserting only mocks, constants, or implementation details unrelated to the requested behavior
- written so wrong behavior would still pass
- skipped without a concrete reason
- replacing meaningful assertions with looser checks only to pass

Do not perform a full test-quality review here. The review phase still performs deeper test-quality review.

## Failure Handling

Return `failed` when a command fails, times out, or test integrity is obviously invalid.

Return `escalate` when validation cannot make a safe decision because of:

- missing logical step context
- missing required credentials or services
- ambiguous repo instructions
- dirty state that appears unrelated to the workflow step
- unavailable required tools
- repeated validation infrastructure failures not caused by the implementation

## Final Output

The final line must be valid JSON using this shape:

```json
{
  "status": "passed",
  "logical_step": "short-step-name",
  "commands_run": [
    {
      "command": "pytest tests/test_example.py",
      "status": "passed",
      "scope": "targeted",
      "summary": "2 tests passed"
    }
  ],
  "test_integrity": {
    "checked": true,
    "status": "passed",
    "summary": "changed tests assert the requested behavior"
  },
  "blocking_failures": [],
  "warnings": [],
  "safe_to_commit": true
}
```

For failed validation:

```json
{
  "status": "failed",
  "logical_step": "short-step-name",
  "commands_run": [],
  "test_integrity": {
    "checked": false,
    "status": "not_checked",
    "summary": "validation stopped before test integrity check"
  },
  "blocking_failures": ["pytest tests/test_example.py failed: expected 200, got 500"],
  "warnings": [],
  "safe_to_commit": false
}
```

For escalation:

```json
{
  "status": "escalate",
  "logical_step": "short-step-name",
  "commands_run": [],
  "test_integrity": {
    "checked": false,
    "status": "not_checked",
    "summary": "blocked before validation"
  },
  "blocking_failures": [],
  "warnings": [],
  "safe_to_commit": false,
  "reason": "required database service is unavailable"
}
```
