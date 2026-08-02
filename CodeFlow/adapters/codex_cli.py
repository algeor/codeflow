from __future__ import annotations

import json

from .base import AgentRequest, CliAgentAdapter


class CodexCliAdapter(CliAgentAdapter):
    provider_cli = "codex"
    executable = "codex"

    def build_command(self, request: AgentRequest, prompt: str) -> list[str]:
        # Codex CLI flags can differ by installed version; this is the adapter seam.
        command = [self.executable, "exec"]
        if request.model:
            command.extend(["--model", request.model])
        if request.expected_schema:
            schema_path = request.output_path.with_suffix(".schema.json")
            schema_path.write_text(json.dumps(request.expected_schema, indent=2, sort_keys=True))
            command.extend(["--output-schema", str(schema_path)])
        command.append(prompt)
        return command
