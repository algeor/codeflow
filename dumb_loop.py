#!/usr/bin/env python3
"""
dumb_loop.py — The simplest context-isolated CI loop (v0).

Solves context window exhaustion without any framework dependency.
Each iteration runs in a fresh Claude Code session with only a compressed
summary of prior iterations injected as context.

No cycle detection, no parallel reviews, no reconciliation.
~50 lines of meaningful code. Start here, graduate to langgraph_ci_loop.py
when you need more.

Usage:
    python dumb_loop.py --change unified-ci-agnostic-schema
    python dumb_loop.py --change my-feature --max-iterations 5
"""

import json
import subprocess
import sys
import argparse
from pathlib import Path


def call_claude(prompt: str, max_turns: int = 30, timeout: int = 300) -> dict:
    """Call claude CLI as subprocess, return parsed JSON or raw text."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--max-turns", str(max_turns)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "all_pass": False, "summary": "Claude CLI timed out"}

    if result.returncode != 0:
        return {"error": f"exit_{result.returncode}", "all_pass": False, "summary": result.stderr[:500]}

    # Parse output — claude --output-format json wraps in {"type":"result","result":"..."}
    try:
        parsed = json.loads(result.stdout)
        inner = parsed.get("result", result.stdout)
        if isinstance(inner, str):
            # The result field might itself be JSON
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                if "```json" in inner:
                    json_block = inner.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_block)
                return {"text": inner, "all_pass": False, "summary": inner[:200]}
        return inner if isinstance(inner, dict) else {"text": str(inner), "all_pass": False}
    except json.JSONDecodeError:
        return {"text": result.stdout[:500], "all_pass": False, "summary": "Could not parse output"}


def truncate(text: str, max_chars: int = 2000) -> str:
    """Keep text under max_chars, preserving the most recent content."""
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    while len("\n".join(lines)) > max_chars and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)


def preflight_check() -> bool:
    """Verify workspace is clean before starting."""
    # Git status clean
    git_status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if git_status.stdout.strip():
        print("⚠️  Working directory has uncommitted changes.")
        print("   Commit or stash them before running the loop.")
        return False

    return True


def run_loop(change_name: str, max_iterations: int = 10, skip_preflight: bool = False):
    """The main loop: implement, then iterate until convergence."""

    if not skip_preflight and not preflight_check():
        return 1

    # Step 0: Implement the change
    print(f"🚀 Implementing: {change_name}")
    print(f"   Max iterations: {max_iterations}")
    print()

    impl_result = call_claude(
        f"Run /opsx:apply {change_name}. "
        f"After completion, list the files you created or modified.",
        max_turns=50,
        timeout=600,
    )

    if impl_result.get("error"):
        print(f"⛔ Implementation failed: {impl_result.get('error')}")
        return 1

    # Iteration loop
    summary = f"Change '{change_name}' has been implemented."

    for iteration in range(1, max_iterations + 1):
        print(f"\n{'═'*60}")
        print(f"  Iteration {iteration}/{max_iterations}")
        print(f"{'═'*60}")

        prompt = f"""You are in iteration {iteration} of an automated quality loop.

## Prior context:
{summary}

## Your job this iteration (do ALL steps in order):

1. **Code Review**: Run /code-review on changed files. Note findings.
   - Fix ONLY correctness, security, or design issues.
   - SKIP style nits entirely.

2. **Tests**: Run the full test suite (pytest). Fix any failures.
   - Fix the CODE, not the test (unless the test expectation is factually wrong).

3. **Coverage**: Run pytest --cov. If changed code is below 80%, add tests.

4. **Pre-commit**: Run pre-commit run --files <changed files>. Fix any failures.

## CRITICAL RULES:
- Be SURGICAL. Fix ONLY what's reported. Maximum 10 lines per finding.
- Do NOT refactor, improve, or touch code that isn't flagged.
- Do NOT add error handling, logging, or types to unflagged code.
- If a fix would require > 20 lines, describe what's needed but DON'T do it.

## After ALL steps, output EXACTLY this JSON block:

```json
{{
  "all_pass": true or false,
  "gates": {{
    "code_review": true or false,
    "tests": true or false,
    "coverage": true or false,
    "precommit": true or false
  }},
  "findings_fixed": number,
  "findings_skipped": number,
  "summary": "One paragraph describing what happened this iteration"
}}
```

Output the JSON block LAST, after all work is done."""

        result = call_claude(prompt, max_turns=40, timeout=600)

        # Check for errors
        if result.get("error"):
            print(f"  ⚠️  Error: {result['error']}")
            summary += f"\n- Iter {iteration}: ERROR — {result.get('summary', '?')}"
            continue

        # Check if all gates pass
        if result.get("all_pass"):
            print(f"\n✅ All gates pass after {iteration} iteration(s)!")
            print(f"   Summary: {result.get('summary', 'Done')}")
            return 0

        # Report status
        gates = result.get("gates", {})
        failing = [k for k, v in gates.items() if not v]
        passing = [k for k, v in gates.items() if v]
        fixed = result.get("findings_fixed", "?")
        skipped = result.get("findings_skipped", "?")

        print(f"  ✓ Passing: {passing}")
        print(f"  ✗ Failing: {failing}")
        print(f"  Fixed: {fixed} | Skipped: {skipped}")

        # Update summary (compressed — only recent iterations survive)
        iter_summary = result.get("summary", "No summary")
        summary = truncate(
            summary + f"\n- Iter {iteration} [failing: {failing}]: {iter_summary}",
            max_chars=2000,
        )

    print(f"\n⛔ Max iterations ({max_iterations}) reached without convergence.")
    print(f"   Final state: {json.dumps(gates, indent=2)}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dumb CI loop (v0) — context-isolated quality iterations"
    )
    parser.add_argument(
        "--change", required=True,
        help="Name of the change to implement (passed to /opsx:apply)"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=10,
        help="Maximum loop iterations before giving up (default: 10)"
    )
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="Skip the pre-flight workspace check"
    )

    args = parser.parse_args()
    sys.exit(run_loop(args.change, args.max_iterations, args.skip_preflight))
