from __future__ import annotations

import json
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


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None

    def to_dict(self) -> dict[str, int]:
        data: dict[str, int] = {}
        if self.input_tokens is not None:
            data["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            data["output_tokens"] = self.output_tokens
        if self.cached_input_tokens is not None:
            data["cached_input_tokens"] = self.cached_input_tokens
        return data


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
        token_usage = extract_token_usage(completed.stdout)
        return AgentResult(
            provider_cli=self.provider_cli,
            model=request.model,
            status=status,
            return_code=completed.returncode,
            output_path=request.output_path,
            stderr=completed.stderr,
            duration_ms=_elapsed_ms(started),
            metadata={"token_usage": token_usage.to_dict()} if token_usage.to_dict() else {},
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


def extract_token_usage(raw_output: str) -> TokenUsage:
    parsed = _loads_json_value(raw_output.strip())
    if parsed is None:
        return TokenUsage()
    usage = _find_usage_mapping(parsed)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=_first_int(usage, "input_tokens", "input_token_count", "prompt_tokens", "prompt_token_count"),
        output_tokens=_first_int(usage, "output_tokens", "output_token_count", "completion_tokens", "completion_token_count"),
        cached_input_tokens=_first_int(usage, "cached_input_tokens", "cache_read_input_tokens", "cached_tokens"),
    )


def _loads_json_value(raw: str) -> Any | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for line in reversed(raw.splitlines()):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    return None


def _find_usage_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if any(key in value for key in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")):
            return value
        usage = value.get("usage")
        if isinstance(usage, dict):
            return usage
        for key in ("result", "response", "text", "content"):
            nested = value.get(key)
            if isinstance(nested, str):
                parsed = _loads_json_value(nested)
                found = _find_usage_mapping(parsed)
                if found is not None:
                    return found
            else:
                found = _find_usage_mapping(nested)
                if found is not None:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _find_usage_mapping(item)
            if found is not None:
                return found
    return None


def _first_int(values: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None
