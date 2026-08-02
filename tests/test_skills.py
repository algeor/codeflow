from __future__ import annotations

import unittest
from pathlib import Path


class SkillTests(unittest.TestCase):
    def test_implement_init_skill_has_required_handoff_and_guardrails(self) -> None:
        skill = Path(".CodeFlow/skills/implement-init/SKILL.md").read_text()

        self.assertIn("name: implement-init", skill)
        self.assertIn("Run this phase in a dedicated subagent", skill)
        self.assertIn("Do not write production code", skill)
        self.assertIn("GIT_USERNAME or GIT_EMAIL is not set", skill)
        self.assertIn('"status": "ready"', skill)
        self.assertIn('"existing_worktree_changes": []', skill)
        self.assertIn('"validation_commands": []', skill)

    def test_implement_code_creation_skill_has_subagent_handoff_contract(self) -> None:
        skill = Path(".CodeFlow/skills/implement-code-creation/SKILL.md").read_text()

        self.assertIn("name: implement-code-creation", skill)
        self.assertIn("Run this phase in a dedicated subagent", skill)
        self.assertIn('"status": "completed"', skill)
        self.assertIn('"tests_to_run": ["pytest tests/test_example.py"]', skill)
        self.assertIn("Before exiting successfully, run configured pre-commit hooks", skill)
        self.assertIn("pre-commit run --files <changed files>", skill)
        self.assertIn('"precommit_run": {', skill)
        self.assertIn("Do not run `git add`, `git commit`, or `git push`", skill)
        self.assertIn("without rereading the whole repository", skill)

    def test_implement_validation_skill_is_lightweight_pre_commit_gate(self) -> None:
        skill = Path(".CodeFlow/skills/implement-validation/SKILL.md").read_text()

        self.assertIn("name: implement-validation", skill)
        self.assertIn("before every workflow-created commit", skill)
        self.assertIn("Keep this phase lightweight", skill)
        self.assertIn('"safe_to_commit": true', skill)
        self.assertIn("Do not run `git add`, `git commit`, or `git push`", skill)
        self.assertNotIn("Commit and Push", skill)
        self.assertNotIn("gerrit_url", skill)


if __name__ == "__main__":
    unittest.main()
