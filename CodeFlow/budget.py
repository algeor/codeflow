from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class BudgetConfigError(ValueError):
    """Raised when workflow budget configuration is invalid."""


@dataclass(frozen=True)
class WorkflowBudget:
    billing_currency_code: str
    max_workflow_budget_minor_units: int | None
    spent_budget_minor_units: int = 0

    @property
    def is_limited(self) -> bool:
        return self.max_workflow_budget_minor_units is not None

    @property
    def remaining_budget_minor_units(self) -> int | None:
        if self.max_workflow_budget_minor_units is None:
            return None
        return self.max_workflow_budget_minor_units - self.spent_budget_minor_units

    @property
    def is_exceeded(self) -> bool:
        remaining = self.remaining_budget_minor_units
        return remaining is not None and remaining < 0

    def can_spend(self, additional_minor_units: int) -> bool:
        if additional_minor_units < 0:
            raise BudgetConfigError("additional spend cannot be negative")
        if self.max_workflow_budget_minor_units is None:
            return True
        return self.spent_budget_minor_units + additional_minor_units <= self.max_workflow_budget_minor_units


def workflow_budget_from_config(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    spent_budget_minor_units: int = 0,
) -> WorkflowBudget:
    environment = env if env is not None else os.environ
    workflow_config = config.get("workflow", {})
    if not isinstance(workflow_config, Mapping):
        workflow_config = {}

    currency_code = str(
        environment.get("CODEFLOW_BILLING_CURRENCY_CODE")
        or workflow_config.get("billing_currency_code")
        or "USD"
    )
    if not _CURRENCY_RE.fullmatch(currency_code):
        raise BudgetConfigError("billing currency code must be a three-letter ISO 4217 code")

    configured_budget = environment.get("CODEFLOW_MAX_WORKFLOW_BUDGET_MINOR_UNITS")
    if configured_budget is None:
        configured_budget = workflow_config.get("max_workflow_budget_minor_units")

    return WorkflowBudget(
        billing_currency_code=currency_code,
        max_workflow_budget_minor_units=_parse_optional_non_negative_int(configured_budget),
        spent_budget_minor_units=_parse_non_negative_int(spent_budget_minor_units, "spent budget"),
    )


def _parse_optional_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return _parse_non_negative_int(value, "workflow budget")


def _parse_non_negative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BudgetConfigError(f"{label} must be an integer minor-unit amount") from exc
    if parsed < 0:
        raise BudgetConfigError(f"{label} cannot be negative")
    return parsed

