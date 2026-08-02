create extension if not exists pgcrypto;
create extension if not exists vector;

create table repositories (
  id uuid primary key default gen_random_uuid(),
  github_repo_url text not null,
  github_owner text not null,
  github_repo text not null,
  local_repo_path text not null,
  default_base_branch text not null default 'main',
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (github_owner, github_repo, local_repo_path)
);

create table workflow_runs (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  change_name text not null,
  title text not null,
  description text,
  status text not null,
  current_state text not null,
  base_branch text not null,
  work_branch text not null,
  local_repo_path text not null,
  worktree_path text,
  github_pr_number integer,
  github_pr_url text,
  plan_path text,
  proposal_path text,
  design_path text,
  tasks_path text,
  approval_required boolean not null default true,
  approved_by text[] not null default '{}',
  approved_at timestamptz,
  worker_id text,
  lease_expires_at timestamptz,
  billing_currency_code text not null default 'USD',
  max_workflow_budget_minor_units bigint,
  spent_budget_minor_units bigint not null default 0,
  model_config jsonb not null default '{}',
  runtime_limits jsonb not null default '{}',
  metadata jsonb not null default '{}',
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  unique (repository_id, change_name),
  check (billing_currency_code ~ '^[A-Z]{3}$'),
  check (max_workflow_budget_minor_units is null or max_workflow_budget_minor_units >= 0),
  check (spent_budget_minor_units >= 0),
  check (status in ('planning', 'waiting_for_feedback', 'waiting_for_approval', 'coding', 'testing', 'reviewing', 'fixing', 'ready', 'failed', 'aborted'))
);

create index workflow_runs_repository_status_idx on workflow_runs(repository_id, status);
create index workflow_runs_pr_number_idx on workflow_runs(github_pr_number);
create index workflow_runs_lease_expires_idx on workflow_runs(lease_expires_at);

create table workflow_events (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  sequence bigint not null,
  event_type text not null,
  state_from text,
  state_to text,
  actor_type text not null,
  actor_id text,
  message text,
  payload jsonb not null default '{}',
  github_comment_id text,
  github_review_id text,
  commit_sha text,
  created_at timestamptz not null default now(),
  unique (workflow_run_id, sequence),
  check (actor_type in ('bot', 'human', 'github', 'system', 'review_agent'))
);

create index workflow_events_run_created_idx on workflow_events(workflow_run_id, created_at);
create index workflow_events_type_idx on workflow_events(event_type);
create index workflow_events_commit_idx on workflow_events(commit_sha);

create table workflow_steps (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  state_from text,
  state_to text not null,
  status text not null default 'completed',
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  metadata jsonb not null default '{}'
);

create index workflow_steps_run_started_idx on workflow_steps(workflow_run_id, started_at);

create table pull_requests (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  repository_id uuid not null references repositories(id),
  github_pr_number integer not null,
  github_pr_url text not null,
  head_ref text not null,
  base_ref text not null,
  state text not null,
  review_decision text,
  labels text[] not null default '{}',
  approved_by text[] not null default '{}',
  changes_requested_by text[] not null default '{}',
  last_synced_at timestamptz,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workflow_run_id),
  unique (repository_id, github_pr_number)
);

create table commits (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  sha text not null,
  parent_sha text,
  branch text not null,
  logical_step text not null,
  message text not null,
  summary text not null,
  files_changed text[] not null default '{}',
  tests_passed boolean not null default false,
  test_run_ids uuid[] not null default '{}',
  review_finding_ids_fixed uuid[] not null default '{}',
  created_by text not null default 'CodeFlow-bot',
  pushed_at timestamptz,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  unique (workflow_run_id, sha)
);

create index commits_run_created_idx on commits(workflow_run_id, created_at);
create index commits_sha_idx on commits(sha);
create index commits_logical_step_idx on commits(logical_step);

create table test_runs (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  commit_sha text,
  logical_step text,
  command text not null,
  command_type text not null,
  scope text not null,
  status text not null,
  exit_code integer,
  duration_ms integer,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  stdout_path text,
  stderr_path text,
  combined_output_path text,
  failure_summary text,
  coverage_summary jsonb not null default '{}',
  metadata jsonb not null default '{}',
  check (command_type in ('unit', 'integration', 'e2e', 'lint', 'typecheck', 'format', 'precommit', 'validation', 'custom')),
  check (scope in ('targeted', 'changed_area', 'full_suite')),
  check (status in ('passed', 'failed', 'timed_out', 'skipped', 'error'))
);

create index test_runs_status_idx on test_runs(workflow_run_id, status);
create index test_runs_commit_idx on test_runs(workflow_run_id, commit_sha);
create index test_runs_command_type_idx on test_runs(command_type);
create index test_runs_started_idx on test_runs(started_at);

create table review_runs (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  commit_sha text,
  reviewer_role text not null,
  source text not null,
  model text,
  scope text not null,
  status text not null,
  files_reviewed text[] not null default '{}',
  findings_count integer not null default 0,
  blocking_findings_count integer not null default 0,
  prompt_path text,
  output_path text,
  duration_ms integer,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  metadata jsonb not null default '{}',
  check (source in ('local_agent', 'github_review', 'github_comments')),
  check (scope in ('changed_files', 'full_pr', 'targeted_files', 'tests_only', 'design_only')),
  check (status in ('passed', 'findings_opened', 'failed', 'timed_out', 'skipped'))
);

create index review_runs_role_idx on review_runs(workflow_run_id, reviewer_role);
create index review_runs_status_idx on review_runs(workflow_run_id, status);
create index review_runs_commit_idx on review_runs(workflow_run_id, commit_sha);
create index review_runs_started_idx on review_runs(started_at);

create table review_findings (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  review_run_id uuid references review_runs(id),
  source text not null,
  reviewer_role text not null,
  category text not null,
  severity text not null,
  blocking boolean not null default false,
  status text not null default 'open',
  title text not null,
  explanation text not null,
  suggested_fix text,
  file_path text,
  line_start integer,
  line_end integer,
  fingerprint text not null,
  confidence numeric(3,2),
  introduced_by_commit_sha text,
  fixed_by_commit_sha text,
  github_comment_id text,
  github_review_id text,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (workflow_run_id, fingerprint),
  check (source in ('local_agent', 'github_review', 'github_comment', 'test_quality_gate')),
  check (severity in ('critical', 'high', 'medium', 'low', 'info')),
  check (status in ('open', 'fixed', 'false_positive', 'accepted_risk'))
);

create index review_findings_status_idx on review_findings(workflow_run_id, status, blocking);
create index review_findings_role_idx on review_findings(workflow_run_id, reviewer_role);
create index review_findings_file_idx on review_findings(file_path);
create index review_findings_fixed_commit_idx on review_findings(fixed_by_commit_sha);

create table agent_invocations (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  task_type text not null,
  state_node text,
  provider_cli text not null,
  model text not null,
  routing_reason text not null,
  cost_policy text not null,
  status text not null,
  prompt_path text,
  output_path text,
  structured_output_path text,
  input_tokens integer,
  output_tokens integer,
  cached_input_tokens integer,
  estimated_cost_minor_units bigint,
  actual_cost_minor_units bigint,
  billing_currency_code text not null default 'USD',
  duration_ms integer,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  metadata jsonb not null default '{}',
  check (provider_cli in ('claude', 'codex')),
  check (cost_policy in ('min_cost', 'balanced', 'max_quality', 'user_pinned')),
  check (billing_currency_code ~ '^[A-Z]{3}$'),
  check (status in ('succeeded', 'failed', 'timed_out', 'cancelled'))
);

create index agent_invocations_task_idx on agent_invocations(workflow_run_id, task_type);
create index agent_invocations_model_idx on agent_invocations(workflow_run_id, provider_cli, model);
create index agent_invocations_started_idx on agent_invocations(workflow_run_id, started_at);
create index agent_invocations_status_idx on agent_invocations(status);

create table implementation_handoffs (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  agent_invocation_id uuid references agent_invocations(id),
  status text not null,
  git_identity_configured boolean not null default false,
  repo_instructions_read text[] not null default '{}',
  repo_instruction_summary jsonb not null default '[]',
  existing_worktree_changes jsonb not null default '[]',
  external_references jsonb not null default '[]',
  relevant_files jsonb not null default '[]',
  relevant_tests jsonb not null default '[]',
  validation_commands jsonb not null default '[]',
  coding_standards jsonb not null default '[]',
  implementation_plan jsonb not null default '[]',
  risks jsonb not null default '[]',
  escalation_reason text,
  raw_handoff_path text,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}',
  unique (workflow_run_id),
  check (status in ('ready', 'escalate'))
);

create index implementation_handoffs_status_idx on implementation_handoffs(workflow_run_id, status);

create table pr_comments (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  github_comment_id text not null,
  github_review_id text,
  author text not null,
  body text not null,
  file_path text,
  line_start integer,
  line_end integer,
  is_bot boolean not null default false,
  created_at timestamptz not null,
  updated_at timestamptz,
  synced_at timestamptz not null default now(),
  metadata jsonb not null default '{}',
  unique (workflow_run_id, github_comment_id)
);

create table human_questions (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  question_key text not null,
  question text not null,
  why_it_matters text,
  blocks_implementation boolean not null default false,
  github_comment_id text,
  status text not null default 'open',
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  metadata jsonb not null default '{}',
  unique (workflow_run_id, question_key),
  check (status in ('open', 'answered', 'withdrawn'))
);

create table human_answers (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  human_question_id uuid references human_questions(id),
  github_comment_id text,
  author text not null,
  answer text not null,
  accepted boolean not null default false,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'
);

create table design_revisions (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  human_answer_id uuid references human_answers(id),
  commit_sha text,
  artifact_path text not null,
  summary text not null,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'
);

create table repo_files (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  path text not null,
  content_hash text not null,
  language text,
  size_bytes bigint,
  last_indexed_commit_sha text,
  indexed_at timestamptz,
  metadata jsonb not null default '{}',
  unique (repository_id, path)
);

create table repo_chunks (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  repo_file_id uuid not null references repo_files(id),
  path text not null,
  chunk_kind text not null,
  symbol_name text,
  line_start integer,
  line_end integer,
  content text not null,
  content_tsv tsvector generated always as (to_tsvector('english', content)) stored,
  embedding vector,
  embedding_model text,
  content_hash text not null,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index repo_chunks_file_idx on repo_chunks(repo_file_id);
create index repo_chunks_path_idx on repo_chunks(repository_id, path);
create index repo_chunks_tsv_idx on repo_chunks using gin(content_tsv);

create table file_summaries (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  repo_file_id uuid not null references repo_files(id),
  path text not null,
  summary text not null,
  model text,
  content_hash text not null,
  updated_at timestamptz not null default now(),
  unique (repo_file_id, content_hash)
);

create table symbol_index (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  repo_file_id uuid not null references repo_files(id),
  path text not null,
  symbol_kind text not null,
  symbol_name text not null,
  line_start integer,
  line_end integer,
  metadata jsonb not null default '{}'
);

create index symbol_index_lookup_idx on symbol_index(repository_id, symbol_name);
create index symbol_index_path_idx on symbol_index(repository_id, path);

create table test_map (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid not null references repositories(id),
  source_path text not null,
  test_path text not null,
  confidence numeric(3,2),
  reason text,
  updated_at timestamptz not null default now(),
  unique (repository_id, source_path, test_path)
);

create table change_context (
  id uuid primary key default gen_random_uuid(),
  workflow_run_id uuid not null references workflow_runs(id),
  context_type text not null,
  title text not null,
  body text not null,
  related_commit_sha text,
  related_finding_id uuid references review_findings(id),
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'
);

create index change_context_run_type_idx on change_context(workflow_run_id, context_type);
