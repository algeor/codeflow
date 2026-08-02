from __future__ import annotations

import unittest

from CodeFlow.budget import BudgetConfigError, workflow_budget_from_config


class WorkflowBudgetTests(unittest.TestCase):
    def test_budget_loads_from_workflow_config(self) -> None:
        config = {"workflow": {"billing_currency_code": "EUR", "max_workflow_budget_minor_units": 2500}}

        budget = workflow_budget_from_config(config, env={}, spent_budget_minor_units=400)

        self.assertEqual(budget.billing_currency_code, "EUR")
        self.assertEqual(budget.max_workflow_budget_minor_units, 2500)
        self.assertEqual(budget.remaining_budget_minor_units, 2100)
        self.assertTrue(budget.can_spend(2100))
        self.assertFalse(budget.can_spend(2101))

    def test_env_budget_overrides_config_budget(self) -> None:
        config = {"workflow": {"billing_currency_code": "USD", "max_workflow_budget_minor_units": 2500}}
        env = {"CODEFLOW_BILLING_CURRENCY_CODE": "EUR", "CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS": "1200"}

        budget = workflow_budget_from_config(config, env=env)

        self.assertEqual(budget.billing_currency_code, "EUR")
        self.assertEqual(budget.max_workflow_budget_minor_units, 1200)

    def test_empty_budget_means_unlimited(self) -> None:
        budget = workflow_budget_from_config({"workflow": {"billing_currency_code": "USD"}}, env={})

        self.assertFalse(budget.is_limited)
        self.assertIsNone(budget.remaining_budget_minor_units)
        self.assertTrue(budget.can_spend(10_000_000))

    def test_invalid_currency_is_rejected(self) -> None:
        with self.assertRaises(BudgetConfigError):
            workflow_budget_from_config({"workflow": {"billing_currency_code": "usd"}}, env={})

    def test_negative_budget_is_rejected(self) -> None:
        with self.assertRaises(BudgetConfigError):
            workflow_budget_from_config({"workflow": {"max_workflow_budget_minor_units": -1}}, env={})


if __name__ == "__main__":
    unittest.main()

