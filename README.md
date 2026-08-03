# CodeFlow

CodeFlow is a local automation scaffold for running an approved pull-request workflow with AI coding agents. It is designed to keep the human approval boundary in GitHub while using local Claude or Codex CLI processes for proposal, implementation, validation, and review phases.

## What It Does

- Creates proposal and implementation workflow phases around a single GitHub PR.
- Waits for explicit human approval before code generation begins.
- Runs implementation work in small logical steps.
- Runs validation before each workflow-created commit.
- Tracks workflow state, events, tests, reviews, findings, budgets, and agent calls in Postgres.
- Supports Claude CLI and Codex CLI through a shared adapter seam.
- Routes tasks to cheap, balanced, or strong model profiles based on risk and cost policy.

## Current Status

This repository is an MVP scaffold. The core CLI, model router, local prompt skills, migration skeleton, and a dry-run workflow runner are present. The full production runner, PR lifecycle automation, durable Postgres execution, and real apply mode are still being built.

Implemented commands:

```bash
python -m CodeFlow doctor --json
python -m CodeFlow propose <change-name> "request text" --json
python -m CodeFlow propose <change-name> "request text" --open-pr --json
python -m CodeFlow status <change-name> --pr-number <number> --json
python -m CodeFlow run <change-name> --agent claude --dry-run --json
```

Scaffolded commands:

```bash
python -m CodeFlow resume <change-name>
python -m CodeFlow abort <change-name>
```

## Workflow Shape

The implementation workflow is intentionally split into explicit phases:

```text
implement-init -> implement-code-creation -> implement-validation -> commit
```

Each phase runs as a subagent-style task and returns JSON. The orchestrator starts the next phase only after the previous phase succeeds.

## Local Setup

1. Install Python 3.11 or newer.
2. Install project dependencies from `pyproject.toml`.
3. Configure `.env` from `.env.example`.
4. Authenticate GitHub CLI or provide `GH_TOKEN` for local automation.
5. Provide `CODEFLOW_DATABASE_URL` for Postgres-backed workflow state.

Example doctor command:

```bash
CODEFLOW_DATABASE_URL=postgresql://localhost/CodeFlow python -m CodeFlow doctor --json
```

## Model Routing

Model profiles are configured in `.CodeFlow/config.yaml`:

- `cheap` for low-risk support tasks such as status, summarization, RAG indexing, and lightweight validation.
- `balanced` for normal planning and implementation work.
- `strong` for high-risk review tasks such as security, test quality, and final blocking review.

The router can use either Claude CLI or Codex CLI, depending on configured availability and user preference.

## Validation

Run the local test suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -v
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q CodeFlow tests
```

The fake workflow harness can be tested directly with:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_workflow_runner -v
```

## Project Notes

Design decisions and workflow notes live in `automation-workflow-notes.md`. That file is the working design log for the local approved-PR automation system.

## Roadmap

- Wire real apply mode for Claude and Codex agents.
- Persist workflow runs and phase outputs in Postgres.
- Update existing GitHub PRs after proposal creation.
- Commit after each successfully validated logical step.
- Run role-based code, security, and test-quality reviews.
- Track token usage and budget consumption per phase and workflow.
- Add RAG indexing so agents do not reread the full repository every iteration.
