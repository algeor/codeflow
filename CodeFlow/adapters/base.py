from __future__ import annotations

import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from CodeFlow.command_discovery import find_executable


MAX_PROMPT_BYTES = 1_000_000
ALLOWED_PROMPT_CONTROL_CHARS = {"\n", "\r", "\t"}


class PromptLoadError(ValueError):
    """Raised when an agent prompt file is not safe to load."""


@dataclass(frozen=True)
class AgentRequest:
    task_type: str
    model: str
    prompt_path: Path
    output_path: Path
    timeout_seconds: int = 600
    allowed_tools: list[str] = field(default_factory=list)
    expected_schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    provider_cli: str
    model: str
    status: str
    return_code: int | None
    output_path: Path
    stderr: str = ""
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CliAgentAdapter:
    provider_cli: str
    executable: str

    def is_available(self) -> bool:
        return find_executable(self.executable) is not None

    def build_command(self, request: AgentRequest, prompt: str) -> list[str]:
        raise NotImplementedError

    def invoke(self, request: AgentRequest) -> AgentResult:
        started = time.monotonic()
        request.output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            prompt = load_prompt(request.prompt_path)
        except PromptLoadError as exc:
            return self._failure(request, "failed", str(exc), started)

        command = self.build_command(request, prompt)

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            request.output_path.write_text(_coerce_text(exc.stdout))
            return AgentResult(
                provider_cli=self.provider_cli,
                model=request.model,
                status="timed_out",
                return_code=None,
                output_path=request.output_path,
                stderr=_coerce_text(exc.stderr),
                duration_ms=_elapsed_ms(started),
            )
        except OSError as exc:
            return self._failure(request, "failed", f"could not execute {self.executable}: {exc}", started)

        request.output_path.write_text(completed.stdout)
        status = "succeeded" if completed.returncode == 0 else "failed"
        return AgentResult(
            provider_cli=self.provider_cli,
            model=request.model,
            status=status,
            return_code=completed.returncode,
            output_path=request.output_path,
            stderr=completed.stderr,
            duration_ms=_elapsed_ms(started),
        )

    def _failure(self, request: AgentRequest, status: str, message: str, started: float) -> AgentResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_text("")
        return AgentResult(
            provider_cli=self.provider_cli,
            model=request.model,
            status=status,
            return_code=None,
            output_path=request.output_path,
            stderr=message,
            duration_ms=_elapsed_ms(started),
        )


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def load_prompt(path: Path, *, max_bytes: int = MAX_PROMPT_BYTES) -> str:
    try:
        stat = path.stat()
    except OSError as exc:
        raise PromptLoadError(f"could not read prompt: {exc}") from exc

    if not path.is_file():
        raise PromptLoadError(f"could not read prompt: not a file: {path}")
    if stat.st_size > max_bytes:
        raise PromptLoadError(f"prompt file exceeds {max_bytes} bytes: {path}")

    try:
        raw_prompt = path.read_bytes()
    except OSError as exc:
        raise PromptLoadError(f"could not read prompt: {exc}") from exc

    if len(raw_prompt) > max_bytes:
        raise PromptLoadError(f"prompt file exceeds {max_bytes} bytes: {path}")
    if b"\x00" in raw_prompt:
        raise PromptLoadError(f"prompt contains NUL byte: {path}")

    try:
        prompt = raw_prompt.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptLoadError(f"prompt must be valid UTF-8: {exc}") from exc

    for character in prompt:
        if character in ALLOWED_PROMPT_CONTROL_CHARS:
            continue
        if unicodedata.category(character) == "Cc":
            codepoint = f"U+{ord(character):04X}"
            raise PromptLoadError(f"prompt contains unsupported control character {codepoint}: {path}")

    return prompt


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
