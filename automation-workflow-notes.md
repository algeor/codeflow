# Local Automated PR Workflow Notes

## Goal

Build a local LangChain/LangGraph workflow that:

- generates a plan with the proposal skill
- submits that plan to a GitHub PR
- waits for PR approval
- continues implementation on the same PR after approval
- generates code in meaningful commits
- runs tests
- identifies what review roles are needed
- runs reviews
- fixes bugs until no blocking findings remain
- pushes the final ready state to GitHub

## Decisions Made

- Product and CLI namespace: `CodeFlow`.
- Config directory: `.CodeFlow/`.
- Main config file: `.CodeFlow/config.yaml`.
- Environment variable prefix: `CODEFLOW_`.
- Prompt skills are named by purpose only: `implement-init`, `implement-code-creation`, `implement-validation`, `proposal`, and `exploration`.
- The runner executes on the local machine.
- LangChain/LangGraph is the intended orchestration layer.
- GitHub is the control plane for approval and PR state.
- The same PR is used for both the plan and implementation.
- The PR starts as plan-only, then receives implementation commits after approval.
- Polling GitHub for approval is the preferred first version.
- GitHub CLI is the first GitHub integration layer.
- Postgres is the durable workflow state store.
- Commit after each successful logical step, after `implement-validation` returns `safe_to_commit: true`.
- Reviews must verify test quality, not only whether tests pass.
- Review runs use local review agents first, with findings stored in Postgres.
- The implementation loop continues until there are zero blocking review findings.
- Code generation starts only after GitHub PR approval from an allowed human reviewer.
- The stop condition is zero blocking findings; non-blocking findings can remain documented.
- Create only the needed project-local prompt skills: implement-init, implement-code-creation, implement-validation, proposal, and exploration.
- The skill names are not the product name.
- Implement the project-local workflow skills as Codex/Claude-style prompt skills first.
- The bot can ask design questions in PR comments, ingest human replies, and edit the design.
- The bot handles requested changes through the same PR comment loop when clarification is needed.
- The workflow supports both Claude CLI and Codex CLI, with user choice per run.
- Tasks should route to the most appropriate available user model to optimize cost and quality.
- Do not rely on chat memory for this design; keep notes in this repo.

## Proposed State Machine

- `PLAN_CREATED`
- `PR_OPENED`
- `WAITING_FOR_APPROVAL`
- `IMPLEMENT_INIT`
- `CODE_CREATION`
- `VALIDATING_STEP`
- `REVIEWING`
- `FIXING`
- `WAITING_FOR_HUMAN_FEEDBACK`
- `REVISING_DESIGN`
- `READY`

## Main Workflow

1. Run the proposal skill to generate the plan.
2. Commit the plan to a branch.
3. Open a GitHub PR labeled as plan-only.
4. Ask design questions in PR comments when human input is needed.
5. Poll for human replies and edit the design/spec/tasks from those replies.
6. Poll GitHub until the PR has required approval.
7. Run `implement-init` in a dedicated subagent before code creation.
8. Continue on the same branch after approval and init handoff.
9. Run `implement-code-creation` in a dedicated subagent for one small, meaningful step.
10. Run `implement-validation` in a dedicated subagent before every workflow-created commit.
11. Commit only when validation returns `safe_to_commit: true`.
12. Detect review roles from changed files and risk areas.
13. Run the needed reviews.
14. Handle GitHub requested changes and local review findings.
15. Ask follow-up questions in PR comments when requested changes are ambiguous.
16. Fix blocking findings.
17. Repeat validation/review/fix until clean.
18. Push final commits and mark the PR ready.

## First Code Validation Run

Orchestrator sequence:

1. `init`: invoke `implement-init` in a subagent.
2. `code_creation`: invoke `implement-code-creation` in a subagent only after init returns `status: ready`.
3. `validation`: invoke `implement-validation` in a subagent only after code creation returns `status: completed`.

Rules:

- Start task N only after task N-1 completes successfully.
- Keep heavy work inside subagents: repo reading, file edits, tests, and git operations.
- The code-creation subagent runs configured pre-commit hooks before exiting successfully, without creating a commit.
- The orchestrator tracks statuses, stores phase outputs, and passes concise summaries between phases.
- The orchestrator does not directly read the full repo, edit files, run tests, commit, or push.

Fake harness test:

- `CodeFlow/workflow_runner.py` provides an offline runner that accepts fake phase JSON responses.
- `tests/test_workflow_runner.py` verifies happy path, init escalation, code-creation block, pre-commit failure, validation failure, and summary passing.
- Run it with `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_workflow_runner -v`.

Claude connection:

- `CliPhaseAgent` adapts any CLI adapter to the same phase-agent interface used by the fake harness.
- `ClaudePhaseAgent` uses `ClaudeCliAdapter`, writes one prompt per phase, routes the task to a configured Claude model, invokes `claude --print --output-format json`, and parses the phase JSON result.
- Tests use a scripted Claude adapter so the workflow contract is verified without spending tokens.
- CLI smoke command: `python -m CodeFlow run <change_name> --agent claude --dry-run --json`.
- Current Claude CLI mode requires `--dry-run`; prompts forbid file edits, shell commands, git config, commits, and pushes until real apply mode is explicitly wired.

## Review Role Detection Ideas

- Backend reviewer for API, services, database, or Python changes.
- Frontend reviewer for UI, CSS, TypeScript, React, or Next.js changes.
- Test reviewer for test harness, fixtures, or coverage-sensitive changes.
- Test quality reviewer to detect fake, quota-filling, overly mocked, or always-passing tests.
- Security reviewer for auth, secrets, permissions, input handling, or network changes.
- Infra reviewer for CI, Docker, Kubernetes, deployment, or config changes.
- Docs reviewer for public docs, plans, or user-facing guidance.

## Postgres State Model Ideas

- `workflow_runs`: one row per automation run.
- `workflow_events`: append-only event log for auditability.
- `workflow_steps`: current and historical state transitions.
- `pr_checks`: cached GitHub approval/check status.
- `review_findings`: findings emitted by review agents.
- `commits`: commit metadata created by the workflow.
- `human_questions`: questions the bot asked in PR comments.
- `human_answers`: human replies captured from PR comments or reviews.
- `design_revisions`: design/spec/task edits made from human feedback.

## Proposed Postgres Schema v1

Keep v1 small and event-driven. Add specialized tables only where querying matters.

Core tables:

- `repositories`: GitHub repo identity and local checkout path.
- `workflow_runs`: one automation run for one repo, branch, change, and PR.
- `workflow_events`: append-only event log for audit, debugging, and resume.
- `workflow_steps`: current and historical state transitions.
- `pull_requests`: cached PR number, URL, branch, labels, review state, and approval state.
- `commits`: commits created by the workflow, including logical step, tests, and summary.

Human feedback tables:

- `pr_comments`: synced PR comments and review-thread comments from GitHub.
- `human_questions`: questions the bot asked humans in PR comments.
- `human_answers`: human replies linked to bot questions when possible.
- `design_revisions`: proposal/design/spec/task edits made from human feedback.

Quality tables:

- `test_runs`: each test command execution, status, duration, and output path.
- `review_runs`: each local review agent execution.
- `review_findings`: structured findings from local reviews or GitHub requested changes.
- `agent_invocations`: each Claude CLI or Codex CLI call, including model, task type, cost, and artifacts.
- `implementation_handoffs`: structured handoffs produced by `implement-init` before code creation.

RAG/index tables:

- `repo_files`: tracked files, hashes, language, size, and last indexed commit.
- `repo_chunks`: searchable chunks with embeddings and full-text content.
- `file_summaries`: compact per-file summaries.
- `symbol_index`: functions, classes, routes, config keys, commands, and public APIs.
- `test_map`: source files or symbols mapped to likely tests.
- `change_context`: summaries of decisions, commits, fixes, and review outcomes for the current PR.

Recommended conventions:

- Use UUID primary keys.
- Use `jsonb` for flexible metadata from tools and agents.
- Use `created_at` and `updated_at` on mutable tables.
- Use `workflow_events` as the source of truth for replay/debugging.
- Use tables like `review_findings` and `test_runs` for fast queries.
- Use `pgvector` only on `repo_chunks` initially.

## Detailed Columns v1

### `workflow_runs`

One row per automation run.

Columns:

- `id uuid primary key`
- `repository_id uuid not null references repositories(id)`
- `change_name text not null`
- `title text not null`
- `description text`
- `status text not null`: `planning`, `waiting_for_feedback`, `waiting_for_approval`, `coding`, `testing`, `reviewing`, `fixing`, `ready`, `failed`, `aborted`
- `current_state text not null`: current state-machine node, for example `WAITING_FOR_APPROVAL`
- `base_branch text not null`
- `work_branch text not null`
- `local_repo_path text not null`
- `worktree_path text`
- `github_pr_number integer`
- `github_pr_url text`
- `plan_path text`
- `proposal_path text`
- `design_path text`
- `tasks_path text`
- `approval_required boolean not null default true`
- `approved_by text[] not null default '{}'`
- `approved_at timestamptz`
- `worker_id text`: local worker currently holding the run lease
- `lease_expires_at timestamptz`: lets another local worker resume if the first dies
- `billing_currency_code text not null default 'USD'`
- `max_workflow_budget_minor_units bigint`: optional workflow budget ceiling in the configured currency's minor units
- `spent_budget_minor_units bigint not null default 0`: current workflow spend in minor units
- `model_config jsonb not null default '{}'`
- `runtime_limits jsonb not null default '{}'`
- `metadata jsonb not null default '{}'`
- `last_error text`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`
- `started_at timestamptz`
- `completed_at timestamptz`

Indexes:

- `unique(repository_id, change_name)`
- `index(repository_id, status)`
- `index(github_pr_number)`
- `index(lease_expires_at)`

### `workflow_events`

Append-only audit log. This is the resume/debug source of truth.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `sequence bigint not null`: monotonic per workflow run
- `event_type text not null`: examples: `state_changed`, `pr_opened`, `human_question_posted`, `human_answer_received`, `tests_passed`, `review_completed`, `finding_fixed`, `commit_created`
- `state_from text`
- `state_to text`
- `actor_type text not null`: `bot`, `human`, `github`, `system`, `review_agent`
- `actor_id text`: GitHub username, worker id, or agent role
- `message text`
- `payload jsonb not null default '{}'`
- `github_comment_id text`
- `github_review_id text`
- `commit_sha text`
- `created_at timestamptz not null default now()`

Indexes:

- `unique(workflow_run_id, sequence)`
- `index(workflow_run_id, created_at)`
- `index(event_type)`
- `index(commit_sha)`

Rules:

- Never update existing rows.
- Store raw tool/API responses in `payload` when useful.
- Derive current state from `workflow_runs`, but preserve every transition here.

### `review_findings`

Structured findings from local review agents and GitHub requested changes.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `review_run_id uuid references review_runs(id)`
- `source text not null`: `local_agent`, `github_review`, `github_comment`, `test_quality_gate`
- `reviewer_role text not null`: `backend`, `frontend`, `security`, `infra`, `docs`, `test`, `test_quality`, etc.
- `category text not null`: `correctness`, `security`, `design`, `test_quality`, `performance`, `maintainability`, `docs`, `infra`
- `severity text not null`: `critical`, `high`, `medium`, `low`, `info`
- `blocking boolean not null default false`
- `status text not null default 'open'`: `open`, `fixed`, `false_positive`, `accepted_risk`
- `title text not null`
- `explanation text not null`
- `suggested_fix text`
- `file_path text`
- `line_start integer`
- `line_end integer`
- `fingerprint text not null`: stable dedupe key from role/category/file/line/title
- `confidence numeric(3,2)`: optional `0.00` to `1.00`
- `introduced_by_commit_sha text`
- `fixed_by_commit_sha text`
- `github_comment_id text`
- `github_review_id text`
- `metadata jsonb not null default '{}'`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`
- `resolved_at timestamptz`

Indexes:

- `unique(workflow_run_id, fingerprint)`
- `index(workflow_run_id, status, blocking)`
- `index(workflow_run_id, reviewer_role)`
- `index(file_path)`
- `index(fixed_by_commit_sha)`

Rules:

- `blocking=true` means the workflow cannot mark the PR ready.
- Fake, weak, quota-filling, or always-passing tests must become blocking `test_quality` findings.
- `accepted_risk` should require an explicit human answer or PR review comment.

### `commits`

Commits created or tracked by the workflow.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `sha text not null`
- `parent_sha text`
- `branch text not null`
- `logical_step text not null`: short step name, for example `add-auth-checks` or `update-tests`
- `message text not null`
- `summary text not null`
- `files_changed text[] not null default '{}'`
- `tests_passed boolean not null default false`
- `test_run_ids uuid[] not null default '{}'`
- `review_finding_ids_fixed uuid[] not null default '{}'`
- `created_by text not null default 'CodeFlow-bot'`
- `pushed_at timestamptz`
- `metadata jsonb not null default '{}'`
- `created_at timestamptz not null default now()`

Indexes:

- `unique(workflow_run_id, sha)`
- `index(workflow_run_id, created_at)`
- `index(sha)`
- `index(logical_step)`

Rules:

- Implementation commits should be created only after the validation gate passes for the logical step.
- Plan/design commits can happen before implementation approval.
- Commit messages should describe the logical unit, not the automation internals.

### `test_runs`

Every test, lint, typecheck, or validation command run by the workflow.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `commit_sha text`
- `logical_step text`
- `command text not null`
- `command_type text not null`: `unit`, `integration`, `e2e`, `lint`, `typecheck`, `format`, `precommit`, `validation`, `custom`
- `scope text not null`: `targeted`, `changed_area`, `full_suite`
- `status text not null`: `passed`, `failed`, `timed_out`, `skipped`, `error`
- `exit_code integer`
- `duration_ms integer`
- `started_at timestamptz not null default now()`
- `completed_at timestamptz`
- `stdout_path text`
- `stderr_path text`
- `combined_output_path text`
- `failure_summary text`
- `coverage_summary jsonb not null default '{}'`
- `metadata jsonb not null default '{}'`

Indexes:

- `index(workflow_run_id, status)`
- `index(workflow_run_id, commit_sha)`
- `index(command_type)`
- `index(started_at)`

Rules:

- Store long output in files and keep paths in Postgres.
- Store concise failure summaries for retrieval and review.
- A logical implementation step cannot be committed until required validation commands pass.

### `review_runs`

One execution of a local review agent or imported GitHub review batch.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `commit_sha text`
- `reviewer_role text not null`: `backend`, `frontend`, `security`, `infra`, `docs`, `test`, `test_quality`, etc.
- `source text not null`: `local_agent`, `github_review`, `github_comments`
- `model text`
- `scope text not null`: `changed_files`, `full_pr`, `targeted_files`, `tests_only`, `design_only`
- `status text not null`: `passed`, `findings_opened`, `failed`, `timed_out`, `skipped`
- `files_reviewed text[] not null default '{}'`
- `findings_count integer not null default 0`
- `blocking_findings_count integer not null default 0`
- `prompt_path text`
- `output_path text`
- `duration_ms integer`
- `started_at timestamptz not null default now()`
- `completed_at timestamptz`
- `metadata jsonb not null default '{}'`

Indexes:

- `index(workflow_run_id, reviewer_role)`
- `index(workflow_run_id, status)`
- `index(workflow_run_id, commit_sha)`
- `index(started_at)`

Rules:

- Each review run should write structured findings into `review_findings`.
- Review output should be kept as an artifact file when it is too large for Postgres.
- Test quality review should run whenever tests are added or changed.

### `agent_invocations`

Every Claude CLI or Codex CLI call made by the workflow.

Columns:

- `id uuid primary key`
- `workflow_run_id uuid not null references workflow_runs(id)`
- `task_type text not null`: examples include `implement_init`, `planning`, `design`, `coding`, `test_generation`, `test_quality_review`, `code_review`, `security_review`, `fixing`, `summarization`, `rag_indexing`
- `state_node text`: workflow state or LangGraph node that made the call
- `provider_cli text not null`: `claude` or `codex`
- `model text not null`
- `routing_reason text not null`
- `cost_policy text not null`: `min_cost`, `balanced`, `max_quality`, `user_pinned`
- `status text not null`: `succeeded`, `failed`, `timed_out`, `cancelled`
- `prompt_path text`
- `output_path text`
- `structured_output_path text`
- `input_tokens integer`
- `output_tokens integer`
- `cached_input_tokens integer`
- `estimated_cost_minor_units bigint`
- `actual_cost_minor_units bigint`
- `billing_currency_code text not null default 'USD'`
- `duration_ms integer`
- `started_at timestamptz not null default now()`
- `completed_at timestamptz`
- `metadata jsonb not null default '{}'`

Indexes:

- `index(workflow_run_id, task_type)`
- `index(workflow_run_id, provider_cli, model)`
- `index(workflow_run_id, started_at)`
- `index(status)`

Rules:

- Log every model routing decision.
- Prefer the cheapest model that is expected to pass the task safely.
- Escalate to a stronger model after failures, ambiguous findings, high-risk files, or security-sensitive changes.
- Respect user-pinned CLI/model choices even when the router would choose differently.
- Store prompts and outputs as artifacts when they are large.

## Workflow Budget Enforcement

Each workflow run has one optional maximum budget:

- `billing_currency_code`: user-selected ISO 4217 currency code, for example `USD` or `EUR`.
- `max_workflow_budget_minor_units`: maximum spend for the workflow in minor units.
- `spent_budget_minor_units`: accumulated spend for the workflow in minor units.

Rules:

- Store money as integer minor units, not floating-point currency values.
- Do not hard-code USD semantics into column names or config names.
- Before each agent invocation, check whether the estimated next spend fits within the remaining workflow budget.
- After each agent invocation, add the actual or estimated cost to `spent_budget_minor_units`.
- If the next step would exceed the workflow budget, move the run to `failed` with a budget-exceeded reason and write a failure report.

## CLI and Model Routing

The workflow should support both Claude CLI and Codex CLI behind one adapter interface.

User choice:

- A run can pin `claude`, `codex`, or `auto`.
- `auto` lets the router choose per task.
- A run can also pin a specific model for all tasks when the user wants control.

Routing inputs:

- available CLIs on the machine
- available models for the user
- task type
- risk level
- changed file types
- required context size
- expected cost
- prior failure count
- user cost policy

Default routing policy:

- Use cheaper models for summarization, RAG indexing, status reports, and simple docs edits.
- Use balanced models for normal coding and test generation.
- Use stronger models for architecture, security, complex debugging, ambiguous requested changes, and final blocking review.
- Escalate one tier after repeated failed fixes or review/test regressions.
- Do not use expensive models for bulk repo rereads; use RAG and exact file reads instead.

Recommended config keys in `.CodeFlow/config.yaml`:

- `agent.default_cli`: `claude`, `codex`, or `auto`
- `agent.cost_policy`: `min_cost`, `balanced`, or `max_quality`
- `agent.allowed_clis`: list of allowed CLIs
- `agent.model_profiles`: concrete model IDs or CLI aliases per tier and CLI
- `agent.models_by_task`: model preferences per task type
- `agent.escalation_rules`: when to move to a stronger model
- `workflow.max_workflow_budget_minor_units`: workflow-level budget ceiling in the configured currency's minor units
- `workflow.billing_currency_code`: ISO 4217 currency code such as `USD` or `EUR`
- `agent.max_cost_minor_units_per_task`: optional task-level cost ceiling in the configured currency's minor units

## MVP v1 Boundary

MVP v1 should prove the full approved-PR loop without building every advanced feature.

Included:

- `proposal` prompt skill
- `implement-init` prompt skill
- `implement-code-creation` prompt skill
- `implement-validation` prompt skill
- `exploration` prompt skill
- `CodeFlow` local runner
- GitHub CLI based PR creation and polling
- same-PR plan and implementation flow
- allowed human reviewer approval gate
- Postgres state tables for runs, events, commits, tests, reviews, findings, and agent calls
- Claude CLI and Codex CLI adapters
- simple model router with cost policy
- validation gate must pass before implementation commits
- local review agents with blocking findings
- test quality review gate
- PR comment loop for design questions and requested changes
- basic RAG index with Postgres full-text search and pgvector chunks

Deferred:

- web UI
- GitHub webhooks
- distributed workers
- advanced dashboards
- automatic archival
- full marketplace/plugin packaging
- multi-repo orchestration

## Environment and Configuration

The local runner should be configured through environment variables plus an optional project config file.

Required env vars:

- `CODEFLOW_GITHUB_REPO_URL`: GitHub repository URL.
- `CODEFLOW_GITHUB_OWNER`: GitHub owner or organization.
- `CODEFLOW_GITHUB_REPO`: GitHub repository name.
- `CODEFLOW_BASE_BRANCH`: base branch for PRs, for example `main`.
- `CODEFLOW_LOCAL_REPO_PATH`: absolute path to the local checkout.
- `CODEFLOW_DATABASE_URL`: Postgres connection string.
- `CODEFLOW_WORKTREE_ROOT`: directory where temporary worktrees can be created.

GitHub auth:

- Prefer existing `gh` authentication for v1.
- Optional fallback: `GITHUB_TOKEN` or `GH_TOKEN` if direct API calls are needed.

Model configuration:

- Concrete planner/coder/reviewer models live in `.CodeFlow/config.yaml` under `agent.model_profiles` and `agent.models_by_task`.
- `CODEFLOW_EMBEDDING_MODEL`: embedding model for repo RAG.
- `CODEFLOW_AGENT_CLI`: `claude`, `codex`, or `auto`.
- `CODEFLOW_ALLOWED_AGENT_CLIS`: comma-separated list, for example `claude,codex`.
- `CODEFLOW_COST_POLICY`: `min_cost`, `balanced`, or `max_quality`.

Runtime limits:

- `CODEFLOW_MAX_ITERATIONS`: max review/fix loops.
- `CODEFLOW_MAX_RUNTIME_MINUTES`: max runtime for one workflow run.
- `CODEFLOW_BILLING_CURRENCY_CODE`: ISO 4217 currency code, for example `USD` or `EUR`.
- `CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS`: optional workflow budget ceiling in minor units, for example cents.
- `CODEFLOW_POLL_INTERVAL_SECONDS`: GitHub polling interval.

Optional behavior flags:

- `CODEFLOW_REQUIRE_HUMAN_APPROVAL=true`
- `CODEFLOW_ENABLE_RAG=true`
- `CODEFLOW_ENABLE_PR_QUESTIONS=true`
- `CODEFLOW_DRY_RUN=false`

Recommended non-secret project config file:

- `.CodeFlow/config.yaml`

Use it for repo-specific defaults such as labels, allowed reviewers, review-role rules, test commands, chunking rules, and ignored paths.

Do not store secrets in `.CodeFlow/config.yaml`.

## Review Finding Model

Each review finding should store:

- reviewer role
- severity
- title
- explanation
- file and line when available
- suggested fix
- status: `open`, `fixed`, `false_positive`, or `accepted_risk`
- linked commit or workflow step when fixed

The workflow should continue fixing and reviewing until no blocking findings remain.

## Project-Local Workflow Skills

Create project-local prompt skills instead of depending on external skill behavior.

These skills are implementation tools for this project. They are not the product name.

The local skill set should include only implement-init, implement-code-creation, implement-validation, proposal, and exploration skills.

Implementation order:

- Build Codex/Claude-style prompt skills first.
- Add Python command wrappers later only where orchestration, persistence, or CLI ergonomics require them.

Skills:

- Proposal skill: create proposal, design notes, specs, open questions, and task breakdown.
- Implement-init skill: configure local git identity, read repo instructions, inspect worktree state, fetch task URLs, map relevant code/tests, identify validation commands, and return a structured handoff without editing production code.
- Implement-code-creation skill: edit one approved logical step in a dedicated subagent, update real tests, and return a concise handoff for validation.
- Implement-validation skill: run a lightweight targeted validation gate before every workflow-created commit, verify obvious test integrity issues, and return `safe_to_commit` JSON without staging, committing, or pushing.
- Exploration skill: investigate the repo, compare options, answer design questions, and identify risks without making implementation changes unless explicitly asked.

Skill file structure:

- `.CodeFlow/skills/proposal/SKILL.md`
- `.CodeFlow/skills/implement-init/SKILL.md`
- `.CodeFlow/skills/implement-code-creation/SKILL.md`
- `.CodeFlow/skills/implement-validation/SKILL.md`
- `.CodeFlow/skills/exploration/SKILL.md`

Skill output contract:

- Proposal skill writes proposal/design/spec/task artifacts under `.CodeFlow/changes/<change_name>/`.
- Proposal skill writes `.CodeFlow/changes/<change_name>/proposal.json` with title, summary, artifact paths, open questions, and risk areas.
- Implement-init skill runs in a dedicated subagent before code creation and writes a final JSON handoff consumed by the code-creation skill.
- Implement-code-creation skill runs in a dedicated subagent and writes a step result JSON consumed by validation.
- Implement-validation skill runs after each logical implementation step and before the runner creates a commit.
- Exploration skill writes findings to a requested artifact path and does not edit product code by default.

Required proposal artifacts:

- `proposal.md`: what and why.
- `design.md`: architecture and tradeoffs.
- `tasks.md`: ordered implementation steps with test expectations.
- `questions.md`: open human questions, if any.

Runner command contract:

- `CodeFlow propose "<request>"`: run proposal skill, commit plan artifacts, open PR.
- `CodeFlow run <change_name>`: poll approval, run implement-init, implement, test, review, fix, and push.
- `CodeFlow status <change_name>`: print run state, PR state, commits, tests, and blocking findings.
- `CodeFlow resume <change_name>`: continue an interrupted run from Postgres state.
- `CodeFlow abort <change_name>`: mark the run aborted without deleting artifacts.

Claude/Codex adapter contract:

- Both CLIs expose the same internal call shape: task type, model, prompt path, output path, allowed tools, timeout, and expected schema.
- The adapter logs every call in `agent_invocations`.
- The adapter stores large prompts and outputs as files and writes paths to Postgres.
- The adapter must return structured failure details, not only raw stderr.

Initial model-routing defaults:

- `summarization`, `rag_indexing`, `status`: cheapest available model.
- `implement_init`, `proposal`, `design`, `exploration`: balanced model.
- `coding`, `test_generation`, `fixing`: balanced model, escalate after one failed loop.
- `security_review`, `test_quality_review`, `final_blocking_review`: strongest allowed review model.
- User-pinned CLI/model overrides router defaults.

Runner capabilities, not prompt skills:

- validation
- review orchestration
- status reporting
- resume
- abort

Excluded:

- archival skill
- automatic archival or cleanup of completed changes
- moving proposal/spec artifacts out of active project history as part of the workflow

Reasoning:

- This project wants PR approval and implementation history to remain visible on the same branch.
- The local runner and Postgres state already provide durable tracking.
- Archival can hide context that is still useful for review, debugging, and future RAG retrieval.

## PR Comment Feedback Loop

The bot should use PR comments as a human feedback channel.

Design phase:

- If the proposal/design/spec is ambiguous, the bot posts targeted questions on the PR.
- The workflow enters `WAITING_FOR_HUMAN_FEEDBACK`.
- The bot polls PR comments and review threads for replies.
- Human answers are stored in Postgres.
- The bot edits proposal, design, specs, or tasks based on those answers.
- Design revisions are committed to the same PR before approval.

Changes requested phase:

- If GitHub review state is `CHANGES_REQUESTED`, the bot reads review comments and requested changes.
- Clear requested changes become blocking findings.
- Ambiguous requested changes become PR comment questions.
- The bot waits for clarification, then updates design/code/tests as needed.
- Code changes are committed only after the logical step's validation gate passes.

Guardrails:

- Ask specific questions, not broad open-ended prompts.
- Link each question to the relevant file, design section, task, or review comment when possible.
- Do not proceed past an unresolved blocking ambiguity.
- Keep question/answer history in Postgres for audit and RAG retrieval.

## Token Efficiency and RAG Recommendation

Use a hybrid context system instead of rereading the whole repo on every loop.

Recommended approach:

- Keep a repo index in Postgres with vector search plus keyword/full-text search.
- Store file summaries, symbols, imports, ownership hints, test mappings, and recent change summaries.
- Refresh only changed files after each commit instead of rebuilding the whole index.
- Use retrieval to find likely relevant files, tests, docs, and prior decisions.
- Read exact source files from disk before editing, testing, or reviewing them.
- Treat RAG output as navigation context, not as authority.

Suggested retrieval layers:

- `repo_chunks`: searchable code/doc chunks with embeddings.
- `file_summaries`: one compact summary per file.
- `symbol_index`: functions, classes, routes, commands, config keys, and public APIs.
- `test_map`: likely tests for each source area.
- `change_context`: summaries of commits produced during the current workflow run.
- `review_memory`: prior findings, fixed bugs, and decisions for this PR.

Recommended stack:

- Postgres with `pgvector` for embeddings.
- Postgres full-text search for exact names, paths, symbols, and error messages.
- A reranker or LLM-based shortlisting step before reading full files.
- Git diff based incremental indexing after every commit.

Important guardrail:

- The workflow should not edit based only on retrieved snippets.
- Before editing, it must open the current full file, inspect nearby call sites, and check tests.

RAG v1 decisions:

- Use API embeddings first, configured by `CODEFLOW_EMBEDDING_MODEL`.
- Use local embeddings later only if cost, privacy, or offline use becomes important.
- Chunk source code by symbols/functions/classes when possible.
- Fall back to file-section chunks of about 80 to 160 lines with small overlap.
- Chunk Markdown by headings.
- Chunk config files by top-level keys or logical sections.
- Incrementally reindex files changed by `git diff` after each commit.

## `.CodeFlow/config.yaml` Draft

Example non-secret project config:

```yaml
github:
  repo_url: ""
  owner: ""
  repo: ""
  base_branch: main
  plan_label: plan-only
  ready_label: ready-for-review
  allowed_reviewers: []
  poll_interval_seconds: 60

agent:
  default_cli: auto
  allowed_clis: [claude, codex]
  cost_policy: balanced
  model_profiles:
    cheap:
      codex:
        model: gpt-5-mini
      claude:
        model: haiku
    balanced:
      codex:
        model: gpt-5
      claude:
        model: sonnet
    strong:
      codex:
        model: gpt-5
      claude:
        model: opus
  models_by_task:
    implement_init: balanced
    implement_code_creation: balanced
    implement_validation: cheap
    proposal: balanced
    design: balanced
    exploration: balanced
    coding: balanced
    fixing: balanced
    summarization: cheap
    rag_indexing: cheap
    code_review: balanced
    security_review: strong
    test_quality_review: strong
    final_blocking_review: strong
  escalation_rules:
    failed_fix_loops_before_escalation: 1
    test_regressions_before_escalation: 1
    security_sensitive_files_use_strong: true

workflow:
  require_human_approval: true
  max_iterations: 8
  max_runtime_minutes: 360
  billing_currency_code: USD
  max_workflow_budget_minor_units: null
  dry_run: false
  same_pr_for_plan_and_implementation: true

tests:
  require_tests_before_commit: true
  commands: []

review:
  stop_condition: zero_blocking_findings
  blocking_severities: [critical, high, medium]
  require_test_quality_review: true
  roles:
    backend:
      patterns: ["**/*.py", "**/*.go", "**/*.java", "**/*.ts"]
    frontend:
      patterns: ["**/*.tsx", "**/*.jsx", "**/*.css", "**/*.scss"]
    security:
      patterns: ["**/*auth*", "**/*secret*", "**/*token*", "**/*permission*"]
    infra:
      patterns: ["Dockerfile", "docker-compose*.yml", ".github/**", "k8s/**", "helm/**"]
    docs:
      patterns: ["**/*.md", "docs/**"]
    test_quality:
      patterns: ["tests/**", "**/*test*", "**/*spec*"]

rag:
  enabled: true
  embedding_model: ""
  chunking: symbol_aware
  incremental_reindex: true
  ignored_paths:
    - .git/**
    - node_modules/**
    - .venv/**
    - dist/**
    - build/**
```

## State Transition Rules

Core transitions:

- `PLAN_CREATED` -> `PR_OPENED` after plan artifacts are committed and PR is opened.
- `PR_OPENED` -> `WAITING_FOR_HUMAN_FEEDBACK` when proposal/design has unresolved questions.
- `WAITING_FOR_HUMAN_FEEDBACK` -> `REVISING_DESIGN` after humans reply.
- `REVISING_DESIGN` -> `WAITING_FOR_APPROVAL` after design/spec/tasks are updated and committed.
- `PR_OPENED` -> `WAITING_FOR_APPROVAL` when no human design questions are open.
- `WAITING_FOR_APPROVAL` -> `IMPLEMENT_INIT` after allowed human reviewer approval.
- `IMPLEMENT_INIT` -> `CODE_CREATION` after the init subagent returns a ready handoff.
- `CODE_CREATION` -> `VALIDATING_STEP` after one logical implementation step is complete.
- `VALIDATING_STEP` -> `CODE_CREATION` when validation fails and the fix is still part of the same logical step.
- `VALIDATING_STEP` -> `REVIEWING` when validation passes, returns `safe_to_commit: true`, and the logical step is committed.
- `REVIEWING` -> `FIXING` when blocking findings exist.
- `REVIEWING` -> `READY` when no blocking findings remain.
- `FIXING` -> `VALIDATING_STEP` after fixes are applied.
- Any state -> `WAITING_FOR_HUMAN_FEEDBACK` when a blocking ambiguity requires a human answer.

Failure transitions:

- Any state -> `failed` when max runtime, max cost, or max iterations are exceeded.
- Any state -> `aborted` only by explicit user action.

## GitHub CLI Usage v1

Use GitHub CLI first because it handles local auth and is easy to inspect.

Core commands:

- Create PR: `gh pr create --base <base> --head <branch> --title <title> --body-file <file>`
- Read PR state: `gh pr view <number> --json number,url,state,reviewDecision,latestReviews,labels,comments,headRefName,baseRefName`
- Read checks: `gh pr checks <number> --json name,state,conclusion,completedAt,link`
- Add comment/question: `gh pr comment <number> --body-file <file>`
- Read review threads when CLI output is insufficient: `gh api graphql ...`
- Push branch: `git push origin <branch>`

Rules:

- Do not start implementation until the PR has approval from an allowed human reviewer.
- Treat `CHANGES_REQUESTED` as blocking until resolved or clarified.
- Sync comments/reviews into Postgres before deciding the next state.
- Keep generated PR comments short, specific, and linked to the relevant design or code area.

## Artifact Layout

Recommended local files:

- `.CodeFlow/config.yaml`: non-secret project config.
- `.CodeFlow/skills/proposal/SKILL.md`: proposal prompt skill.
- `.CodeFlow/skills/implement-init/SKILL.md`: implementation init prompt skill.
- `.CodeFlow/skills/implement-code-creation/SKILL.md`: code creation prompt skill.
- `.CodeFlow/skills/implement-validation/SKILL.md`: pre-commit validation prompt skill.
- `.CodeFlow/skills/exploration/SKILL.md`: exploration prompt skill.
- `.CodeFlow/changes/<change_name>/proposal.md`
- `.CodeFlow/changes/<change_name>/design.md`
- `.CodeFlow/changes/<change_name>/tasks.md`
- `.CodeFlow/changes/<change_name>/questions.md`
- `.CodeFlow/changes/<change_name>/proposal.json`
- `.CodeFlow/runs/<run_id>/`: prompts, outputs, test logs, review logs, and summaries.
- `.CodeFlow/runs/<run_id>/artifacts/`: large raw artifacts referenced from Postgres.

Rules:

- Commit proposal/design/task artifacts to the PR branch.
- Do not commit large raw model outputs or full test logs by default.
- Keep large artifacts on disk and reference them from Postgres.

## Review Routing Rules

Route reviewers by changed files, changed behavior, and risk.

Always run:

- code review for changed implementation files
- test reviewer when tests or test infrastructure changed
- test quality reviewer when tests were added or edited
- final blocking review before marking ready

Conditional reviewers:

- security reviewer for auth, permissions, secrets, input parsing, network calls, dependency changes, or crypto.
- infra reviewer for CI, Docker, Kubernetes, deployment, env vars, or config changes.
- frontend reviewer for UI, CSS, accessibility, browser behavior, or client-side data flow.
- backend reviewer for APIs, persistence, job processing, business logic, or service boundaries.
- docs reviewer for public docs, user-facing docs, or design/spec-only changes.

Escalation:

- Run a stronger model for security review, test quality review, and final blocking review.
- Escalate fixing after one failed fix loop or repeated test regression.
- Ask humans when two reviewers disagree on a blocking design decision.

## Safety Rules

Hard rules:

- Never push directly to the base branch.
- Never force-push unless explicitly configured and approved.
- Never delete user files or unrelated changes.
- Never proceed past unresolved blocking ambiguity.
- Never mark ready while blocking findings remain open.
- Never accept fake or weak tests as satisfying the test requirement.

Dirty worktree handling:

- Detect dirty state before starting.
- Prefer a dedicated worktree under `CODEFLOW_WORKTREE_ROOT`.
- Refuse to overwrite unrelated user changes.
- Record all workflow-created commits in Postgres.

Budget handling:

- Stop before exceeding max cost, runtime, or iteration limits.
- Write a failure report with current state, last error, open findings, and next manual step.
- Do not hide partial progress; push only tested logical commits.

## Build Phases

Phase 0: scaffold

- `.CodeFlow/config.yaml` example
- five skill files
- basic `CodeFlow` CLI entrypoint
- Postgres migration skeleton

Phase 1: proposal PR

- run proposal skill
- write proposal/design/tasks/questions artifacts
- commit artifacts
- create PR with GitHub CLI

Phase 2: approval and comments

- poll PR approval
- sync PR comments and reviews
- ask/reconcile design questions

Phase 3: implementation loop

- run implement-code-creation skill per task
- run implement-validation before each workflow-created commit
- commit after validation passes for a logical step
- push branch

Phase 4: review loop

- route review roles
- run local review agents
- store findings
- fix blocking findings
- rerun tests and reviews

Phase 5: RAG

- index repo files
- retrieve relevant context per task
- incremental reindex after commits

Phase 6: hardening

- cost accounting
- better resume behavior
- failure reports
- docs and examples

## Test Quality Review Gate

After implementation, reviews must inspect whether tests are meaningful.

The test quality reviewer should check:

- Tests fail on plausible wrong behavior.
- Assertions validate actual outcomes, not only that code runs.
- Tests are connected to the changed behavior.
- Mocks do not replace the behavior being tested.
- Snapshot or golden tests are not blindly updated without behavioral justification.
- Coverage is not achieved by testing implementation details with no user-visible guarantee.
- New tests would catch the bug or requirement the implementation claims to address.

Suspicious test patterns:

- Assertions like `assert True`, empty tests, or smoke-only tests for non-trivial behavior.
- Excessive mocking of the unit under test.
- Tests that only assert no exception was raised.
- Tests that duplicate the implementation logic instead of checking expected behavior.
- Tests with weakened expectations to match current output.
- Tests added only to hit a quota or satisfy coverage numbers.

The workflow should treat fake or weak tests as blocking review findings.

## Open Design Choices

- Exact initial model names for cheap, balanced, and strong tiers.
- Actual allowed human reviewers for the first repo.

## Recommended Next Step

Scaffold MVP v1.

First files to create:

- `.CodeFlow/config.yaml`
- `.CodeFlow/skills/proposal/SKILL.md`
- `.CodeFlow/skills/implement-code-creation/SKILL.md`
- `.CodeFlow/skills/implement-validation/SKILL.md`
- `.CodeFlow/skills/exploration/SKILL.md`
- `CodeFlow` CLI entrypoint
- first Postgres migration
- Claude/Codex adapter interface

Scaffold status:

- Created `.CodeFlow/config.yaml`.
- Created proposal, implement-init, implement-code-creation, implement-validation, and exploration skill files.
- Created `CodeFlow` Python package with `python -m CodeFlow` entrypoint.
- Created CLI scaffold commands: `doctor`, `propose`, `run`, `status`, `resume`, `abort`.
- Created Claude CLI and Codex CLI adapter seam.
- Created model router scaffold.
- Created initial Postgres migration at `CodeFlow/db/migrations/001_initial.sql`.

Review/fix status:

- Fixed `doctor` so missing required runtime prerequisites make it exit non-zero.
- Fixed `doctor` so a pinned unavailable CLI is reported as blocking.
- Fixed model routing so `auto` resolves to an actual allowed CLI when available.
- Fixed model routing so it does not silently use a disallowed CLI.
- Fixed model routing so invalid cost policies normalize to `balanced`.
- Fixed pinned model routing so audit rows can use `user_pinned`.
- Fixed adapter invocation so missing prompts, missing commands, and timeouts return structured failures.
- Fixed adapter invocation so output directories are created before writing artifacts.
- Fixed Claude adapter option ordering and JSON schema support.
- Fixed Codex adapter JSON schema support.
- Added regression tests for CLI doctor behavior, model routing, and adapter command/failure behavior.
- Added scoped `structlog` JSON logs for blocking `doctor` failures on stderr without changing normal CLI stdout.
