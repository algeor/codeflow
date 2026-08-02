---
name: implement-init
description: First phase of implementing a Jira ticket. Run in a dedicated subagent before code creation. Configures git identity, reads all repo instructions, gathers task context, maps relevant code/tests, and returns a structured implementation handoff without editing production code.
---

# Implement: Init Phase

Run this phase in a dedicated subagent.

Goal: prepare implementation context for the code-creation phase.

Do not write production code in this phase.
Only allowed repository mutation: configuring local git identity.

## 1. Configure Git Identity First

Before any exploration, configure git identity.

If `GIT_USERNAME` or `GIT_EMAIL` is missing or empty, stop and return:

```json
{"status":"escalate","reason":"GIT_USERNAME or GIT_EMAIL is not set"}
```

Otherwise run:

```bash
git config user.name "${GIT_USERNAME}"
git config user.email "${GIT_EMAIL}"
```

Then verify:

```bash
git config user.name
git config user.email
```

## 2. Inspect Worktree State

Check for existing user changes before touching anything else:

```bash
git status --short
```

Record existing modified, added, deleted, renamed, and untracked files in the final handoff.

Do not revert, overwrite, stage, or commit anything.

## 3. Read All Repository Instructions

Scan the entire repository tree for development instructions and config files.

Use `rg --files` when available.

Look for all files matching names such as:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `CONTRIBUTING.md`
- `.clinerules`
- `.editorconfig`
- `.prettierrc*`
- `.eslintrc*`
- `eslint.config.*`
- `pyproject.toml`
- `package.json`
- `Makefile`
- `justfile`
- `Taskfile.yml`
- `tox.ini`
- `pytest.ini`
- `mypy.ini`
- `ruff.toml`

Read every matching file, wherever it appears in the tree.

If nested instruction files apply to specific subdirectories, record that scope.

## 4. Fetch External References

If the ticket/task text contains URLs, fetch each one:

```bash
curl -sL "<url>"
```

Summarize only the parts relevant to implementation.

If a URL cannot be fetched, record the failure and decide whether the task can proceed.

## 5. Explore Relevant Code

Identify:

- likely implementation files
- related tests
- shared modules or APIs affected
- configuration files involved
- entry points
- existing patterns to follow

Prefer targeted searches with `rg`.

Do not make code changes.

## 6. Identify Validation Commands

Find the commands the validation phase should run.

Include applicable commands for:

- tests
- lint
- typecheck
- formatting check
- build
- targeted tests for changed areas

Also record required environment variables, services, or setup steps if discovered.

## 7. Assess Complexity

Escalate if the task is:

- too ambiguous to implement safely
- broader than a single ticket
- blocked by missing credentials, services, or unavailable references
- likely to require architectural decisions
- risky because relevant files are heavily modified by the user

If escalating, the final line must be exactly:

```json
{"status":"escalate","reason":"<brief explanation>"}
```

## 8. Final Handoff Format

If the task can proceed, end with a concise handoff.

The final line must be valid JSON using this shape:

```json
{
  "status": "ready",
  "git_identity_configured": true,
  "repo_instructions_read": [],
  "repo_instruction_summary": [],
  "existing_worktree_changes": [],
  "external_references": [],
  "relevant_files": [],
  "relevant_tests": [],
  "validation_commands": [],
  "coding_standards": [],
  "implementation_plan": [],
  "risks": []
}
```

Keep values concise but specific enough for the code-creation subagent to start without rediscovering basics.

