"""
LangGraph CI Loop — Architectural Design Document
===================================================

A LangGraph state machine that orchestrates:
  implement → code-review → council-review → triage → fix → test → pre-commit

Each node runs as a subprocess call to `claude` CLI, getting a FRESH context window.
State passes between nodes via structured JSON files on disk (not conversation history).
This eliminates context window exhaustion across iterations.

Key features:
  - File-based context bus (.loop/iteration-N/*.json)
  - Cycle detection (oscillation, recurrence, stalls)
  - Reconciliation node for conflicting constraints
  - Diminishing authority (triage threshold narrows with iteration)
  - Hard stop with surfaced report

Dependencies:
  - langgraph
  - langchain-core (for state/typing)
  - subprocess (stdlib) for claude CLI calls

Usage:
  python langgraph_ci_loop.py --change unified-ci-agnostic-schema --max-iterations 10
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

# =============================================================================
# STATE DEFINITION
# =============================================================================


class Finding(TypedDict):
    """A single review finding."""
    file: str
    line: int
    issue: str
    severity: str  # "correctness" | "security" | "design" | "style"
    fingerprint: str  # deterministic ID for dedup/cycle detection


class GateStatus(TypedDict):
    """Status of all quality gates for one iteration."""
    code_review: bool
    council_review: bool
    tests: bool
    coverage: bool
    precommit: bool


class LoopState(TypedDict):
    """The complete graph state. Kept minimal — heavy context lives in files."""
    # Identity
    change_name: str
    loop_dir: str  # absolute path to .loop/

    # Iteration tracking
    iteration: int
    max_iterations: int

    # What's being worked on
    changed_files: list[str]

    # Current gate status
    gates: GateStatus

    # Active work items (for routing, not for LLM context)
    active_findings: list[Finding]
    test_failures: list[str]
    precommit_errors: list[str]

    # Cycle detection
    cycle_detected: bool
    cycle_type: str  # "" | "oscillation" | "recurrence" | "stall"
    locked_files: list[str]

    # Termination
    all_gates_pass: bool
    halted: bool
    halt_reason: str


# =============================================================================
# FILE-BASED CONTEXT BUS
# =============================================================================


def iter_dir(state: LoopState) -> Path:
    """Get the directory for the current iteration's artifacts."""
    d = Path(state["loop_dir"]) / f"iteration-{state['iteration']}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_artifact(state: LoopState, name: str, data: Any) -> Path:
    """Write a JSON artifact for the current iteration."""
    path = iter_dir(state) / name
    path.write_text(json.dumps(data, indent=2))
    return path


def read_artifact(state: LoopState, name: str, iteration: int | None = None) -> Any:
    """Read a JSON artifact. Defaults to current iteration."""
    it = iteration if iteration is not None else state["iteration"]
    path = Path(state["loop_dir"]) / f"iteration-{it}" / name
    if path.exists():
        return json.loads(path.read_text())
    return None


def read_memory(state: LoopState) -> dict:
    """Read the append-only memory file."""
    path = Path(state["loop_dir"]) / "memory.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "iterations_completed": 0,
        "gate_history": [],
        "file_hashes": {},  # file -> [hash_per_iteration]
        "finding_history": [],  # [set_of_fingerprints_per_iteration]
        "oscillations_resolved": [],
        "summary_notes": [],
    }


def write_memory(state: LoopState, memory: dict) -> None:
    """Write the memory file."""
    path = Path(state["loop_dir"]) / "memory.json"
    path.write_text(json.dumps(memory, indent=2))


def compute_file_hash(filepath: str) -> str:
    """SHA256 of a file's contents."""
    try:
        content = Path(filepath).read_bytes()
        return hashlib.sha256(content).hexdigest()[:12]
    except FileNotFoundError:
        return "DELETED"


# =============================================================================
# CLAUDE CLI SUBPROCESS INTERFACE
# =============================================================================


def call_claude(
    prompt: str,
    allowed_tools: list[str] | None = None,
    max_turns: int = 10,
    timeout_seconds: int = 300,
) -> dict:
    """
    Call claude CLI as a subprocess.

    Returns parsed JSON if output is JSON, otherwise {"text": raw_output}.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json"]

    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    cmd.extend(["--max-turns", str(max_turns)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "text": ""}

    if result.returncode != 0:
        return {"error": f"exit_{result.returncode}", "text": result.stderr}

    # Try to parse as JSON (claude --output-format json returns structured output)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"text": result.stdout}


# =============================================================================
# TRIAGE THRESHOLD — DIMINISHING AUTHORITY
# =============================================================================


def allowed_severities(iteration: int) -> list[str]:
    """
    As iterations increase, we become stricter about what we'll fix.
    This guarantees eventual convergence — at some point we stop accepting work.
    """
    if iteration <= 2:
        return ["correctness", "security", "design"]
    elif iteration <= 4:
        return ["correctness", "security"]
    elif iteration <= 6:
        return ["security"]
    else:
        # Only test failures and precommit errors are actionable.
        # Review findings are ignored entirely.
        return []


# =============================================================================
# PROMPT TEMPLATES
# =============================================================================

IMPLEMENT_PROMPT = """You are implementing a change named "{change_name}".

Run the command: /opsx:apply {change_name}

This will apply the change according to its specification.
After implementation, write a JSON file at {output_path} with this schema:

{{
  "files_changed": ["list of files created or modified"],
  "summary": "one paragraph describing what was implemented"
}}
"""

CODE_REVIEW_PROMPT = """You are a code reviewer. Review the following changed files for issues.

Changed files: {changed_files}

ONLY report issues that are:
- Correctness bugs (logic errors, off-by-ones, null dereferences, race conditions)
- Security vulnerabilities (injection, auth bypass, data exposure)
- Design problems (wrong abstraction, coupling issues, interface misuse)

DO NOT report:
- Style issues (formatting, naming preferences, comment style)
- Minor nits that don't affect behavior

{locked_files_instruction}

Read each file listed above, then write your findings to {output_path} as JSON:

{{
  "findings": [
    {{
      "file": "path/to/file.py",
      "line": 42,
      "issue": "description of the problem",
      "severity": "correctness|security|design",
      "fingerprint": "file.py:42:short-slug"
    }}
  ],
  "pass": true/false
}}

If no issues found, set "findings" to [] and "pass" to true.
"""

COUNCIL_REVIEW_PROMPT = """You are an independent second reviewer. You have NOT seen the first review.
Your role is to catch issues the first reviewer might have missed, from a different angle.

Changed files: {changed_files}

Approach this from these angles:
1. Integration: do these changes work correctly with the rest of the system?
2. Edge cases: what inputs or states could break this?
3. Contracts: are interfaces/types/APIs used correctly?

DO NOT report style issues. Only report correctness, security, or design problems.

{locked_files_instruction}

Write findings to {output_path} as JSON (same schema as code review):

{{
  "findings": [...],
  "pass": true/false
}}
"""

TRIAGE_PROMPT = """You are triaging review findings to decide which are worth fixing.

## Current iteration: {iteration}
## Allowed severities this iteration: {allowed_severities}

## Findings from code review:
{code_review_findings}

## Findings from council review:
{council_review_findings}

## Previously attempted fixes that caused regressions (DO NOT retry these):
{failed_attempts}

For each finding, decide:
- ACCEPT: worth fixing (matches allowed severity, not previously failed)
- REJECT: skip it (style nit, below threshold, or known to cause regressions)

Write your triage to {output_path}:

{{
  "accepted": [
    {{
      "file": "...",
      "line": N,
      "issue": "...",
      "severity": "...",
      "fingerprint": "...",
      "fix_hint": "brief suggestion for how to fix"
    }}
  ],
  "rejected": [
    {{
      "fingerprint": "...",
      "reason": "why it was rejected"
    }}
  ]
}}
"""

FIX_PROMPT = """You are fixing specific issues in the codebase.

## Iteration: {iteration}
## Issues to fix:
{findings_json}

## Context from prior attempts (if any):
{memory_context}

## Rules:
1. Fix ONLY the listed issues. Do not refactor or "improve" anything else.
2. If a file is LOCKED, do not modify it: {locked_files}
3. After fixing, write a summary to {output_path}:

{{
  "fixed": [
    {{
      "file": "path/to/file.py",
      "lines_changed": [42, 67],
      "what": "description of the fix",
      "why": "which finding this addresses (include fingerprint)",
      "test_hint": "what should be tested to verify this fix"
    }}
  ],
  "files_touched": ["all files modified"],
  "suggested_test_focus": ["specific test functions or areas to run"]
}}
"""

# =============================================================================
# RECONCILIATION PROMPT — THE KEY DIFFERENTIATOR
# =============================================================================

RECONCILIATION_PROMPT = """You are a RECONCILIATION agent. You are NOT a normal fixer.

## The Problem
The automated fix loop has detected a CYCLE — the same issue keeps being fixed and
then re-introduced because two constraints are in conflict.

## Conflict Details

### What oscillated:
{oscillation_description}

### History of attempts:
{attempt_history}

### Constraint A (from review):
{review_constraint}

### Constraint B (from tests / other gate):
{test_constraint}

## Your Job

You must find a THIRD OPTION that satisfies BOTH constraints simultaneously.
Do not simply pick one side. The previous iterations already proved that satisfying
only one constraint breaks the other.

Common resolution patterns:
1. **Widen the type** — make it Optional/Union so both cases are handled
2. **Split the path** — different behavior for different callers
3. **Update the test** — if the test expectation is wrong (outdated), fix the test
4. **Update the review concern** — if the review is asking for something the design
   intentionally doesn't do, document the decision and mark the finding as "won't fix"
5. **Extract a configuration** — make the conflicting behavior configurable
6. **Add a guard clause** — handle the edge case without changing the main path

## Rules
1. You MUST modify files to resolve this. Status quo = infinite loop.
2. You may modify source code AND test code if needed.
3. Locked files: {locked_files} — do not touch these.
4. After resolving, write {output_path}:

{{
  "resolution_strategy": "which pattern you used (1-6 above or novel)",
  "explanation": "why this satisfies both constraints",
  "files_modified": ["list"],
  "constraint_a_satisfied": true/false,
  "constraint_b_satisfied": true/false,
  "compromise_made": "what tradeoff was accepted, if any"
}}

## IMPORTANT
If you cannot find a resolution that satisfies both constraints, explain why
in your output and set both satisfied fields to false. The loop will then LOCK
the file and surface it for human review.
"""

HALT_REPORT_PROMPT = """Generate a final status report.

## Loop completed after {iteration} iterations.
## Final gate status: {gates}
## Unresolved conflicts: {unresolved}
## Memory summary: {memory_summary}

Write a human-readable report to {output_path} summarizing:
1. What was accomplished
2. What remains unresolved (and why)
3. Recommended next steps for a human
"""


# =============================================================================
# NODE IMPLEMENTATIONS
# =============================================================================


def implement_node(state: LoopState) -> LoopState:
    """Initial implementation via /opsx:apply."""
    output_path = iter_dir(state) / "implementation.json"

    prompt = IMPLEMENT_PROMPT.format(
        change_name=state["change_name"],
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Skill"],
        max_turns=50,
        timeout_seconds=600,
    )

    # Read what was produced
    result = read_artifact(state, "implementation.json")
    if result:
        state["changed_files"] = result.get("files_changed", [])

    # Snapshot initial file hashes
    memory = read_memory(state)
    for f in state["changed_files"]:
        memory["file_hashes"][f] = [compute_file_hash(f)]
    write_memory(state, memory)

    return state


def code_review_node(state: LoopState) -> LoopState:
    """Run code review via claude CLI."""
    output_path = iter_dir(state) / "code-review-findings.json"

    locked_instruction = ""
    if state["locked_files"]:
        locked_instruction = (
            f"LOCKED FILES (do not report issues in these): {state['locked_files']}"
        )

    prompt = CODE_REVIEW_PROMPT.format(
        changed_files=json.dumps(state["changed_files"]),
        locked_files_instruction=locked_instruction,
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write", "Bash"],
        max_turns=20,
        timeout_seconds=300,
    )

    result = read_artifact(state, "code-review-findings.json")
    if result:
        state["gates"]["code_review"] = result.get("pass", False)
    else:
        # If no output, assume pass (reviewer had nothing to say)
        state["gates"]["code_review"] = True

    return state


def council_review_node(state: LoopState) -> LoopState:
    """Run independent second review."""
    output_path = iter_dir(state) / "council-review-findings.json"

    locked_instruction = ""
    if state["locked_files"]:
        locked_instruction = (
            f"LOCKED FILES (do not report issues in these): {state['locked_files']}"
        )

    prompt = COUNCIL_REVIEW_PROMPT.format(
        changed_files=json.dumps(state["changed_files"]),
        locked_files_instruction=locked_instruction,
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write", "Bash"],
        max_turns=20,
        timeout_seconds=300,
    )

    result = read_artifact(state, "council-review-findings.json")
    if result:
        state["gates"]["council_review"] = result.get("pass", False)
    else:
        state["gates"]["council_review"] = True

    return state


def triage_node(state: LoopState) -> LoopState:
    """Triage findings — filter by severity threshold and prior failures."""
    output_path = iter_dir(state) / "triage.json"

    # Load review outputs
    code_findings = read_artifact(state, "code-review-findings.json") or {"findings": []}
    council_findings = read_artifact(state, "council-review-findings.json") or {"findings": []}

    # Get prior failed attempts from memory
    memory = read_memory(state)
    failed_fingerprints = set()
    for osc in memory.get("oscillations_resolved", []):
        failed_fingerprints.add(osc.get("fingerprint", ""))

    # If we're past the severity threshold, skip LLM triage entirely
    allowed = allowed_severities(state["iteration"])
    if not allowed:
        # No severities accepted — skip all review findings
        write_artifact(state, "triage.json", {"accepted": [], "rejected": []})
        state["active_findings"] = []
        return state

    prompt = TRIAGE_PROMPT.format(
        iteration=state["iteration"],
        allowed_severities=json.dumps(allowed),
        code_review_findings=json.dumps(code_findings["findings"], indent=2),
        council_review_findings=json.dumps(council_findings["findings"], indent=2),
        failed_attempts=json.dumps(list(failed_fingerprints)),
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write"],
        max_turns=5,
        timeout_seconds=120,
    )

    result = read_artifact(state, "triage.json")
    if result:
        state["active_findings"] = result.get("accepted", [])
    else:
        state["active_findings"] = []

    return state


def fix_node(state: LoopState) -> LoopState:
    """Apply fixes for accepted findings."""
    output_path = iter_dir(state) / "fix-summary.json"

    # Build memory context (compressed history of prior attempts)
    memory = read_memory(state)
    memory_lines = []
    for note in memory.get("summary_notes", [])[-5:]:  # last 5 notes max
        memory_lines.append(f"- Iteration {note['iteration']}: {note['note']}")
    memory_context = "\n".join(memory_lines) if memory_lines else "(first iteration)"

    prompt = FIX_PROMPT.format(
        iteration=state["iteration"],
        findings_json=json.dumps(state["active_findings"], indent=2),
        memory_context=memory_context,
        locked_files=json.dumps(state["locked_files"]),
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write", "Edit", "Bash"],
        max_turns=30,
        timeout_seconds=300,
    )

    # Update changed_files with anything new the fix touched
    result = read_artifact(state, "fix-summary.json")
    if result:
        new_files = result.get("files_touched", [])
        all_files = set(state["changed_files"]) | set(new_files)
        state["changed_files"] = sorted(all_files)

    # Record file hashes after fix
    for f in state["changed_files"]:
        h = compute_file_hash(f)
        memory["file_hashes"].setdefault(f, []).append(h)
    write_memory(state, memory)

    return state


def run_tests_node(state: LoopState) -> LoopState:
    """Run pytest + coverage. Deterministic — no LLM needed for execution."""
    output_path = iter_dir(state) / "test-results.json"

    # Run pytest with coverage
    result = subprocess.run(
        [
            "pytest", "tests/",
            "--tb=short",
            "--cov=src",
            "--cov-report=json:coverage.json",
            "--cov-report=term-missing",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    tests_pass = result.returncode == 0
    state["gates"]["tests"] = tests_pass

    # Parse failures
    failures = []
    if not tests_pass:
        for line in result.stdout.split("\n"):
            if "FAILED" in line:
                failures.append(line.strip())
    state["test_failures"] = failures

    # Check coverage
    coverage_ok = False
    try:
        cov_data = json.loads(Path("coverage.json").read_text())
        # Check coverage for changed files specifically
        total_covered = 0
        total_statements = 0
        for f in state["changed_files"]:
            file_cov = cov_data.get("files", {}).get(f, {})
            total_covered += file_cov.get("summary", {}).get("covered_lines", 0)
            total_statements += file_cov.get("summary", {}).get("num_statements", 1)
        coverage_pct = (total_covered / max(total_statements, 1)) * 100
        coverage_ok = coverage_pct >= 80.0
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        coverage_pct = 0.0

    state["gates"]["coverage"] = coverage_ok

    # Write structured results
    test_results = {
        "pass": tests_pass,
        "failures": failures,
        "coverage_percent": round(coverage_pct, 1),
        "coverage_ok": coverage_ok,
        "stdout": result.stdout[-2000:],  # last 2k chars for context
    }
    write_artifact(state, "test-results.json", test_results)

    return state


def run_precommit_node(state: LoopState) -> LoopState:
    """Run pre-commit hooks. Deterministic — no LLM needed."""
    output_path = iter_dir(state) / "precommit-results.json"

    result = subprocess.run(
        ["pre-commit", "run", "--files"] + state["changed_files"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    precommit_clean = result.returncode == 0
    state["gates"]["precommit"] = precommit_clean

    errors = []
    if not precommit_clean:
        # pre-commit outputs which hooks failed
        errors = [
            line.strip()
            for line in result.stdout.split("\n")
            if "Failed" in line or "error" in line.lower()
        ]
    state["precommit_errors"] = errors

    precommit_results = {
        "clean": precommit_clean,
        "errors": errors,
        "stdout": result.stdout[-2000:],
    }
    write_artifact(state, "precommit-results.json", precommit_results)

    return state


# =============================================================================
# CYCLE DETECTION — PURE PYTHON, NO LLM
# =============================================================================


def detect_cycles(state: LoopState) -> LoopState:
    """
    Check for oscillation patterns. This is deterministic Python logic.

    Detection strategies:
    1. File hash oscillation: A-B-A pattern (file reverts to prior state)
    2. Finding recurrence: same fingerprint appears after being "fixed"
    3. Gate stall: identical gate status for 2+ consecutive iterations
    """
    memory = read_memory(state)
    state["cycle_detected"] = False
    state["cycle_type"] = ""

    # --- Strategy 1: File hash oscillation ---
    for filepath, hashes in memory.get("file_hashes", {}).items():
        if len(hashes) >= 3:
            # A-B-A pattern: current == two-ago
            if hashes[-1] == hashes[-3] and hashes[-1] != hashes[-2]:
                state["cycle_detected"] = True
                state["cycle_type"] = "oscillation"
                # Record what's oscillating for the reconciliation node
                write_artifact(state, "cycle-info.json", {
                    "type": "oscillation",
                    "file": filepath,
                    "pattern": f"Hash went {hashes[-3][:6]}→{hashes[-2][:6]}→{hashes[-1][:6]} (A-B-A)",
                    "iterations_involved": list(range(
                        max(1, state["iteration"] - 2),
                        state["iteration"] + 1
                    )),
                })
                return state

    # --- Strategy 2: Finding recurrence ---
    # Check if any current finding was present 2 iterations ago (after being "fixed")
    current_fingerprints = {f["fingerprint"] for f in state["active_findings"]}
    if state["iteration"] >= 3:
        old_findings_data = read_artifact(
            state, "triage.json", iteration=state["iteration"] - 2
        )
        if old_findings_data:
            old_fingerprints = {
                f["fingerprint"] for f in old_findings_data.get("accepted", [])
            }
            recurring = current_fingerprints & old_fingerprints
            if recurring:
                state["cycle_detected"] = True
                state["cycle_type"] = "recurrence"
                write_artifact(state, "cycle-info.json", {
                    "type": "recurrence",
                    "recurring_fingerprints": list(recurring),
                    "message": "These findings were fixed but came back",
                })
                return state

    # --- Strategy 3: Gate stall ---
    gate_history = memory.get("gate_history", [])
    if len(gate_history) >= 2:
        if gate_history[-1] == gate_history[-2]:
            # Same gate status two iterations in a row — no progress
            state["cycle_detected"] = True
            state["cycle_type"] = "stall"
            write_artifact(state, "cycle-info.json", {
                "type": "stall",
                "repeated_status": gate_history[-1],
                "message": "No progress — identical gate status for 2 consecutive iterations",
            })
            return state

    return state


# =============================================================================
# RECONCILIATION NODE
# =============================================================================


def reconcile_node(state: LoopState) -> LoopState:
    """
    Resolve a detected cycle by reframing it as constraint resolution.

    This node gets a DIFFERENT system prompt than the fix node. Instead of
    "fix this bug," it says "these two constraints conflict — find a third
    option that satisfies both."
    """
    output_path = iter_dir(state) / "reconciliation.json"

    # Load cycle info
    cycle_info = read_artifact(state, "cycle-info.json") or {}
    memory = read_memory(state)

    # Build the oscillation description
    oscillation_desc = json.dumps(cycle_info, indent=2)

    # Build attempt history from memory
    attempt_history_lines = []
    for note in memory.get("summary_notes", []):
        attempt_history_lines.append(
            f"Iteration {note['iteration']}: {note['note']}"
        )
    attempt_history = "\n".join(attempt_history_lines[-6:])  # last 6

    # Determine what the conflicting constraints are
    # Look at the most recent review finding vs test failure for this file
    review_constraint = "Unknown — check code-review-findings.json"
    test_constraint = "Unknown — check test-results.json"

    if cycle_info.get("type") == "oscillation":
        target_file = cycle_info.get("file", "")
        # Find relevant review finding
        cr = read_artifact(state, "code-review-findings.json") or {"findings": []}
        for finding in cr.get("findings", []):
            if finding.get("file") == target_file:
                review_constraint = f"File: {target_file}\nIssue: {finding['issue']}"
                break
        # Find relevant test failure
        tr = read_artifact(state, "test-results.json") or {}
        test_failures = tr.get("failures", [])
        if test_failures:
            test_constraint = "\n".join(test_failures[:3])

    prompt = RECONCILIATION_PROMPT.format(
        oscillation_description=oscillation_desc,
        attempt_history=attempt_history,
        review_constraint=review_constraint,
        test_constraint=test_constraint,
        locked_files=json.dumps(state["locked_files"]),
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write", "Edit", "Bash"],
        max_turns=30,
        timeout_seconds=300,
    )

    # Check if reconciliation succeeded
    result = read_artifact(state, "reconciliation.json")
    if result:
        both_satisfied = (
            result.get("constraint_a_satisfied", False)
            and result.get("constraint_b_satisfied", False)
        )
        if not both_satisfied:
            # Reconciliation failed — lock the file
            if cycle_info.get("file"):
                state["locked_files"].append(cycle_info["file"])

        # Record in memory
        memory["oscillations_resolved"].append({
            "iteration": state["iteration"],
            "cycle_info": cycle_info,
            "resolution": result.get("resolution_strategy", "unknown"),
            "success": both_satisfied,
        })
        write_memory(state, memory)

    # Reset cycle detection flags
    state["cycle_detected"] = False
    state["cycle_type"] = ""

    return state


# =============================================================================
# GATE CHECKING & TERMINATION
# =============================================================================


def check_gates_node(state: LoopState) -> LoopState:
    """Evaluate all gates and update memory."""
    gates = state["gates"]

    state["all_gates_pass"] = all([
        gates["code_review"],
        gates["council_review"],
        gates["tests"],
        gates["coverage"],
        gates["precommit"],
    ])

    # Record gate status in memory
    memory = read_memory(state)
    memory["gate_history"].append({
        "iteration": state["iteration"],
        "code_review": gates["code_review"],
        "council_review": gates["council_review"],
        "tests": gates["tests"],
        "coverage": gates["coverage"],
        "precommit": gates["precommit"],
    })
    memory["iterations_completed"] = state["iteration"]

    # Add a summary note
    passing = [k for k, v in gates.items() if v]
    failing = [k for k, v in gates.items() if not v]
    memory["summary_notes"].append({
        "iteration": state["iteration"],
        "note": f"Passing: {passing}. Failing: {failing}.",
    })

    write_memory(state, memory)

    # Increment iteration for next loop
    if not state["all_gates_pass"]:
        state["iteration"] += 1

    return state


def halt_node(state: LoopState) -> LoopState:
    """Hard stop — generate a human-readable report of what happened."""
    output_path = iter_dir(state) / "halt-report.json"
    memory = read_memory(state)

    prompt = HALT_REPORT_PROMPT.format(
        iteration=state["iteration"],
        gates=json.dumps(state["gates"], indent=2),
        unresolved=json.dumps({
            "locked_files": state["locked_files"],
            "active_findings": state["active_findings"][:5],
            "test_failures": state["test_failures"][:5],
        }, indent=2),
        memory_summary=json.dumps(memory["summary_notes"][-5:], indent=2),
        output_path=output_path,
    )

    call_claude(
        prompt,
        allowed_tools=["Read", "Write"],
        max_turns=5,
        timeout_seconds=60,
    )

    state["halted"] = True
    state["halt_reason"] = (
        f"Max iterations ({state['max_iterations']}) reached. "
        f"Gates still failing: {[k for k, v in state['gates'].items() if not v]}"
    )

    return state


# =============================================================================
# ROUTING FUNCTIONS (CONDITIONAL EDGES)
# =============================================================================


def route_after_triage(state: LoopState) -> str:
    """After triage: if findings to fix → fix. Otherwise → tests."""
    if state["active_findings"]:
        return "fix"
    return "run_tests"


def route_after_fix(state: LoopState) -> str:
    """After fix: check for cycles before proceeding to tests."""
    return "detect_cycles"


def route_after_cycle_check(state: LoopState) -> str:
    """After cycle detection: reconcile if cycle found, else tests."""
    if state["cycle_detected"]:
        return "reconcile"
    return "run_tests"


def route_after_tests(state: LoopState) -> str:
    """After tests: if tests fail, try fixing. Otherwise → precommit."""
    if not state["gates"]["tests"]:
        # Inject test failures as findings for the fix node
        state["active_findings"] = [
            {
                "file": "tests/",
                "line": 0,
                "issue": f"Test failure: {f}",
                "severity": "correctness",
                "fingerprint": f"test-failure:{f}",
            }
            for f in state["test_failures"][:5]
        ]
        return "fix"
    if not state["gates"]["coverage"]:
        # Need more tests — route to fix node with coverage findings
        state["active_findings"] = [{
            "file": "tests/",
            "line": 0,
            "issue": "Coverage below 80% for changed files. Add tests.",
            "severity": "correctness",
            "fingerprint": "coverage:below-threshold",
        }]
        return "fix"
    return "run_precommit"


def route_after_precommit(state: LoopState) -> str:
    """After precommit: if errors, fix them. Otherwise → check gates."""
    if not state["gates"]["precommit"]:
        state["active_findings"] = [
            {
                "file": err.split(":")[0] if ":" in err else "unknown",
                "line": 0,
                "issue": f"Pre-commit hook failure: {err}",
                "severity": "correctness",
                "fingerprint": f"precommit:{err[:40]}",
            }
            for err in state["precommit_errors"][:5]
        ]
        return "fix"
    return "check_gates"


def route_after_gates(state: LoopState) -> str:
    """After gate check: done if all pass, else check iteration limit."""
    if state["all_gates_pass"]:
        return "done"
    if state["iteration"] > state["max_iterations"]:
        return "halt"
    # Loop back to reviews for another iteration
    return "code_review"


# =============================================================================
# GRAPH DEFINITION
# =============================================================================


def build_graph() -> StateGraph:
    """
    Construct the full LangGraph state machine.

    Flow:
      implement → code_review → council_review → triage
        → fix → cycle_check → [reconcile?] → tests → precommit → check_gates
        → (loop back to code_review OR done OR halt)
    """
    graph = StateGraph(LoopState)

    # Add all nodes
    graph.add_node("implement", implement_node)
    graph.add_node("code_review", code_review_node)
    graph.add_node("council_review", council_review_node)
    graph.add_node("triage", triage_node)
    graph.add_node("fix", fix_node)
    graph.add_node("detect_cycles", detect_cycles)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("run_precommit", run_precommit_node)
    graph.add_node("check_gates", check_gates_node)
    graph.add_node("halt", halt_node)

    # Define edges
    graph.set_entry_point("implement")
    graph.add_edge("implement", "code_review")
    graph.add_edge("code_review", "council_review")
    graph.add_edge("council_review", "triage")

    # After triage: fix or skip to tests
    graph.add_conditional_edges("triage", route_after_triage, {
        "fix": "fix",
        "run_tests": "run_tests",
    })

    # After fix: always check for cycles
    graph.add_edge("fix", "detect_cycles")

    # After cycle check: reconcile or proceed
    graph.add_conditional_edges("detect_cycles", route_after_cycle_check, {
        "reconcile": "reconcile",
        "run_tests": "run_tests",
    })

    # After reconciliation: always re-run tests
    graph.add_edge("reconcile", "run_tests")

    # After tests: fix failures, add coverage, or proceed
    graph.add_conditional_edges("run_tests", route_after_tests, {
        "fix": "fix",
        "run_precommit": "run_precommit",
    })

    # After precommit: fix errors or check gates
    graph.add_conditional_edges("run_precommit", route_after_precommit, {
        "fix": "fix",
        "check_gates": "check_gates",
    })

    # After gate check: loop, halt, or done
    graph.add_conditional_edges("check_gates", route_after_gates, {
        "code_review": "code_review",
        "halt": "halt",
        "done": END,
    })

    # Halt is terminal
    graph.add_edge("halt", END)

    return graph


# =============================================================================
# ENTRY POINT
# =============================================================================


def create_initial_state(
    change_name: str,
    max_iterations: int = 10,
    loop_dir: str | None = None,
) -> LoopState:
    """Create the initial state for a new loop run."""
    if loop_dir is None:
        loop_dir = str(Path.cwd() / ".loop")

    Path(loop_dir).mkdir(parents=True, exist_ok=True)

    return LoopState(
        change_name=change_name,
        loop_dir=loop_dir,
        iteration=1,
        max_iterations=max_iterations,
        changed_files=[],
        gates=GateStatus(
            code_review=False,
            council_review=False,
            tests=False,
            coverage=False,
            precommit=False,
        ),
        active_findings=[],
        test_failures=[],
        precommit_errors=[],
        cycle_detected=False,
        cycle_type="",
        locked_files=[],
        all_gates_pass=False,
        halted=False,
        halt_reason="",
    )


def run(change_name: str, max_iterations: int = 10) -> dict:
    """
    Execute the full CI loop.

    Returns the final state as a dict.
    """
    graph = build_graph()
    app = graph.compile()

    initial_state = create_initial_state(change_name, max_iterations)

    # LangGraph executes the graph to completion
    final_state = app.invoke(initial_state)

    # Print summary
    if final_state.get("all_gates_pass"):
        print(f"\n✅ All gates passed after {final_state['iteration']} iterations.")
    elif final_state.get("halted"):
        print(f"\n⛔ Halted: {final_state['halt_reason']}")
    else:
        print(f"\n❓ Unexpected termination at iteration {final_state['iteration']}")

    return final_state


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="LangGraph CI Loop — automated implement/review/fix cycle"
    )
    parser.add_argument(
        "--change", required=True,
        help="Name of the change to implement (passed to /opsx:apply)"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=10,
        help="Maximum loop iterations before hard stop (default: 10)"
    )
    parser.add_argument(
        "--loop-dir", default=None,
        help="Directory for loop artifacts (default: .loop/)"
    )

    args = parser.parse_args()

    final = run(args.change, args.max_iterations)

    # Exit with appropriate code
    sys.exit(0 if final.get("all_gates_pass") else 1)
