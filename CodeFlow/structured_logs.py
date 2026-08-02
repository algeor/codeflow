from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_PROCESSORS = [
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.JSONRenderer(),
]


def get_logger(name: str) -> Any:
    return structlog.wrap_logger(
        structlog.PrintLogger(sys.stderr),
        processors=_PROCESSORS,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    ).bind(logger=name)


def log_doctor_blocking_failures(checks: list[dict[str, Any]], config_error: str | None) -> None:
    blocking_failures = [check for check in checks if check.get("required", True) and not check.get("ok")]
    if not blocking_failures and not config_error:
        return

    logger = get_logger("CodeFlow.doctor")
    for check in blocking_failures:
        logger.error(
            "doctor_blocking_failure",
            check_name=check.get("name"),
            details={key: value for key, value in check.items() if key not in {"name", "ok", "required"}},
        )

    if config_error:
        logger.error("doctor_blocking_failure", check_name="config_error", details={"error": config_error})
