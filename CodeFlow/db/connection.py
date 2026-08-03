from __future__ import annotations

import os
from typing import Any


class DatabaseError(RuntimeError):
    """Raised when CodeFlow cannot connect to the configured database."""


def connect_database(database_url: str | None = None) -> Any:
    url = database_url or os.getenv("CODEFLOW_DATABASE_URL")
    if not url:
        raise DatabaseError("CODEFLOW_DATABASE_URL is required for database persistence")

    try:
        import psycopg  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise DatabaseError("Install psycopg or CodeFlow[runtime] to use database persistence") from exc

    return psycopg.connect(url)
