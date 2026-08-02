from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from CodeFlow.adapters.base import MAX_PROMPT_BYTES, AgentRequest, CliAgentAdapter
from CodeFlow.adapters.claude_cli import ClaudeCliAdapter
from CodeFlow.adapters.codex_cli import CodexCliAdapter


class NoopAdapter(CliAgentAdapter):
    provider_cli = "noop"
    executable = "noop"

    def build_command(self, request: AgentRequest, prompt: str) -> list[str]:
        return ["noop"]


class AdapterTests(unittest.TestCase):
    def test_missing_prompt_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "nested" / "out.txt"
            request = AgentRequest(
                task_type="status",
                model="test-model",
                prompt_path=Path(tmpdir) / "missing.txt",
                output_path=output_path,
            )

            result = NoopAdapter().invoke(request)

        self.assertEqual(result.status, "failed")
        self.assertIn("could not read prompt", result.stderr)
        self.assertIsNone(result.return_code)
        self.assertIsNotNone(result.duration_ms)

    def test_nul_prompt_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.txt"
            prompt_path.write_bytes(b"hello\x00world")
            request = AgentRequest(
                task_type="status",
                model="test-model",
                prompt_path=prompt_path,
                output_path=Path(tmpdir) / "out.txt",
            )

            result = NoopAdapter().invoke(request)

        self.assertEqual(result.status, "failed")
        self.assertIn("NUL byte", result.stderr)

    def test_invalid_utf8_prompt_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.txt"
            prompt_path.write_bytes(b"\xff")
            request = AgentRequest(
                task_type="status",
                model="test-model",
                prompt_path=prompt_path,
                output_path=Path(tmpdir) / "out.txt",
            )

            result = NoopAdapter().invoke(request)

        self.assertEqual(result.status, "failed")
        self.assertIn("valid UTF-8", result.stderr)

    def test_control_character_prompt_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.txt"
            prompt_path.write_text("hello\x08world")
            request = AgentRequest(
                task_type="status",
                model="test-model",
                prompt_path=prompt_path,
                output_path=Path(tmpdir) / "out.txt",
            )

            result = NoopAdapter().invoke(request)

        self.assertEqual(result.status, "failed")
        self.assertIn("unsupported control character U+0008", result.stderr)

    def test_oversized_prompt_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.txt"
            prompt_path.write_bytes(b"a" * (MAX_PROMPT_BYTES + 1))
            request = AgentRequest(
                task_type="status",
                model="test-model",
                prompt_path=prompt_path,
                output_path=Path(tmpdir) / "out.txt",
            )

            result = NoopAdapter().invoke(request)

        self.assertEqual(result.status, "failed")
        self.assertIn("prompt file exceeds", result.stderr)

    def test_claude_command_includes_expected_schema(self) -> None:
        request = AgentRequest(
            task_type="proposal",
            model="sonnet",
            prompt_path=Path("prompt.txt"),
            output_path=Path("out.txt"),
            expected_schema={"type": "object"},
        )

        command = ClaudeCliAdapter().build_command(request, "hello")

        self.assertEqual(command[1:3], ["--print", "--output-format"])
        self.assertEqual(command[-1], "hello")
        self.assertIn("--json-schema", command)
        self.assertIn('{"type": "object"}', command)

    def test_codex_command_writes_expected_schema_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.txt"
            request = AgentRequest(
                task_type="proposal",
                model="gpt-5",
                prompt_path=Path(tmpdir) / "prompt.txt",
                output_path=output_path,
                expected_schema={"type": "object"},
            )

            command = CodexCliAdapter().build_command(request, "hello")
            schema_path = output_path.with_suffix(".schema.json")

            self.assertIn("--output-schema", command)
            self.assertIn(str(schema_path), command)
            self.assertTrue(schema_path.exists())


if __name__ == "__main__":
    unittest.main()
