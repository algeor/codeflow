from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def find_executable(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """Return the executable path for a command name, or None when unavailable."""
    environment = env if env is not None else os.environ

    if os.sep in name or (os.altsep and os.altsep in name):
        return _executable_path(Path(name))

    for directory in os.get_exec_path(environment):
        candidate = Path(directory) / name
        if executable := _executable_path(candidate):
            return executable

    return None


def _executable_path(path: Path) -> str | None:
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None

