from __future__ import annotations

import json

from .base import AgentRequest, CliAgentAdapter


class ClaudeCliAdapter(CliAgentAdapter):
    provider_cli = "claude"
    executable = "claude"

    def build_command(self, request: AgentRequest, prompt: str) -> list[str]:
        command = [self.executable, "--print", "--output-format", "json"]
        if request.model:
            command.extend(["--model", request.model])
        if request.allowed_tools:
            command.extend(["--allowedTools", ",".join(request.allowed_tools)])
        if request.expected_schema:
            command.extend(["--json-schema", json.dumps(request.expected_schema)])
        command.append(prompt)
        return command
