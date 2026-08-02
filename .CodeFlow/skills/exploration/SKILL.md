# Exploration Skill

## Purpose

Investigate the repository, compare options, and answer design questions without making implementation changes by default.

## Inputs

- Exploration question or investigation goal.
- Retrieved repo context.
- Exact files opened from disk when needed.
- Prior proposal/design/task artifacts when relevant.

## Required Output

Write an exploration report to the output path supplied by the runner.

Recommended structure:

- Question answered.
- Relevant files and symbols.
- Options considered.
- Recommendation.
- Risks and unknowns.
- Suggested next step.

Optional JSON summary:

```json
{
  "question": "what was investigated",
  "recommendation": "short recommendation",
  "relevant_files": ["path/to/file.py"],
  "risks": ["risk description"],
  "open_questions": []
}
```

## Rules

- Do not modify product code unless the user explicitly asks.
- Prefer evidence from current files over memory or retrieved summaries.
- Keep the result useful for proposal/design updates and PR comment answers.
- Do not include archival instructions.

