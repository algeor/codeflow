---
name: implement-code-creation
description: Code creation phase for CodeFlow implementation workflows. Use in a dedicated subagent after implement-init succeeds and before implement-validation runs to edit one logical implementation step, update real tests, and return a concise JSON handoff without committing or pushing.
---

# Implement: Code Creation Phase

Run this phase in a dedicated subagent after `implement-init` returns `status: ready`.

Goal: implement exactly one approved logical step and hand it to `implement-validation`.

## Inputs

Use runner-provided context first:

- `implement-init` handoff JSON
- approved proposal, design, specs, and tasks
- current logical step selected by the runner
- retrieved RAG context, when supplied
- open blocking review findings, when fixing
- prior phase summaries from the orchestrator

Do not rediscover broad repo context unless an input is missing or stale.

## Work Rules

- Open full current files before editing. Do not edit from RAG snippets alone.
- Keep the step small enough for one meaningful commit.
- Add or update real tests for changed behavior.
- Do not create weak, fake, quota-filling, or always-passing tests.
- Do not run broad validation unless needed to choose implementation details.
- Do not run `git add`, `git commit`, or `git push`.
- Do not stage files, create commits, create branches, push branches, or open PRs.
- If blocked by ambiguity, stop and return `status: blocked` with concise questions.

## Expected Work

1. Read only the files needed for the selected logical step.
2. Implement the code and test changes for that step.
3. Run only fast, local checks needed while editing, if useful.
4. Record the tests and validation commands that `implement-validation` should run.
5. Return a concise JSON result for the orchestrator.

## Final Output

The final line must be valid JSON using this shape:

```json
{
  "status": "completed",
  "change_name": "kebab-case-name",
  "logical_step": "short-step-name",
  "summary": "what changed and why",
  "files_changed": ["path/to/file.py"],
  "tests_changed": ["tests/test_example.py"],
  "tests_to_run": ["pytest tests/test_example.py"],
  "validation_notes": ["targeted test covers the new branch"],
  "review_roles_suggested": ["backend", "test_quality"],
  "blocking_questions": []
}
```

For blocked work:

```json
{
  "status": "blocked",
  "change_name": "kebab-case-name",
  "logical_step": "short-step-name",
  "summary": "blocked before code changes",
  "files_changed": [],
  "tests_changed": [],
  "tests_to_run": [],
  "validation_notes": [],
  "review_roles_suggested": [],
  "blocking_questions": ["Which API behavior should be preserved?"],
  "reason": "ambiguous requirement"
}
```

Keep values concise but specific enough for `implement-validation` to run without rereading the whole repository.
