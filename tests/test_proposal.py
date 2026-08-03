from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from CodeFlow.proposal import ProposalError, create_proposal_artifacts


class ProposalTests(unittest.TestCase):
    def test_create_proposal_artifacts_writes_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = create_proposal_artifacts(
                "readme-documentation-plan",
                "Enhance project documentation by creating a useful README.",
                root=Path(tmpdir),
            )

            change_dir = Path(tmpdir) / ".CodeFlow" / "changes" / "readme-documentation-plan"
            proposal_json = json.loads((change_dir / "proposal.json").read_text())
            filenames = sorted(path.name for path in change_dir.iterdir())

        self.assertEqual(result.change_name, "readme-documentation-plan")
        self.assertEqual(proposal_json["change_name"], "readme-documentation-plan")
        self.assertEqual(
            filenames,
            ["design.md", "proposal.json", "proposal.md", "questions.md", "tasks.md"],
        )
        self.assertEqual(proposal_json["open_questions"], [])
        self.assertIn("docs", proposal_json["risk_areas"])

    def test_create_proposal_artifacts_refuses_invalid_change_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ProposalError, "kebab-case"):
                create_proposal_artifacts("Bad Name", "request", root=Path(tmpdir))

    def test_create_proposal_artifacts_refuses_to_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            create_proposal_artifacts("readme-plan", "request", root=root)

            with self.assertRaisesRegex(ProposalError, "already exist"):
                create_proposal_artifacts("readme-plan", "request", root=root)

            result = create_proposal_artifacts("readme-plan", "updated request", root=root, overwrite=True)

        self.assertEqual(result.summary, "Plan implementation work for: updated request")


if __name__ == "__main__":
    unittest.main()
