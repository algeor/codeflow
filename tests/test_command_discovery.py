from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from CodeFlow.command_discovery import find_executable


class CommandDiscoveryTests(unittest.TestCase):
    def test_find_executable_uses_path_without_shutil_which(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = Path(tmpdir) / "tool"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)

            result = find_executable("tool", env={"PATH": tmpdir})

        self.assertEqual(result, str(executable))

    def test_find_executable_returns_none_for_missing_command(self) -> None:
        self.assertIsNone(find_executable("missing-tool", env={"PATH": os.devnull}))


if __name__ == "__main__":
    unittest.main()

