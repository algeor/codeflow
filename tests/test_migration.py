from __future__ import annotations

import unittest
from pathlib import Path


class MigrationTests(unittest.TestCase):
    def test_initial_migration_has_implementation_handoff_table(self) -> None:
        migration = Path("CodeFlow/db/migrations/001_initial.sql").read_text()

        self.assertIn("create table implementation_handoffs", migration)
        self.assertIn("workflow_run_id uuid not null references workflow_runs(id)", migration)
        self.assertIn("check (status in ('ready', 'escalate'))", migration)


if __name__ == "__main__":
    unittest.main()

