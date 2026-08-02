# LangGraph CI Loop — Design Exploration Document

> **Status:** Exploration / Architectural Blueprint  
> **Date:** 2026-07-09  
> **Context:** Automating the "implement → review → fix → verify" loop using LangGraph to eliminate context window exhaustion.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Why Context Exhaustion Happens](#2-why-context-exhaustion-happens)
3. [The LangGraph Solution: Constant Context Per Iteration](#3-the-langgraph-solution-constant-context-per-iteration)
4. [Architecture Overview](#4-architecture-overview)
5. [State Design](#5-state-design)
6. [File-Based Context Bus](#6-file-based-context-bus)
7. [Node Implementations](#7-node-implementations)
8. [Cycle Detection](#8-cycle-detection)
9. [Reconciliation Node — Constraint Resolution](#9-reconciliation-node--constraint-resolution)
10. [Degradation Detection](#10-degradation-detection)
11. [Diminishing Authority](#11-diminishing-authority)
12. [Fix Budgeting — Limiting N Per Node](#12-fix-budgeting--limiting-n-per-node)
13. [Council Review as 5-Perspective Subgraph](#13-council-review-as-5-perspective-subgraph)
14. [Parallel Reviews](#14-parallel-reviews)
15. [Human Escape Hatch](#15-human-escape-hatch)
16. [Checkpointing and Resumability](#16-checkpointing-and-resumability)
17. [The "Dumb" Middle Ground — v0](#17-the-dumb-middle-ground--v0)
18. [Testing Strategy](#18-testing-strategy)
19. [Pros and Cons](#19-pros-and-cons)
20. [Open Questions](#20-open-questions)
21. [Full Architecture Diagram](#21-full-architecture-diagram)
22. [Preventing Over-Fixing](#22-preventing-over-fixing-the-1-cause-of-new-bugs)
23. [Cost Estimation Model](#23-cost-estimation-model)
24. [The "Dumb Loop" — Full v0 Implementation](#24-the-dumb-loop--full-v0-implementation)
25. [OpenSpec Integration](#25-openspec-integration)
26. [Answered Open Questions](#26-answered-open-questions)
27. [Convergence Guarantees](#27-convergence-guarantees)
28. [Memory Architecture](#28-memory-architecture--what-crosses-iteration-boundaries)
29. [Failure Scenarios and Recovery Matrix](#29-failure-scenarios-and-recovery-matrix)
30. [Evolution Path](#30-evolution-path)
31. [Review — Additional Open Points Discovered and Clarified](#31-review--additional-open-points-discovered-and-clarified)

---

## 1. Problem Statement

The current approach uses a single Claude Code session with a natural language goal:

```
/goal Run /opsx:apply to implement X.
After implementing, loop until all of the following are true — then stop:
1. /code-review reports no findings worth fixing
2. /council-review reports no findings worth fixing
3. All unit tests pass
4. Code coverage for new/changed code is ≥ 80%
5. pre-commit run --files <changed files> exits clean
```

**The failure mode:** Each iteration accumulates conversation history. By iteration 5-6, the context window is exhausted. Claude either loses important details during summarization, prematurely terminates, or makes confused decisions.

**The goal:** Run N iterations without cumulative context growth. Each iteration should use the same amount of context as iteration 1.

---

## 2. Why Context Exhaustion Happens

```
┌─────────────────────────── Context Window ────────────────────────────────┐
│                                                                           │
│  Iteration 1:  implement (big) + code-review output + fix                │
│  Iteration 2:  council-review output + triage reasoning + fix            │
│  Iteration 3:  test output (often verbose) + fix                         │
│  Iteration 4:  pre-commit output + fix                                   │
│  Iteration 5:  code-review again + reasoning about what changed...       │
│  Iteration 6:  ██████████ CONTEXT FULL ██████████                        │
│                                                                           │
│  Each iteration accumulates:                                              │
│  - Full review output (2-5k tokens)                                      │
│  - Reasoning about triage (1-2k tokens)                                  │
│  - Code diffs / edits (variable, can be huge)                            │
│  - Test output (pytest can dump 5-10k tokens easily)                     │
│  - Pre-commit output                                                     │
│                                                                           │
│  × 4-8 iterations = 50-100k tokens of ACCUMULATED conversation           │
└───────────────────────────────────────────────────────────────────────────┘
```

**Root cause:** Every iteration carries the full history of all prior iterations, even though most of that history is irrelevant to what needs to happen *right now*.

### Context growth comparison

| Approach | Iteration 1 | Iteration 4 | Iteration 8 |
|----------|-------------|-------------|-------------|
| **Single Claude Code session** | ~15k | ~60k | ~120k+ (💀) |
| **LangGraph with isolated nodes** | ~15k | ~15k | ~15k |

Single-session is **O(n)** in context per iteration. LangGraph is **O(1)**.

---

## 3. The LangGraph Solution: Constant Context Per Iteration

Each node gets a **fresh context window** with only the state it needs:

```
┌─────────────────────────────────────────────────────────────────┐
│                        LangGraph State                           │
│                                                                  │
│  {                                                               │
│    "iteration": 4,                                               │
│    "changed_files": ["src/schema.py", "tests/test_schema.py"],   │
│    "gates": {                                                    │
│      "code_review": "pass",                                      │
│      "council_review": "2_findings",                             │
│      "tests": "pass",                                            │
│      "coverage": 84,                                             │
│      "precommit": "fail"                                         │
│    },                                                            │
│    "current_findings": [                                         │
│      {"file": "src/schema.py", "line": 42, "issue": "..."}      │
│    ]                                                             │
│  }                                                               │
│                                                                  │
│  ← THIS is what each node sees. Not 100k of conversation.       │
└─────────────────────────────────────────────────────────────────┘
```

Each node invocation:

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Review Node    │     │   Fix Node       │     │   Test Node      │
│                  │     │                  │     │                  │
│ Context:         │     │ Context:         │     │ Context:         │
│ - The diff       │     │ - The findings   │     │ - Changed files  │
│ - Review prompt  │     │ - The files      │     │ - Test command   │
│                  │     │ - Fix prompt     │     │                  │
│ ~5-10k tokens    │     │ ~10-20k tokens   │     │ ~2k tokens +     │
│                  │     │                  │     │   test output    │
└──────────────────┘     └──────────────────┘     └──────────────────┘

Each node: FRESH window. No history accumulation.
State carries forward as structured data, not conversation.
```

---

## 4. Architecture Overview

### Approach: Claude CLI as Subprocess

The graph calls `claude` CLI as a subprocess. This:
- Keeps all Claude Code skills working (`/code-review`, etc.)
- Gets project awareness (CLAUDE.md, settings, file context) for free
- Avoids reimplementing review/triage logic as raw API calls
- Each subprocess = fresh context window

### Why "shelling out" is NOT brittle

| Concern | Actually a problem? |
|---------|-------------------|
| Parsing unstructured output | **No** — use `--output-format json` or structured prompts |
| Process crashes | **No** — standard subprocess hygiene (timeout, retry) |
| Auth/environment issues | **Rare** — same env as your terminal |
| No shared memory between calls | **No** — that's the whole point (context isolation) |
| CLI interface changes between versions | **Minor** — pin your claude version |
| Long-running processes | **Maybe** — some fix operations could take minutes |

The real requirement: **prompt discipline.** Each subprocess needs a well-structured prompt that elicits parseable output.

---

## 5. State Design

The graph state is minimal — heavy context lives in files on disk:

```python
class LoopState(TypedDict):
    # Identity
    change_name: str
    loop_dir: str              # absolute path to .loop/

    # Iteration tracking
    iteration: int
    max_iterations: int

    # What's being worked on
    changed_files: list[str]

    # Current gate status
    gates: GateStatus          # {code_review, council_review, tests, coverage, precommit}

    # Active work items (for routing decisions, not LLM context)
    active_findings: list[Finding]
    test_failures: list[str]
    precommit_errors: list[str]

    # Cycle detection
    cycle_detected: bool
    cycle_type: str            # "" | "oscillation" | "recurrence" | "stall"
    locked_files: list[str]

    # Termination
    all_gates_pass: bool
    halted: bool
    halt_reason: str
```

Key principle:
```
LangGraph state  →  controls FLOW    (which node runs next)
.loop/ files     →  controls CONTEXT (what the LLM knows when it runs)
```

---

## 6. File-Based Context Bus

### The Pattern

Instead of passing state through stdout/parsing, **nodes write JSON files that the next node reads:**

```
.loop/
├── memory.json               ← append-only compressed history
├── iteration-1/
│   ├── implementation.json   ← what was implemented
│   ├── code-review-findings.json
│   ├── council-review-findings.json
│   ├── triage.json           ← what was accepted/rejected
│   ├── fix-summary.json      ← what was fixed and why
│   ├── test-results.json     ← pass/fail + coverage
│   └── precommit-results.json
├── iteration-2/
│   ├── ...
│   └── cycle-info.json       ← (if cycle detected)
├── iteration-3/
│   ├── ...
│   └── reconciliation.json   ← (if reconciliation ran)
└── NEEDS_HUMAN_DECISION.json ← (if human intervention needed)
```

### Example: fix-summary.json

```json
{
  "iteration": 3,
  "fixed": [
    {
      "file": "src/schema.py",
      "lines_changed": [42, 67, 103],
      "what": "Added null check before accessing .provider field",
      "why": "council-review finding #2: possible NoneType access",
      "test_hint": "test with provider=None in CI config input"
    }
  ],
  "files_touched": ["src/schema.py", "src/normalize.py"],
  "suggested_test_focus": [
    "tests/test_schema.py::test_null_provider",
    "tests/test_normalize.py::test_explicit_override"
  ]
}
```

### Why Files Are Better Than State Dicts

| Passing state as... | Context cost | Debuggability | Resumability |
|---|---|---|---|
| Conversation history | Grows every turn | Read the chat? 😬 | Start over |
| Python dict in memory | Zero (not seen by LLM) | Print it | Lost on crash |
| **Files on disk** | **Only what's read** | **`cat .loop/iteration-3/fix-summary.json`** | **Just re-run from last checkpoint** |

### How Each Node Uses Files

```
  Fix Node                        Test Node                    Review Node
  ┌──────────┐                    ┌──────────┐                ┌──────────┐
  │ Reads:   │                    │ Reads:   │                │ Reads:   │
  │ • findings.json               │ • fix-summary.json        │ • git diff
  │ • source files                │                           │ • test-results.json
  │                               │ Runs:    │                │ • fix-summary.json
  │ Writes:  │                    │ • pytest │                │          │
  │ • fix-summary.json            │                           │ Writes:  │
  │ • (edits source)              │ Writes:  │                │ • review-findings.json
  └──────────┘                    │ • test-results.json       └──────────┘
                                  └──────────┘

              .loop/ directory is the shared memory bus
```

---

## 7. Node Implementations

### Node: Implement

- Calls `/opsx:apply <change_name>` via claude CLI
- Writes `implementation.json` with files changed
- Snapshots initial file hashes into memory

### Node: Code Review

- Reads changed files (via git diff)
- Prompts claude to find correctness/security/design issues only
- Writes `code-review-findings.json`
- Updates `gates.code_review`

### Node: Council Review

- 5 parallel perspectives (see [Section 13](#13-council-review-as-5-perspective-subgraph))
- Each perspective runs independently
- Results merged via pure Python (dedup, confidence scoring)
- Writes `council-review-findings.json`

### Node: Triage

- Mostly pure Python (severity filter, exclude locked, exclude failed)
- LLM only for gray-zone decisions
- Applies diminishing authority threshold
- Writes `triage.json` with accepted/rejected lists

### Node: Fix

- Chunked: max N findings per call (see [Section 12](#12-fix-budgeting--limiting-n-per-node))
- Reads findings + relevant source files
- Applies fixes, commits after each
- Writes `fix-summary.json`

### Node: Run Tests (deterministic — no LLM)

- `pytest --cov` via subprocess
- Parses results, calculates coverage for changed files
- Writes `test-results.json`
- Updates `gates.tests` and `gates.coverage`

### Node: Run Pre-commit (deterministic — no LLM)

- `pre-commit run --files <changed_files>` via subprocess
- Writes `precommit-results.json`
- Updates `gates.precommit`

### Node: Check Gates

- Evaluates all gates
- Records gate status in memory
- Increments iteration counter
- Routes to: END, HALT, or loop back

### Node: Detect Cycles (pure Python — no LLM)

- See [Section 8](#8-cycle-detection)

### Node: Reconcile

- See [Section 9](#9-reconciliation-node--constraint-resolution)

### Node: Halt

- Generates human-readable report
- Surfaces what remains unresolved

---

## 8. Cycle Detection

### The Problem

```
Iteration 1:  Review says "add null check on line 42"
              Fix adds: if provider is None: return default

Iteration 2:  Tests fail: "test_real_provider expected ValueError, got default"
              Fix removes the null check, adds raise ValueError

Iteration 3:  Review says "unhandled None case on line 42, add null check"
              Fix adds: if provider is None: return default

              ♻️ INFINITE LOOP ♻️
```

The graph executes perfectly — every node does its job — but the *fixes contradict each other*.

### Detection Strategies (all pure Python, no LLM cost)

#### Strategy 1: File Hash Oscillation (A-B-A)

Track SHA256 of each file across iterations:

```python
def detect_oscillation(file_hashes: list[str]) -> bool:
    """Detect A-B-A pattern in a single file's hash history."""
    if len(hashes) < 3:
        return False
    return hashes[-1] == hashes[-3] and hashes[-1] != hashes[-2]
```

#### Strategy 2: Cross-File Composite Hash

A subtler case: no single file oscillates, but the *system* oscillates:

```python
def detect_system_oscillation(all_file_hashes: dict[str, list[str]]) -> bool:
    """Hash the tuple of ALL changed files together."""
    composite_hashes = []
    for iteration in range(max_iterations):
        composite = hashlib.sha256(
            "".join(h[iteration] for h in all_file_hashes.values()).encode()
        ).hexdigest()[:12]
        composite_hashes.append(composite)
    
    # Check A-B-A at system level
    if len(composite_hashes) >= 3:
        return composite_hashes[-1] == composite_hashes[-3]
    return False
```

#### Strategy 3: Finding Recurrence

Same fingerprint appears in iteration N and N+2 (after being "fixed" in N+1):

```python
def detect_finding_recurrence(current_fingerprints: set, history: list[set]) -> set:
    """Check if any current finding was present 2 iterations ago."""
    if len(history) >= 2:
        return current_fingerprints & history[-2]  # intersection
    return set()
```

#### Strategy 4: Gate Stall

Identical gate status for 2+ consecutive iterations = no progress:

```python
def detect_stall(gate_history: list[dict]) -> bool:
    if len(gate_history) >= 2:
        return gate_history[-1] == gate_history[-2]
    return False
```

### Detection Flow

```
┌─────────────────────────────┐
│   After each fix node:      │
│   1. Hash changed files     │
│   2. Fingerprint findings   │
│   3. Record test results    │
│   4. Append to memory.json  │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│   Cycle Detector            │
│   (pure Python, no LLM)    │
│                             │
│   • File hash oscillation?  │
│   • System composite hash?  │
│   • Finding recurrence?     │
│   • Gate stall?             │
└──────────────┬──────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
 No cycle            Cycle detected
    │                     │
    ▼                     ▼
 Continue          Route to RECONCILE
```

---

## 9. Reconciliation Node — Constraint Resolution

### Why It's Different From "Fix Harder"

| Normal Fix Node | Reconciliation Node |
|--|--|
| "Here's a bug. Fix it." | "Two requirements conflict. Find a third path." |
| Single constraint | Multiple constraints presented together |
| No history of attempts | Full history of what was tried |
| Success = issue gone | Success = BOTH constraints satisfied |

### The Reconciliation Prompt Structure

The prompt does four things:

1. **Names the conflict explicitly** — "Constraint A wants X, Constraint B wants Y"
2. **Shows the attempt history** — "You tried X, it broke Y. You tried Y, it broke X."
3. **Offers resolution patterns** — widens the solution space
4. **Demands proof** — structured output forces verification of both constraints

```markdown
You are a RECONCILIATION agent. You are NOT a normal fixer.

## The Problem
The automated fix loop has detected a CYCLE — the same issue keeps being
fixed and then re-introduced because two constraints are in conflict.

## Conflict Details

### What oscillated:
{oscillation_description}

### History of attempts:
{attempt_history}
- Iteration 1: added null check → test broke
- Iteration 2: removed null check → review flagged again

### Constraint A (from review):
"Handle None gracefully — don't let NoneType propagate"

### Constraint B (from tests):
"test_real_provider expects ValueError when provider is None"

## Your Job

Find a THIRD OPTION that satisfies BOTH constraints simultaneously.
Do not simply pick one side.

## Common Resolution Patterns:
1. **Widen the type** — Optional/Union so both cases are handled
2. **Split the path** — different behavior for different callers
3. **Update the test** — if the test expectation is wrong/outdated
4. **Document and skip** — if the review concern is intentionally not addressed
5. **Extract a configuration** — make the conflicting behavior configurable
6. **Add a guard clause** — handle the edge case without changing main path

## Output Schema:
{
  "resolution_strategy": "which pattern (1-6 or novel)",
  "explanation": "why this satisfies both constraints",
  "files_modified": ["list"],
  "constraint_a_satisfied": true/false,
  "constraint_b_satisfied": true/false,
  "compromise_made": "what tradeoff was accepted, if any"
}

## IMPORTANT
If you cannot resolve both constraints, set both satisfied fields to false.
The loop will LOCK the file and surface it for human review.
```

### Response Strategies After Cycle Detection

| Cycle Type | Response |
|---|---|
| File oscillation (A-B-A) | → Reconciliation node |
| Finding recurrence | → Lock file + report |
| Gate stall (no progress) | → Reconciliation OR halt |
| Reconciliation fails | → Lock file, continue on other files |

### After Reconciliation

```python
if not both_satisfied:
    # Lock the file — stop trying to fix it
    state["locked_files"].append(oscillating_file)
    # Loop can still converge on everything ELSE
else:
    # Resolution worked — proceed to tests to verify
    pass
```

---

## 10. Degradation Detection

Beyond cycles, there's **monotonic degradation** — things getting steadily worse:

```
Iteration 1: 2 findings, 0 test failures
Iteration 2: 3 findings, 1 test failure
Iteration 3: 5 findings, 3 test failures
Iteration 4: 8 findings, 4 test failures    ← GETTING WORSE
```

### Detection

```python
def detect_degradation(memory: dict) -> bool:
    """
    Heuristic: if failing gates has INCREASED for 3 consecutive
    iterations, the fix loop is making things worse.
    """
    history = memory.get("gate_history", [])
    if len(history) < 3:
        return False
    
    fail_counts = [
        sum(1 for v in entry.values() if v is False)
        for entry in history[-3:]
    ]
    
    # Strictly increasing = getting worse
    return fail_counts[0] < fail_counts[1] < fail_counts[2]
```

### Response to Degradation

```
1. git stash (save current mess)
2. git checkout -- . (revert to last green state)
3. Route to reconciliation with framing:
   "The batch-fix approach is making things worse.
    Try fixing ONE issue at a time, verifying after each."

OR: reduce fix scope to 1 finding per iteration (automatic slowdown)
```

---

## 11. Diminishing Authority

As iterations increase, the triage threshold **narrows**. This guarantees eventual convergence:

```python
def allowed_severities(iteration: int) -> list[str]:
    if iteration <= 2:
        return ["correctness", "security", "design"]
    elif iteration <= 4:
        return ["correctness", "security"]
    elif iteration <= 6:
        return ["security"]
    else:
        return []  # Only test failures and precommit are actionable
```

At iteration 7+, review findings are **completely ignored**. Only deterministic gates (tests, pre-commit) can generate work. This means the loop MUST converge — there's no source of new work being injected.

---

## 12. Fix Budgeting — Limiting N Per Node

### Why Limit

If you fix 8 things at once and tests break, you don't know which fix caused it. Fixing fewer things per iteration gives better signal.

### The Budget

```
Iteration 1-2:  max 3 findings per fix call
Iteration 3-4:  max 2 findings per fix call
Iteration 5+:   max 1 finding per fix call
```

### Chunking Strategy

When findings exceed the budget, group by file and run one CLI call per file:

```python
def fix_node(state: LoopState) -> LoopState:
    findings = state["active_findings"]
    max_per_call = fix_budget(state["iteration"])
    
    # Group by file
    by_file = group_by(findings, key=lambda f: f["file"])
    
    # Chunk: one CLI call per file, max N findings per call
    for filepath, file_findings in by_file.items():
        for chunk in batched(file_findings, max_per_call):
            fix_single_chunk(state, filepath, chunk)
    
    return state
```

### Why This Helps Context Too

Each fix call reads only the files it's modifying — not all 15 changed files. Context per fix call stays bounded even as the total change grows.

---

## 13. Council Review as 5-Perspective Subgraph

### The 5 Perspectives

```
┌─────────────────────────────────────────────────────────────────┐
│                    Council Review Subgraph                       │
│                                                                 │
│     ┌────────────────────────────────────────────────┐          │
│     │        5 parallel Claude CLI calls              │          │
│     └────────────────────────────────────────────────┘          │
│                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐ │
│  │ Security │ │Integra-  │ │ Edge     │ │ Perfor-  │ │Maint- │ │
│  │          │ │tion      │ │ Cases    │ │ mance    │ │enabil.│ │
│  │          │ │          │ │          │ │          │ │       │ │
│  │•Injection│ │•Callers  │ │•Empty    │ │•N+1     │ │•Abstr.│ │
│  │•Auth     │ │•Contracts│ │•None     │ │•Unbounded│ │•Patter│ │
│  │•Exposure │ │•Side-eff.│ │•Boundary │ │•Memory  │ │•Testab│ │
│  │•SSRF     │ │•Txn bound│ │•Concurr. │ │•I/O path│ │•Clarity│ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬───┘ │
│       │            │            │            │           │      │
│       └────────────┴────────────┴────────────┴───────────┘      │
│                              │                                   │
│                              ▼                                   │
│               ┌───────────────────────────────┐                  │
│               │   MERGE (pure Python, no LLM) │                  │
│               │                               │                  │
│               │   • Dedup by file + proximity │                  │
│               │   • Count agreements (N/5)    │                  │
│               │   • Severity = max()          │                  │
│               │   • Confidence scoring        │                  │
│               └───────────────────────────────┘                  │
│                              │                                   │
│                              ▼                                   │
│               council-review-findings.json                       │
│               (with confidence scores)                           │
└─────────────────────────────────────────────────────────────────┘
```

### Confidence Scoring (Merge Logic)

```python
def merge_council_findings(perspective_results: list[dict]) -> dict:
    # Dedup by proximity (same file, within 5 lines = same issue)
    merged = deduplicate_by_proximity(all_findings, threshold=5)
    
    # Score by agreement
    for finding in merged:
        # confidence = how many of 5 perspectives flagged this
        pass
    
    # Filter by confidence
    high_confidence = [f for f in merged if f["confidence"] >= 3]   # 3/5+ → definitely fix
    medium_confidence = [f for f in merged if f["confidence"] == 2] # 2/5  → include in triage
    # 1/5 → likely noise, exclude
    
    return {"findings": high_confidence + medium_confidence, ...}
```

### Why 5 Perspectives > 1 Big Review

```
Single reviewer:
  "Review this for everything"
  → Finds 2-3 things, misses others
  → Biased toward whatever it notices first
  → Can't go deep on security AND performance AND edge cases

5 focused reviewers:
  → Each goes DEEP on one dimension
  → Security reviewer isn't distracted by style
  → Agreement between perspectives = high signal
  → Disagreement = noise (filter out)
  → 5x cost, but WAY better coverage
```

---

## 14. Parallel Reviews

Code review and council review are independent — they can run simultaneously:

```python
def parallel_review_node(state: LoopState) -> LoopState:
    """Run code review + council review in parallel."""
    import concurrent.futures
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        code_future = executor.submit(run_code_review_subprocess, state)
        council_future = executor.submit(run_council_review_subprocess, state)
        
        code_result = code_future.result(timeout=300)
        council_result = council_future.result(timeout=300)
    
    # Both results now available for triage
    write_artifact(state, "code-review-findings.json", code_result)
    write_artifact(state, "council-review-findings.json", council_result)
    
    return state
```

Within the council review itself, all 5 perspectives also run in parallel:

```python
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [
        executor.submit(run_perspective, state, perspective)
        for perspective in COUNCIL_PERSPECTIVES
    ]
    results = [f.result(timeout=300) for f in futures]
```

Total wall-clock savings: instead of 6 sequential CLI calls (1 code + 5 council), you run 6 in parallel = ~1 CLI call worth of wall time.

---

## 15. Human Escape Hatch

### When to Stop and Page a Human

```
HARD STOPS (always halt):
├── Max iterations reached
├── Degradation detected (3 iterations of getting worse)
└── Reconciliation failed (can't satisfy both constraints)

SOFT STOPS (halt + surface for human decision):
├── Finding requires a DESIGN DECISION (not just a fix)
│   e.g., "Should this be async or sync?"
│
├── Fix would change PUBLIC API
│   e.g., adding a required parameter, changing return type
│
├── Test expectation might be WRONG (not the code)
│   e.g., test asserts old behavior that the change intentionally modifies
│
├── Security finding with UNCERTAIN severity
│   e.g., "this might be exploitable depending on deployment"
│
└── Cost threshold exceeded
    e.g., "$5 spent on this loop, continuing?"

CONFIDENCE CHECKS (ask before proceeding):
├── About to delete a file
├── About to modify >100 lines in one fix
└── Reconciliation wants to modify test expectations
```

### Detection Logic

```python
def needs_human_decision(fix_summary: dict, state: LoopState) -> str | None:
    """Returns reason string if human needed, None otherwise."""
    
    # Fix modifies public API file
    for fix in fix_summary.get("fixed", []):
        if any(f.endswith(("__init__.py", "api.py", "routes.py"))
               for f in fix.get("files_touched", [])):
            return "Fix modifies a public API file."
    
    # Fix is unusually large
    total_lines = sum(len(fix.get("lines_changed", [])) for fix in ...)
    if total_lines > 100:
        return f"Fix touches {total_lines} lines — unusually large."
    
    # Fix changes test assertions
    for fix in fix_summary.get("fixed", []):
        if "assert" in fix.get("what", "").lower() and "test" in fix.get("file", ""):
            return "Fix changes test assertions. Verify old expectation was wrong."
    
    return None
```

### The Decision File

When halted, the loop writes `NEEDS_HUMAN_DECISION.json`:

```json
{
  "reason": "Fix modifies a public API file.",
  "iteration": 4,
  "options": [
    {"key": "continue", "description": "Proceed with the automated fix"},
    {"key": "skip", "description": "Skip this finding, continue loop"},
    {"key": "abort", "description": "Stop the loop entirely"},
    {"key": "manual", "description": "I'll fix this myself, then resume"}
  ],
  "resume_command": "python langgraph_ci_loop.py --change X --resume"
}
```

Human writes `HUMAN_RESPONSE.json`, then resumes.

---

## 16. Checkpointing and Resumability

### Git History IS the Checkpoint

```
git log:
  [loop-iter-5] precommit clean, all gates pass ✅
  [loop-iter-4] fixed coverage, tests pass
  [loop-iter-3] reconciled schema.py conflict
  [loop-iter-2] fixed 3 review findings
  [loop-iter-1] initial implementation
  [pre-loop] state before automation
```

Each successful fix → git commit. Crash recovery:
1. Read `.loop/memory.json` → know which iteration
2. Check git log → know code state
3. Re-enter graph at appropriate node

### memory.json After 5 Iterations

```json
{
  "iterations_completed": 5,
  "gate_history": [
    {"iter": 1, "code_review": false, "council_review": false, "tests": true, "precommit": true},
    {"iter": 2, "code_review": false, "council_review": true, "tests": false, "precommit": true},
    {"iter": 3, "code_review": true, "council_review": true, "tests": false, "precommit": true},
    {"iter": 4, "code_review": true, "council_review": true, "tests": true, "precommit": false},
    {"iter": 5, "code_review": true, "council_review": true, "tests": true, "precommit": true}
  ],
  "file_hashes": {
    "src/schema.py": ["a3f2c1", "b7d4e2", "c1e3f5", "c1e3f5", "c1e3f5"],
    "src/normalize.py": ["d4f6a8", "d4f6a8", "e5g7b9", "e5g7b9", "e5g7b9"]
  },
  "oscillations_resolved": [],
  "summary_notes": [
    {"iteration": 1, "note": "Implemented change, 4 review findings"},
    {"iteration": 2, "note": "Fixed review findings, test broke"},
    {"iteration": 3, "note": "Fixed test, coverage low"},
    {"iteration": 4, "note": "Added tests, precommit trailing whitespace"},
    {"iteration": 5, "note": "Fixed whitespace, all gates pass"}
  ]
}
```

The `gate_history` array shows convergence — if you see oscillation there, immediate red flag.

---

## 17. The "Dumb" Middle Ground — v0

Before building the full LangGraph pipeline, a simpler approach that solves context exhaustion:

```python
def dumb_loop(change_name: str, max_iterations: int = 10):
    """
    Sequential claude sessions with manually-managed context summaries.
    ~30 lines. Solves context exhaustion. No cycle detection.
    """
    summary = f"Implementing change: {change_name}"
    
    for iteration in range(1, max_iterations + 1):
        prompt = f"""You are in iteration {iteration} of an automated fix loop.

## Prior context (summarized, ~500 tokens):
{summary}

## This iteration:
1. Run /code-review. Fix correctness/security/design only.
2. Run tests. Fix failures.
3. Check coverage >= 80%. Add tests if needed.
4. Run pre-commit. Fix errors.

## Output EXACTLY this JSON:
{{"gates": {{...}}, "all_pass": bool, "summary": "one paragraph"}}
"""
        result = call_claude(prompt, max_turns=30)
        
        if result.get("all_pass"):
            return  # Done!
        
        # Compress: keep summary under ~500 tokens
        summary += f"\n- Iter {iteration}: {result.get('summary', '?')}"
        summary = truncate_to_tokens(summary, 500)
```

### Comparison: Dumb Loop vs LangGraph

| | Dumb Loop (v0) | LangGraph (v1) |
|--|--|--|
| Lines of code | ~30 | ~1200 |
| Context exhaustion | ✅ Solved | ✅ Solved |
| Cycle detection | ❌ None | ✅ Full |
| Parallel reviews | ❌ No | ✅ Yes |
| Artifact trail | ❌ Minimal | ✅ Full .loop/ directory |
| Holistic reasoning within iteration | ✅ Claude sees everything | ⚠️ Each node isolated |
| Claude Code skills | ✅ All work naturally | ✅ All work (subprocess) |
| Resumability | ⚠️ Manual | ✅ Checkpointed |
| Dependencies | None | langgraph |

**Recommendation:** Start with the dumb loop to prove the concept. Graduate to LangGraph when you need cycle detection, parallel reviews, or better observability.

---

## 18. Testing Strategy

### The Testing Pyramid

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Unit tests (fast, no LLM, no cost):                        │
│  ├── Cycle detection logic (oscillation, recurrence, stall) │
│  ├── Degradation detection                                  │
│  ├── Diminishing authority (allowed_severities per iter)    │
│  ├── File hash computation                                  │
│  ├── State transitions (all routing functions)              │
│  ├── Memory read/write/truncation                           │
│  ├── Council merge logic (dedup, confidence)                │
│  └── Fix budget calculation                                 │
│                                                             │
│  Integration tests (mock LLM, test graph routing):          │
│  ├── Graph routes to reconciliation on cycle                │
│  ├── Graph halts on max iterations                          │
│  ├── Graph terminates when all gates pass                   │
│  ├── Parallel reviews merge correctly                       │
│  ├── Fix chunking triggers on large finding sets            │
│  ├── Human escape hatch fires on API changes                │
│  └── Degradation triggers revert                            │
│                                                             │
│  E2E tests (real LLM, expensive, run rarely):               │
│  ├── Small known-bad file → loop converges                  │
│  ├── Intentional conflict → reconciliation resolves         │
│  └── Already-clean code → 1 iteration, all pass            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Key Insight: Most Logic is Testable Without LLM Calls

The routing, cycle detection, degradation, merge, triage filtering — all pure Python. Only the "generate review findings" and "apply fix" steps need an LLM.

### Mock Pattern

```python
@patch("langgraph_ci_loop.call_claude")
def test_oscillation_triggers_reconciliation(mock_call):
    """Verify graph routes to reconciliation when oscillation detected."""
    mock_call.side_effect = [scripted_responses...]
    # Set up state with pre-existing A-B-A hash pattern
    # Invoke graph
    # Assert reconciliation node was reached
```

---

## 19. Pros and Cons

### Pros of the LangGraph Approach

| Advantage | Why it matters |
|-----------|---------------|
| **Constant context per iteration** | Each node gets fresh window — iteration 8 costs same as iteration 1 |
| **Deterministic control flow** | Graph never forgets a step or reorders |
| **Explicit state** | Serialized JSON — you know exactly where you are |
| **Resumability** | Files + git commits survive crashes |
| **Bounded retries** | max_iterations is code, not vibes |
| **Observability** | Full .loop/ artifact trail, human-readable |
| **Parallel execution** | Reviews run simultaneously |
| **Testability** | Most logic is pure Python, unit-testable |
| **Cycle detection** | Pure Python catches oscillation before it wastes iterations |
| **Token efficiency** | Each node has focused context, not full history |

### Cons / Risks

| Disadvantage | Why it matters |
|--------------|---------------|
| **Loss of holistic reasoning** | Single session has full context of everything done. Isolated nodes can't reason "I keep failing because of a design issue 3 iterations ago." |
| **Complexity** | ~1200 lines of infrastructure to build and maintain |
| **Cold-start per node** | Each node re-reads files to understand the code |
| **Triage is still LLM-shaped** | Making it a node doesn't make it more reliable, just more isolated |
| **Claude Code features need extraction** | `/code-review` is a skill — invoking it from subprocess needs prompt discipline |
| **Error recovery needs design** | What happens when fix introduces a NEW issue? (Solved via cycle detection, but adds complexity) |
| **Over-engineering if infrequent** | If you run this once a week, the ROI may not justify 1200 lines |

### What LangGraph Fixes vs What It Doesn't

| Problem | Fixed? |
|---------|--------|
| Context exhaustion | ✅ Eliminated |
| Non-deterministic ordering | ✅ Graph is code |
| Premature termination | ✅ Conditions are code |
| Infinite loops | ✅ max_iterations + cycle detection |
| State amnesia | ✅ Explicit state + memory.json |
| Triage quality | ⚠️ Still an LLM deciding |
| Fix quality | ⚠️ Still an LLM writing code |
| Holistic reasoning | ❌ Gets WORSE (tradeoff for context isolation) |

---

## 20. Open Questions

1. **What's the typical iteration count today?** If 2-3, the dumb loop is fine. If 8+, LangGraph pays for itself.

2. **How big are the changes?** 3 files/200 lines → one fix node is fine. 30 files/2000 lines → need chunking.

3. **Cost modeling** — each CLI call costs tokens. Back-of-envelope: 6 calls/iteration × ~15k tokens/call × $0.003/1k = ~$0.27/iteration. 10 iterations = ~$2.70. Worth tracking.

4. **Should the fix node be allowed to modify tests?** Currently yes (for coverage), but this risks "fix the test instead of the code" shortcuts.

5. **How to handle the implement node failing?** If `/opsx:apply` produces broken code, the loop starts from a bad foundation.

6. **Should there be a "pre-flight check"** — verify the codebase is clean BEFORE implementing?

7. **Integration with CI** — should this run locally or in CI? If CI, the human escape hatch needs a different notification mechanism (Slack? PR comment?).

---

## 21. Full Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│                          LANGGRAPH CI LOOP                               │
│                                                                         │
│  ┌──────────┐                                                           │
│  │IMPLEMENT │                                                           │
│  └────┬─────┘                                                           │
│       │                                                                 │
│       ▼                                                                 │
│  ┌──────────────────────────────────────────┐                           │
│  │         PARALLEL REVIEW                   │                          │
│  │  ┌──────────┐    ┌─────────────────────┐  │                          │
│  │  │  Code    │    │  Council (5-way)    │  │                          │
│  │  │  Review  │    │  ┌───┬───┬───┬───┐  │  │                          │
│  │  │  (1 CLI) │    │  │Sec│Int│Edg│Prf│Mnt│  │                         │
│  │  └────┬─────┘    │  └─┬─┴─┬─┴─┬─┴─┬─┴┬┘  │                         │
│  │       │          │    └───┴───┴───┴───┘   │                          │
│  │       │          │         │ merge (Py)   │                          │
│  │       │          └─────────┼──────────────┘                          │
│  └───────┼────────────────────┼──────────────┘                          │
│          │                    │                                          │
│          └────────┬───────────┘                                         │
│                   ▼                                                      │
│  ┌─────────────────────────────┐                                        │
│  │  TRIAGE                     │                                        │
│  │  • Severity filter (Py)     │                                        │
│  │  • Exclude locked (Py)      │                                        │
│  │  • Exclude failed (Py)      │◀──── diminishing authority             │
│  │  • Gray zone only → LLM     │◀──── fix budget (max N)               │
│  └──────────────┬──────────────┘                                        │
│                 │                                                        │
│                 ▼                                                        │
│  ┌─────────────────────────────┐     ┌─────────────────────────┐       │
│  │  FIX                        │     │  CYCLE DETECTOR (Py)    │       │
│  │  • Chunked by file          │────▶│  • Hash oscillation     │       │
│  │  • Max N findings/call      │     │  • Composite system hash│       │
│  │  • Git commit after each    │     │  • Finding recurrence   │       │
│  └─────────────────────────────┘     │  • Gate stall           │       │
│                                      │  • Degradation          │       │
│                                      └──────────┬──────────────┘       │
│                                                 │                       │
│                              ┌──────────────────┼──────────────┐        │
│                              │                  │              │        │
│                         no cycle           cycle found    degrading     │
│                              │                  │              │        │
│                              ▼                  ▼              ▼        │
│                     ┌─────────────┐    ┌──────────────┐  ┌─────────┐   │
│                     │  RUN TESTS  │    │ RECONCILE    │  │ REVERT  │   │
│                     │  (pytest)   │    │ (constraint  │  │ + slow  │   │
│                     │  + coverage │    │  resolution) │  │ down    │   │
│                     └──────┬──────┘    └──────┬───────┘  └────┬────┘   │
│                            │                  │               │         │
│                            ▼                  └───────┬───────┘         │
│                     ┌─────────────┐                   │                  │
│                     │ PRE-COMMIT  │                   │                  │
│                     │ (hooks)     │                   │                  │
│                     └──────┬──────┘                   │                  │
│                            │                         │                  │
│                            ▼                         │                  │
│                     ┌─────────────────────────┐      │                  │
│                     │   CHECK GATES           │◀─────┘                  │
│                     │                         │                          │
│                     │  • needs_human_check()  │──▶ 🛑 PAGE HUMAN        │
│                     │  • all pass? → END      │                          │
│                     │  • max iter? → HALT     │                          │
│                     │  • else → loop back     │                          │
│                     └──────────┬──────────────┘                          │
│                                │                                         │
│                     ┌──────────┴────────────┐                           │
│                     │          │            │                            │
│                  not done    all pass    max iter                        │
│                     │          │            │                            │
│                     │          ▼            ▼                            │
│                     │     ┌────────┐   ┌────────┐                       │
│                     │     │ DONE ✅ │   │ HALT ⛔│                       │
│                     │     └────────┘   └────────┘                       │
│                     │                                                    │
│                     └──────▶ (back to PARALLEL REVIEW)                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Summary of All Defense Mechanisms

| Layer | Mechanism | Cost | Purpose |
|-------|-----------|------|---------|
| File hash tracking | Detect A-B-A in single files | Zero (Python) | Catch oscillation |
| Composite hash | Detect system-level oscillation | Zero (Python) | Catch cross-file cycles |
| Finding fingerprints | Detect recurring issues | Zero (Python) | Catch finding-level cycles |
| Gate history | Detect no-progress stalls | Zero (Python) | Catch stalls |
| Degradation detection | Detect things getting worse | Zero (Python) | Early bail-out |
| Reconciliation node | Resolve conflicts with new framing | One LLM call | Break cycles |
| File locking | Give up gracefully on intractable files | Zero | Partial convergence |
| Diminishing authority | Narrow triage threshold over time | Zero | Guarantee convergence |
| Fix budget | Max N findings per CLI call | Zero | Better signal on regressions |
| Max iterations | Hard backstop | Zero | Prevent infinite runs |
| Human escape hatch | Page human for design decisions | Zero | Avoid bad automated decisions |
| Git checkpoints | Commit after each fix | Zero | Resumability + revert |

---

## 22. Preventing Over-Fixing (The #1 Cause of New Bugs)

The fix node is the most dangerous node in the graph. Its failure modes:

1. **Scope creep** — "While I'm here, let me also refactor this unrelated function"
2. **Over-abstraction** — "I'll make this more generic" → introduces complexity
3. **Shotgun surgery** — Changes 5 files to fix a 1-line bug
4. **Wrong layer** — Fixes the symptom (test assertion) instead of the cause (logic bug)
5. **Speculative hardening** — Adds error handling for cases that can't actually happen

### The Over-Fix Taxonomy

```
┌─────────────────────────────────────────────────────────────────┐
│                         OVER-FIX TYPES                           │
│                                                                 │
│  Type 1: SCOPE CREEP                                            │
│  Finding: "null check needed on line 42"                        │
│  Good fix: Add `if x is None: return default` on line 42       │
│  Over-fix: Refactor the entire function to use Optional types,  │
│            add a validator class, change 3 callers              │
│                                                                 │
│  Type 2: WRONG TARGET                                           │
│  Finding: "test_foo fails"                                      │
│  Good fix: Fix the logic bug that test_foo exposes              │
│  Over-fix: Change the test assertion to match the (broken) code │
│                                                                 │
│  Type 3: DEFENSIVE OVERKILL                                     │
│  Finding: "potential None dereference"                           │
│  Good fix: Guard the specific path that can be None             │
│  Over-fix: Add try/except around the entire function,           │
│            add 4 type checks, add logging, add metrics          │
│                                                                 │
│  Type 4: BUTTERFLY EFFECT                                       │
│  Finding: "function signature unclear"                           │
│  Good fix: Rename the parameter                                 │
│  Over-fix: Change the signature → break 12 callers →            │
│            "fix" those callers → introduce 3 new bugs           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Prompt Engineering to Prevent Over-Fixing

The fix node prompt must be **militantly constrained**:

```markdown
## RULES FOR FIXING (read these BEFORE touching any code):

1. **MINIMAL DIFF PRINCIPLE**: The best fix is the smallest change that 
   resolves the finding. Measure your fix in lines changed. If it's more 
   than 10 lines for a single finding, stop and reconsider.

2. **SCOPE BOUNDARY**: You are fixing EXACTLY these findings and NOTHING 
   else. Do not:
   - Refactor adjacent code
   - "Improve" code that works correctly
   - Add error handling for theoretical cases not mentioned in findings
   - Change function signatures unless the finding specifically requires it
   - Modify files not mentioned in the findings

3. **FIX THE CODE, NOT THE TEST**: When a test fails:
   - FIRST: assume the test is correct and the code is wrong
   - ONLY change the test if you can explain why the test's expectation 
     is factually wrong (not just "inconvenient for my fix")
   - If you change a test assertion, you MUST explain why in fix-summary

4. **ONE CONCERN PER FIX**: Each finding gets a focused, surgical fix.
   Do not combine fixes that touch the same code unless they're genuinely 
   the same underlying issue.

5. **NO SPECULATIVE CHANGES**: Only fix what is reported. Do not add:
   - Logging "while you're there"
   - Type annotations on unrelated parameters  
   - Documentation on unchanged functions
   - Error handling for cases not in the findings
```

### Structural Guard: The Diff Budget

After the fix node runs, **check the diff size** before proceeding:

```python
def check_fix_proportionality(state: LoopState) -> str:
    """
    If the fix is disproportionately large relative to findings,
    something went wrong. Revert and retry with stricter constraints.
    """
    fix_summary = read_artifact(state, "fix-summary.json")
    n_findings = len(state["active_findings"])
    
    # Get actual diff size
    diff = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        capture_output=True, text=True
    )
    lines_changed = parse_diff_stat(diff.stdout)  # total insertions + deletions
    
    # Heuristic: >20 lines per finding is suspicious
    if lines_changed > n_findings * 20:
        return "over_fix"  # route to revert + retry with tighter prompt
    
    # Check files touched vs files in findings
    files_in_findings = {f["file"] for f in state["active_findings"]}
    files_touched = set(fix_summary.get("files_touched", []))
    unexpected_files = files_touched - files_in_findings
    
    if unexpected_files:
        return "over_fix"  # touched files not mentioned in findings
    
    return "ok"
```

### The "Retry with Tighter Constraints" Pattern

If over-fix detected:

```
1. git checkout -- .  (revert the over-fix)
2. Re-run fix node with EXTRA constraint in prompt:
   "Your previous attempt was too broad. It touched {N} lines across 
    {M} files for {K} findings. The expected scope is ~{K*5} lines in 
    {len(files_in_findings)} files. Be more surgical this time."
3. If it over-fixes AGAIN → give up on this finding, skip it
```

### Pre/Post Assertions

```python
def fix_wrapper(state: LoopState) -> LoopState:
    """Wrap the fix node with pre/post assertions."""
    
    # PRE: snapshot what exists
    pre_hash = get_repo_hash()
    pre_files = set(get_all_tracked_files())
    
    # RUN FIX
    state = fix_node(state)
    
    # POST: verify fix was proportional
    post_files = set(get_all_tracked_files())
    deleted_files = pre_files - post_files
    new_files = post_files - pre_files
    
    # NEVER delete files in a fix (unless finding explicitly says to)
    if deleted_files:
        git_revert()
        state["halt_reason"] = f"Fix node deleted files: {deleted_files}"
        state["halted"] = True
    
    # Diff budget check
    if check_fix_proportionality(state) == "over_fix":
        git_revert()
        # Retry with tighter prompt...
    
    return state
```

---

## 23. Cost Estimation Model

### Per-Call Cost (Claude Sonnet 4.6, July 2026 pricing)

| Component | Input tokens | Output tokens | Cost |
|-----------|-------------|---------------|------|
| System prompt + project context | ~3,000 | — | $0.009 |
| File contents (per file read) | ~2,000 | — | $0.006 |
| Prompt template | ~500 | — | $0.002 |
| Response (findings/fix) | — | ~2,000 | $0.030 |
| **Total per CLI call** | **~8,000** | **~2,000** | **~$0.05** |

*Pricing: $3/M input, $15/M output (Sonnet 4.6 estimate)*

### Per-Iteration Cost

| Step | # CLI calls | Cost |
|------|------------|------|
| Code review | 1 | $0.05 |
| Council review (5 perspectives) | 5 | $0.25 |
| Triage (if LLM needed) | 0-1 | $0.00–$0.05 |
| Fix (chunked, ~2 calls avg) | 2 | $0.10 |
| Reconciliation (rare) | 0-1 | $0.00–$0.05 |
| **Total per iteration** | **~9** | **~$0.45** |

### Per-Run Estimate

| Scenario | Iterations | Estimated cost |
|----------|-----------|----------------|
| Clean implementation, minor fixes | 2-3 | $0.90–$1.35 |
| Moderate issues, converges | 5-6 | $2.25–$2.70 |
| Tricky conflicts, reconciliation needed | 8-10 | $3.60–$4.50 |
| Worst case (hits max_iterations) | 10 | ~$4.50 |

### Cost Tracking in the Graph

```python
class CostTracker:
    """Track estimated token spend across the run."""
    
    def __init__(self, budget_limit: float = 10.0):
        self.budget_limit = budget_limit
        self.calls = []
    
    def record_call(self, node_name: str, input_tokens: int, output_tokens: int):
        cost = (input_tokens * 3 / 1_000_000) + (output_tokens * 15 / 1_000_000)
        self.calls.append({"node": node_name, "cost": cost})
    
    @property
    def total_cost(self) -> float:
        return sum(c["cost"] for c in self.calls)
    
    def should_halt(self) -> bool:
        return self.total_cost >= self.budget_limit
    
    def summary(self) -> str:
        by_node = {}
        for c in self.calls:
            by_node[c["node"]] = by_node.get(c["node"], 0) + c["cost"]
        return f"Total: ${self.total_cost:.2f} | " + " | ".join(
            f"{k}: ${v:.2f}" for k, v in sorted(by_node.items(), key=lambda x: -x[1])
        )
```

### Pre-Run Estimate

Before starting, give the user a rough estimate:

```python
def estimate_run_cost(n_files: int, estimated_iterations: int = 5) -> str:
    """
    Back-of-envelope cost prediction.
    
    Assumes: 1 code-review + 5 council + 1 triage + 2 fix calls per iteration.
    """
    calls_per_iter = 1 + 5 + 1 + 2  # 9
    cost_per_call = 0.05
    total = calls_per_iter * cost_per_call * estimated_iterations
    
    # Adjust for file count (more files = more input tokens)
    file_factor = 1.0 + (n_files / 10) * 0.3  # 30% more per 10 extra files
    total *= file_factor
    
    return f"Estimated cost: ${total:.2f}–${total*1.5:.2f} for ~{estimated_iterations} iterations"
```

---

## 24. The "Dumb Loop" — Full v0 Implementation

A complete, runnable implementation of the simplest possible version:

```python
#!/usr/bin/env python3
"""
dumb_loop.py — The simplest context-isolated CI loop.
Solves context exhaustion without any framework dependency.
~50 lines of meaningful code. No cycle detection, no parallel reviews.

Usage:
    python dumb_loop.py --change unified-ci-agnostic-schema
"""

import json
import subprocess
import sys
import argparse
from pathlib import Path


def call_claude(prompt: str, max_turns: int = 30, timeout: int = 300) -> dict:
    """Call claude CLI, return parsed JSON or raw text."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--max-turns", str(max_turns)],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        parsed = json.loads(result.stdout)
        # claude --output-format json wraps in {"type":"result","result":"..."}
        inner = parsed.get("result", result.stdout)
        # Try to parse the inner content as JSON too
        if isinstance(inner, str):
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                return {"text": inner}
        return inner
    except json.JSONDecodeError:
        return {"text": result.stdout}


def truncate(text: str, max_chars: int = 2000) -> str:
    """Keep text under max_chars, preserving the most recent lines."""
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    while len("\n".join(lines)) > max_chars and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)


def run_loop(change_name: str, max_iterations: int = 10):
    """The main loop: implement, then iterate until convergence."""
    
    # Step 0: Implement
    print(f"🚀 Implementing: {change_name}")
    call_claude(
        f"Run /opsx:apply {change_name}. After completion, list the files you changed.",
        max_turns=50, timeout=600,
    )
    
    # Iteration loop
    summary = f"Change '{change_name}' has been implemented."
    
    for iteration in range(1, max_iterations + 1):
        print(f"\n{'='*60}")
        print(f"  Iteration {iteration}/{max_iterations}")
        print(f"{'='*60}")
        
        prompt = f"""You are in iteration {iteration} of an automated quality loop.

## Prior context:
{summary}

## Your job this iteration:
1. Run /code-review on the changed files. Note findings.
2. Fix any correctness, security, or design issues found. SKIP style nits.
3. Run the full test suite (pytest). Fix any failures.
4. Check coverage (pytest --cov). If changed code is below 80%, add tests.
5. Run pre-commit hooks (pre-commit run --files <changed>). Fix any failures.

## Rules:
- Be SURGICAL. Fix only what's reported. Do not refactor or improve.
- If a fix would be > 20 lines, describe it but don't apply it.
- After ALL steps, output EXACTLY this JSON (and nothing else after it):

```json
{{
  "all_pass": true/false,
  "gates": {{
    "code_review": true/false,
    "tests": true/false,
    "coverage": true/false,
    "precommit": true/false
  }},
  "findings_fixed": 0,
  "findings_skipped": 0,
  "summary": "One paragraph: what happened this iteration"
}}
```"""
        
        result = call_claude(prompt, max_turns=40, timeout=600)
        
        # Check if done
        if result.get("all_pass"):
            print(f"\n✅ All gates pass after {iteration} iteration(s)!")
            return 0
        
        # Update summary (compressed — only keeps recent iterations)
        iter_summary = result.get("summary", "No summary provided")
        gates = result.get("gates", {})
        summary = truncate(
            summary + f"\n- Iter {iteration}: {iter_summary} "
            f"[gates: {json.dumps(gates)}]",
            max_chars=2000,
        )
        
        failing = [k for k, v in gates.items() if not v]
        print(f"  Still failing: {failing}")
    
    print(f"\n⛔ Max iterations ({max_iterations}) reached without convergence.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dumb CI loop — v0")
    parser.add_argument("--change", required=True, help="Change name for /opsx:apply")
    parser.add_argument("--max-iterations", type=int, default=10)
    args = parser.parse_args()
    
    sys.exit(run_loop(args.change, args.max_iterations))
```

### When v0 Is Enough vs When to Graduate

| Condition | Stick with v0 | Graduate to LangGraph |
|-----------|---------------|----------------------|
| Iterations to converge | ≤ 4 | > 5 regularly |
| Change size | ≤ 5 files | > 10 files |
| Oscillation frequency | Never seen | Happens occasionally |
| Need to debug failures | Rarely | Often need artifact trail |
| Parallel reviews | Don't care about speed | Want 5x wall-clock reduction |
| Human oversight | Always watching | Want unattended runs |

---

## 25. OpenSpec Integration

### How This Connects to the /opsx Workflow

The CI loop is the **execution engine** for OpenSpec changes. The flow:

```
┌─────────────────────────────────────────────────────────────────┐
│  OpenSpec Workflow                                               │
│                                                                 │
│  /opsx:propose "unified CI schema"                              │
│       │                                                         │
│       ▼                                                         │
│  openspec/changes/unified-ci-agnostic-schema/                   │
│  ├── proposal.md          ← what and why                        │
│  ├── design.md            ← how (architecture decisions)        │
│  ├── specs/               ← detailed requirements               │
│  └── tasks.md             ← work breakdown                      │
│       │                                                         │
│       ▼                                                         │
│  /opsx:apply unified-ci-agnostic-schema                         │
│       │                                                         │
│       │  ← THIS IS WHERE THE CI LOOP TAKES OVER                │
│       ▼                                                         │
│  ┌─────────────────────────────────────────┐                    │
│  │  LangGraph CI Loop                       │                   │
│  │  (implement → review → fix → verify)     │                   │
│  └─────────────────────────────────────────┘                    │
│       │                                                         │
│       ▼                                                         │
│  /opsx:archive unified-ci-agnostic-schema                       │
│  (clean, tested, pre-commit-clean code)                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### The Implement Node Uses OpenSpec Context

The first node doesn't just run `/opsx:apply` blindly. It has access to the full change context:

```python
IMPLEMENT_PROMPT = """You are implementing the OpenSpec change: {change_name}

## Change Context
Read the following files for full context:
- openspec/changes/{change_name}/proposal.md (what and why)
- openspec/changes/{change_name}/design.md (architecture decisions)
- openspec/changes/{change_name}/tasks.md (work breakdown)

Then run: /opsx:apply {change_name}

This applies the change according to its specification.
"""
```

### Review Nodes Use Design Decisions as Context

The code review is more effective when it knows the **intended design**:

```python
CODE_REVIEW_PROMPT = """You are reviewing code that implements the change: {change_name}

## Design Context (what was INTENDED):
Read openspec/changes/{change_name}/design.md for the intended architecture.

## Your job:
Review the implementation against BOTH:
1. General code quality (correctness, security, design)
2. Fidelity to the design doc (does the implementation match what was specified?)

If the implementation DIVERGES from design.md, flag it — but note whether the 
divergence seems intentional (better approach discovered) or accidental (missed requirement).
"""
```

### The Full Pipeline

```
┌───────────────────────────────────────────────────────────────┐
│                                                               │
│  Human:  /opsx:propose "unified CI schema"                    │
│          /opsx:apply unified-ci-agnostic-schema               │
│                                                               │
│  Automation takes over:                                       │
│  python langgraph_ci_loop.py --change unified-ci-agnostic-schema
│                                                               │
│  Output:                                                      │
│  ├── Implemented, tested, reviewed, pre-commit-clean code     │
│  ├── .loop/ directory with full audit trail                   │
│  └── Ready for: /opsx:archive OR human final review           │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### Entry Point Variant: Direct CLI Integration

Instead of running a separate Python script, this could be a skill/hook triggered by `/opsx:apply`:

```yaml
# .claude/settings.json (hypothetical hook)
hooks:
  post-opsx-apply:
    command: "python langgraph_ci_loop.py --change $CHANGE_NAME --max-iterations 10"
    description: "Auto-run quality loop after applying a change"
```

---

## 26. Answered Open Questions

### Q1: What's the typical iteration count?

**Answer:** Based on the workflow description (code-review + council-review + tests + coverage + pre-commit), a typical run likely needs:
- **Happy path:** 2-3 iterations (implement → fix review findings → fix pre-commit)
- **Normal path:** 4-5 iterations (add test failures, coverage gaps)
- **Hard path:** 7-10 iterations (conflicting constraints, large changes)

**Decision:** Start with v0 (dumb loop). If you regularly hit 5+ iterations and see context-related failures, graduate to LangGraph.

### Q2: How big are the changes?

**Answer:** OpenSpec changes are spec-driven — they tend to be well-scoped. Typical:
- Small: 3-5 files, 100-300 lines (most common)
- Medium: 8-12 files, 500-1000 lines
- Large: 15+ files, 1000+ lines (rare, should be split)

**Decision:** Fix chunking is needed for medium+ changes. Default to 1 CLI call per file for the fix node.

### Q3: Should the fix node be allowed to modify tests?

**Answer:** Yes, but with constraints:
- **Adding new tests** → always allowed (for coverage)
- **Modifying test assertions** → requires justification in fix-summary
- **Deleting tests** → NEVER allowed by automation (human decision)

The prompt enforces: "If you change a test assertion, explain WHY the old assertion was wrong."

### Q4: How to handle the implement node failing?

**Answer:** Add a **validation step** after implementation:

```python
def post_implement_check(state: LoopState) -> str:
    """Verify implementation produced something meaningful."""
    if not state["changed_files"]:
        return "halt"  # Nothing was implemented — fail fast
    
    # Quick smoke test: does the code at least parse?
    for f in state["changed_files"]:
        if f.endswith(".py"):
            result = subprocess.run(
                ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
                capture_output=True,
            )
            if result.returncode != 0:
                return "halt"  # Produced syntactically invalid code
    
    return "continue"
```

### Q5: Should there be a pre-flight check?

**Answer:** Yes. Before implementing:

```python
def preflight_check() -> bool:
    """Verify the workspace is ready for automation."""
    checks = [
        # Clean git status
        subprocess.run(["git", "status", "--porcelain"], capture_output=True).stdout == b"",
        # Tests currently pass
        subprocess.run(["pytest", "--tb=no", "-q"], capture_output=True).returncode == 0,
        # Pre-commit is clean
        subprocess.run(["pre-commit", "run", "--all-files"], capture_output=True).returncode == 0,
    ]
    return all(checks)
```

If pre-flight fails: abort with message "Workspace is not clean. Fix existing issues before running the automation."

### Q6: Local or CI?

**Answer:** **Both**, with different configurations:

| | Local | CI |
|--|--|--|
| Human escape hatch | Terminal prompt | PR comment / Slack |
| Max iterations | 10 (interactive) | 5 (fail fast, let human take over) |
| Parallel workers | 2 (don't hog machine) | 6 (dedicated runner) |
| Cost limit | $5 (default) | $2 (per PR) |
| On halt | Wait for human input | Post report to PR, assign reviewer |

### Q7: How to prevent the "fix the test instead of the code" shortcut?

**Answer:** The fix prompt explicitly forbids it, AND we add a structural check:

```python
def check_test_modification_justified(fix_summary: dict) -> bool:
    """
    If tests were modified, verify the fix also modified source code.
    Pure test modification without source change = suspicious.
    """
    test_files = [f for f in fix_summary["files_touched"] if "test" in f]
    source_files = [f for f in fix_summary["files_touched"] if "test" not in f]
    
    if test_files and not source_files:
        # ONLY tests were modified — this is suspicious
        # Exception: if the finding was explicitly about coverage
        return False
    
    return True
```

---

## 27. Convergence Guarantees

A formal argument that the loop MUST terminate:

### Theorem: The Loop Converges in Finite Iterations

**Proof sketch:**

1. **Fix budget decreases monotonically** — by iteration 5+, max 1 finding per call
2. **Severity threshold narrows monotonically** — by iteration 7+, no review findings accepted
3. **At iteration 7+:** The only possible work generators are:
   - Test failures (deterministic gate)
   - Pre-commit failures (deterministic gate)
4. **Deterministic gates have bounded fix space** — pre-commit errors are finite and enumerable
5. **Cycle detection prevents infinite oscillation** — after 3 iterations of A-B-A, file is locked
6. **Degradation detection halts** — if things get worse for 3 iterations, stop
7. **max_iterations provides absolute backstop** — even if all else fails

**Therefore:** The loop terminates. QED.

### Convergence Timeline (Worst Case)

```
Iter 1-2:  Fix up to 3 findings each. Broad strokes.
Iter 3-4:  Fix up to 2 findings each. Narrowing.
Iter 5-6:  Fix 1 finding each. Security only from reviews.
Iter 7+:   Reviews generate NO work. Only fix test/precommit failures.
            These are deterministic — finite fix space.
Iter 10:   Hard stop regardless.

With cycle detection: oscillations caught by iter 3-4 at latest.
With degradation detection: getting-worse caught by iter 4 at latest.
```

---

## 28. Memory Architecture — What Crosses Iteration Boundaries

The key design question: **what does the fix node in iteration 5 need to know about iterations 1-4?**

### Answer: Almost Nothing (By Design)

The fix node needs:
1. What findings to fix (from THIS iteration's triage)
2. What files to read (from the findings)
3. What NOT to do again (from memory.json — compressed)

It does NOT need:
- The full review output from iteration 2
- The test output from iteration 3
- The reasoning about why something was rejected in iteration 1
- The implementation details from the original change

### The memory.json Contract

```
memory.json is NOT a conversation log.
It's a COMPRESSED wisdom store with three jobs:

1. ANTI-PATTERNS: "Don't try X, it broke Y" (failed attempts)
2. ORIENTATION: "We're in iteration 5, these files changed" (breadcrumbs)
3. DETECTION DATA: file hashes, finding fingerprints (for cycle detection)
```

Maximum useful size: ~1000 tokens. Beyond that, it should be trimmed (oldest entries dropped).

### How memory.json Stays Small

```python
def trim_memory(memory: dict, max_notes: int = 8) -> dict:
    """Keep memory bounded. Old wisdom fades."""
    # Keep only recent summary notes
    memory["summary_notes"] = memory["summary_notes"][-max_notes:]
    
    # Keep only recent gate history (for stall detection)
    memory["gate_history"] = memory["gate_history"][-5:]
    
    # File hashes: only need last 3 for oscillation detection
    for file, hashes in memory["file_hashes"].items():
        memory["file_hashes"][file] = hashes[-3:]
    
    return memory
```

---

## 29. Failure Scenarios and Recovery Matrix

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| Fix introduces new bug | Tests fail in next iteration | Fix node addresses test failure |
| Fix oscillates (A-B-A) | File hash detection | Reconciliation node |
| Fix makes everything worse | Degradation detection (3 iters) | Revert to last-good, single-fix mode |
| Fix is too broad | Diff budget check | Revert, retry with tighter prompt |
| Claude CLI times out | subprocess.TimeoutExpired | Retry once, then skip this call |
| Claude CLI returns garbage | JSON parse failure | Retry once with explicit format reminder |
| Pre-commit auto-fixes files | Pre-commit modifies staged files | Re-read changed files, re-hash |
| Tests are flaky | Same test passes/fails randomly | Track test stability (3 runs), exclude flaky |
| Implementation was wrong from start | All reviews flag fundamental issues | Halt early: "Implementation doesn't match spec" |
| Cost exceeds budget | CostTracker.should_halt() | Halt with report of what's done vs remaining |
| Human doesn't respond to decision request | NEEDS_HUMAN_DECISION.json untouched | Timeout after configurable period, auto-halt |

---

## 30. Evolution Path

### Phase 1: v0 (Now)

```
dumb_loop.py — 50 lines
- Sequential CLI calls
- Compressed summary between iterations
- No cycle detection, no parallel reviews
- Manual observation
- Good enough for proof-of-concept
```

### Phase 2: v1 (When v0 hits limits)

```
langgraph_ci_loop.py — 1200 lines
- Full graph with cycle detection
- File-based context bus
- Reconciliation node
- Git checkpointing
- Diminishing authority
- Fix budgeting
```

### Phase 3: v2 (When running frequently)

```
Additions:
- Parallel 5-perspective council review
- Cost tracking and budgets
- Human escape hatch with Slack/PR notifications
- CI integration (runs on PR push)
- Dashboard for loop observability
- Flaky test detection
- Cross-run learning (which findings recur across changes?)
```

### Phase 4: v3 (When serving a team)

```
Additions:
- Multi-change orchestration (run loops for 5 PRs simultaneously)
- Shared knowledge base (findings that recur = create lint rules)
- Auto-generation of pre-commit hooks from repeated findings
- Cost allocation per team/change
- SLA tracking (time-to-convergence)
```

---

## Summary of All Defense Mechanisms

| Layer | Mechanism | Cost | Purpose |
|-------|-----------|------|---------|
| File hash tracking | Detect A-B-A in single files | Zero (Python) | Catch oscillation |
| Composite hash | Detect system-level oscillation | Zero (Python) | Catch cross-file cycles |
| Finding fingerprints | Detect recurring issues | Zero (Python) | Catch finding-level cycles |
| Gate history | Detect no-progress stalls | Zero (Python) | Catch stalls |
| Degradation detection | Detect things getting worse | Zero (Python) | Early bail-out |
| Reconciliation node | Resolve conflicts with new framing | One LLM call | Break cycles |
| File locking | Give up gracefully on intractable files | Zero | Partial convergence |
| Diminishing authority | Narrow triage threshold over time | Zero | Guarantee convergence |
| Fix budget | Max N findings per CLI call | Zero | Better signal on regressions |
| Diff budget | Revert over-broad fixes | Zero | Prevent scope creep |
| Fix prompt constraints | Explicit "do not" rules | Zero | Prevent over-fixing |
| Pre/post assertions | Check fix proportionality | Zero | Catch structural problems |
| Test modification check | Verify test changes are justified | Zero | Prevent shortcutting |
| Max iterations | Hard backstop | Zero | Prevent infinite runs |
| Human escape hatch | Page human for design decisions | Zero | Avoid bad automated decisions |
| Git checkpoints | Commit after each fix | Zero | Resumability + revert |
| Cost tracking | Halt when budget exceeded | Zero | Prevent runaway spend |
| Pre-flight check | Verify workspace is clean first | Zero | Don't automate on broken base |

---

## 31. Review — Additional Open Points Discovered and Clarified

After a thorough review of the full document and reference implementation, the following gaps were identified and resolved:

---

### OP1: How does the fix node know which files to read?

**The gap:** The fix node receives a list of findings with file paths — but what if understanding the fix requires reading a *different* file (e.g., a base class, an import, a config file)? The prompt says "read the findings + relevant source files" but doesn't explain how "relevant" is determined.

**Answer:** Two strategies:

1. **Explicit in the finding:** The review node should include `context_files` in its output — files that are relevant to understanding the issue:
```json
{
  "file": "src/schema.py",
  "line": 42,
  "issue": "Null dereference",
  "context_files": ["src/types.py", "src/base.py"]
}
```

2. **Let the CLI figure it out:** The fix node prompt says "Read the file containing the issue. If you need to understand imports or base classes, read those too." Since Claude Code has file access, it can navigate imports naturally. The key constraint is: "Read files for understanding. Edit ONLY the file(s) mentioned in findings."

**Decision:** Strategy 2 (let Claude navigate). Adding `context_files` to findings is a v2 optimization if fix quality is poor due to missing context.

---

### OP2: What happens when pre-commit auto-fixes files?

**The gap:** Many pre-commit hooks (black, isort, trailing-whitespace) auto-fix files rather than just reporting errors. After `pre-commit run`, the working tree might have changes that weren't made by the fix node. The file hashes will be different, and the cycle detector might misinterpret this.

**Answer:** After running pre-commit, always:

```python
def run_precommit_node(state: LoopState) -> LoopState:
    result = subprocess.run(["pre-commit", "run", "--files", ...], ...)
    
    # Pre-commit may have auto-fixed files (black, isort, etc.)
    # Stage and commit those auto-fixes separately
    auto_fixed = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True
    )
    if auto_fixed.stdout.strip():
        # These are auto-fixes, not LLM fixes — commit them with a distinct message
        subprocess.run(["git", "add", "-A"])
        subprocess.run(["git", "commit", "-m", 
                       f"[loop-iter-{state['iteration']}] pre-commit auto-fix"])
        
        # Re-run pre-commit to verify it's now clean
        verify = subprocess.run(["pre-commit", "run", "--files", ...])
        state["gates"]["precommit"] = verify.returncode == 0
        
        # Update file hashes (auto-fixes are legitimate changes)
        for f in auto_fixed.stdout.strip().split("\n"):
            memory = read_memory(state)
            memory["file_hashes"].setdefault(f, []).append(compute_file_hash(f))
            write_memory(state, memory)
    
    return state
```

**Key point:** Auto-fixes should NOT be counted as "LLM fixes" in the cycle detector. They're deterministic transformations. Tag them differently in git history.

---

### OP3: How does the graph handle multi-language projects?

**The gap:** The design assumes `pytest` for tests and Python-centric tooling. What if the project has TypeScript, Go, or mixed languages?

**Answer:** The test and pre-commit nodes should be **configurable**, not hardcoded:

```python
# Configuration (read from .loop/config.json or passed as args)
LOOP_CONFIG = {
    "test_command": ["pytest", "tests/", "--cov=src", "--cov-report=json"],
    "coverage_tool": "pytest-cov",  # or "istanbul", "go-cover"
    "coverage_threshold": 80,
    "precommit_command": ["pre-commit", "run", "--files"],
    "file_extensions": [".py"],  # for AST validation in post-implement check
}
```

For the v0 dumb loop, this doesn't matter — the single Claude session handles whatever tooling the project uses. For the LangGraph version, the deterministic nodes (tests, pre-commit) need explicit configuration.

**Decision:** Add a `--config` flag that reads a `.loop/config.json`. Default to Python/pytest. Document the config schema.

---

### OP4: What if the code review and council review DISAGREE?

**The gap:** Section 13 describes merging council findings by confidence (N/5 perspectives agree). But what if code review flags something that council review says is fine, or vice versa?

**Answer:** Treat them as independent sources with equal weight:

```
Code review: "Line 42 has a null dereference" (severity: correctness)
Council review: (no finding at line 42)

→ This is a 1/6 confidence finding (1 out of 6 total reviewers flagged it)
→ Below the 2/6 threshold → EXCLUDED from triage

BUT: If code review flags it as "security" severity:
→ Security findings bypass confidence threshold
→ Always included regardless of agreement level
```

**The rule:**
- Security findings: always triage (regardless of agreement)
- Correctness: need ≥2/6 agreement (code review + 1 council perspective, or 2 council perspectives)
- Design: need ≥3/6 agreement (high bar — design is subjective)

---

### OP5: How does `--resume` actually work in the LangGraph version?

**The gap:** The doc mentions resumability and git checkpoints, but the reference implementation's `run()` function always starts from `create_initial_state()`. There's no `--resume` path.

**Answer:** Resume needs to:
1. Read `memory.json` to find the last completed iteration
2. Read the last iteration's artifacts to determine which node to re-enter
3. Reconstruct state from the artifacts

```python
def resume(change_name: str, loop_dir: str = ".loop") -> dict:
    """Resume a previously interrupted loop run."""
    memory = json.loads(Path(f"{loop_dir}/memory.json").read_text())
    last_iter = memory["iterations_completed"]
    
    # Reconstruct state from artifacts
    state = create_initial_state(change_name, loop_dir=loop_dir)
    state["iteration"] = last_iter + 1
    state["locked_files"] = [
        osc["file"] for osc in memory.get("oscillations_resolved", [])
        if not osc.get("success")
    ]
    
    # Determine which files were changed (from git)
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~" + str(last_iter), "HEAD"],
        capture_output=True, text=True,
    )
    state["changed_files"] = diff.stdout.strip().split("\n")
    
    # Re-enter the graph at code_review (top of loop)
    # LangGraph checkpointing could also handle this natively
    graph = build_graph()
    app = graph.compile()
    return app.invoke(state)
```

**For v0 (dumb loop):** Resume is simpler — just read `memory.json` for the summary and start the next iteration. The git commits ARE the checkpoints.

---

### OP6: What about findings that are CORRECT but INTENTIONAL?

**The gap:** The review might flag something like "this function doesn't validate input" — but the design doc explicitly says "validation is the caller's responsibility." The current design has no mechanism for "acknowledged, won't fix" that persists across iterations.

**Answer:** Add a `wontfix` list to state that survives across iterations:

```python
# In memory.json
{
  "wontfix": [
    {
      "fingerprint": "schema.py:42:no-validation",
      "reason": "Design doc specifies validation at caller level",
      "acknowledged_iteration": 2
    }
  ]
}
```

The triage node checks this list before accepting any finding. If a finding's fingerprint matches a `wontfix` entry, it's automatically rejected without LLM involvement.

**How it gets populated:**
- The reconciliation node can add entries ("this is intentional per design")
- The human escape hatch response can include "wontfix" as an option
- The fix prompt includes: "If a finding conflicts with the design doc, don't fix it — instead write it to `.loop/wontfix.json` with an explanation."

---

### OP7: Race condition in parallel reviews writing to the same state

**The gap:** Section 14 shows code review and council review running in parallel via ThreadPoolExecutor. But both modify `state["gates"]`. In LangGraph, state updates from parallel nodes need careful handling.

**Answer:** LangGraph handles this with **reducers**. For parallel execution within a single node (the `parallel_review_node`), there's no issue because it's a single function that orchestrates both calls and then updates state once:

```python
def parallel_review_node(state: LoopState) -> LoopState:
    # Both run in parallel threads...
    code_result = ...
    council_result = ...
    
    # ...but state is updated SEQUENTIALLY here (single thread)
    state["gates"]["code_review"] = code_result.get("pass", False)
    state["gates"]["council_review"] = council_result.get("pass", False)
    
    return state  # one atomic state update
```

The parallelism is inside the node (subprocess calls wait in threads), not between nodes. LangGraph executes nodes sequentially unless you use its native parallel branching — and even then, state merges use reducers.

**Decision:** Keep parallel execution INSIDE a single node. Don't use LangGraph's branch parallelism for this — it adds complexity without benefit since both reviews need to finish before triage can start.

---

### OP8: How to handle the case where the change is too large for a single implement node?

**The gap:** If `/opsx:apply` needs to create 30 files and 2000 lines of code, the implement node itself might run out of context or produce partial output.

**Answer:** This is actually handled by the OpenSpec layer, not the CI loop. OpenSpec's `tasks.md` breaks work into chunks. The implement node runs `/opsx:apply` which reads the tasks and implements them in sequence.

**If the implementation is still too large:**
1. The post-implement check detects partial implementation (some tasks not done)
2. The loop halts with: "Implementation incomplete — only N of M tasks completed"
3. Human decision: split the change into multiple smaller changes

**For the loop's scope:** It should NOT try to handle partial implementations by re-running the implement node. That's a fundamentally different problem. The loop's job starts AFTER implementation is complete.

**Structural guard:**
```python
def post_implement_check(state: LoopState) -> str:
    if not state["changed_files"]:
        return "halt"  # nothing happened
    if len(state["changed_files"]) > 30:
        # Very large change — warn but continue
        # (the fix chunking handles this downstream)
        log(f"⚠️ Large change: {len(state['changed_files'])} files")
    return "continue"
```

---

### OP9: What's the interaction between `--output-format json` and Claude Code skills?

**The gap:** The `call_claude` function uses `--output-format json`, which returns structured output. But when Claude Code runs skills like `/code-review`, the skill output is part of the conversation — it's not directly returned as JSON to the subprocess caller.

**Answer:** This is the most practical challenge of the subprocess approach. Two strategies:

**Strategy A: Don't use skills — replicate the logic in the prompt.**

Instead of saying "run /code-review", the prompt says "review the code yourself with these criteria." This is what the current prompts do — they're self-contained review instructions, not skill invocations.

**Strategy B: Use skills but extract results via file output.**

The prompt says: "Run /code-review. Then summarize the findings into `.loop/iteration-N/code-review-findings.json` with this schema: ..."

Claude runs the skill, sees its output in-context, then writes the structured summary to a file. The Python code reads the file, not stdout.

**Decision:** Strategy B for the implement node (it NEEDS `/opsx:apply`). Strategy A for review/fix nodes (replicate the logic in the prompt for more control over output format). This gives the best of both: OpenSpec integration where needed, structured output where needed.

**The implement node is special:**
```python
# Implement uses skills (needs /opsx:apply)
call_claude(
    f"Run /opsx:apply {change_name}. Write implementation.json when done.",
    allowed_tools=["Read", "Write", "Edit", "Bash", "Skill"],  # Skill needed!
    max_turns=50,
)

# Review/fix nodes DON'T use skills (more controllable)
call_claude(
    "Review these files for correctness issues...",
    allowed_tools=["Read", "Write", "Bash"],  # No Skill — self-contained prompt
    max_turns=20,
)
```

---

### Summary of New Points

| # | Point | Status |
|---|-------|--------|
| OP1 | Fix node file discovery | ✅ Let Claude navigate imports; constrain edits to finding files |
| OP2 | Pre-commit auto-fixes | ✅ Commit separately, don't count in cycle detection |
| OP3 | Multi-language support | ✅ Configurable via `.loop/config.json` |
| OP4 | Code review vs council disagreement | ✅ Confidence threshold with security override |
| OP5 | Resume implementation | ✅ Read memory.json + git history, re-enter at code_review |
| OP6 | Intentional findings ("won't fix") | ✅ Persistent wontfix list in memory.json |
| OP7 | Parallel state race conditions | ✅ Parallelism inside single node, not between nodes |
| OP8 | Change too large for implement | ✅ Out of scope — handled by OpenSpec layer; loop halts on incomplete |
| OP9 | Skills vs structured output | ✅ Skills for implement only; self-contained prompts for review/fix |

---

## File Reference

| File | Purpose |
|------|---------|
| `DESIGN_EXPLORATION.md` | This document — full design exploration |
| `langgraph_ci_loop.py` | Reference implementation (LangGraph v1, ~1200 lines) |
| `dumb_loop.py` | Runnable v0 implementation (~210 lines) |

---

*End of exploration document.*
