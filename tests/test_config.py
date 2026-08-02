from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from CodeFlow.config import env_settings


class ConfigTests(unittest.TestCase):
    def test_env_settings_include_workflow_budget_minor_units(self) -> None:
        env = {
            "CODEFLOW_BILLING_CURRENCY_CODE": "EUR",
            "CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS": "2500",
            "CODEFLOW_EMBEDDING_MODEL": "text-embedding-3-large",
            "CODEFLOW_MAX_COST_MINOR_UNITS": "9999",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = env_settings()

        self.assertEqual(settings["CODEFLOW_BILLING_CURRENCY_CODE"], "EUR")
        self.assertEqual(settings["CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS"], "2500")
        self.assertEqual(settings["CODEFLOW_EMBEDDING_MODEL"], "text-embedding-3-large")
        self.assertNotIn("CODEFLOW_MAX_COST_MINOR_UNITS", settings)


if __name__ == "__main__":
    unittest.main()
