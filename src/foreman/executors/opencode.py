"""OpenCode (https://opencode.ai) in non-interactive `run` mode.

The node is registered as an OpenAI-compatible provider named "foreman" in a
generated config file; the prompt is passed as the run message.
"""

from __future__ import annotations

import json
from pathlib import Path

from foreman.core.models import Node, Project, Task
from foreman.executors.base import ExecutorInvocation

PROVIDER = "foreman"


def opencode_config(node: Node, model: str) -> dict:
    options: dict[str, str] = {"baseURL": node.base_url}
    if node.api_key:
        options["apiKey"] = node.api_key
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"foreman node {node.name}",
                "options": options,
                "models": {model: {"name": model}},
            }
        },
        "model": f"{PROVIDER}/{model}",
        "autoupdate": False,
        "share": "disabled",
        # Unattended run inside a sandbox: allow edits and shell, nothing from the web.
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "deny"},
    }


class OpenCodeExecutor:
    kind = "opencode"
    needs_node = True

    def prepare(
        self,
        project: Project,
        task: Task,
        node: Node | None,
        model: str,
        ctl_dir: Path,
        prompt: str,
    ) -> ExecutorInvocation:
        if node is None or not model:
            raise ValueError("opencode executor needs a node and a model")
        (ctl_dir / "prompt.md").write_text(prompt)
        (ctl_dir / "opencode.json").write_text(json.dumps(opencode_config(node, model), indent=2))
        return ExecutorInvocation(
            command=[
                "bash",
                "-c",
                'export OPENCODE_CONFIG="$FOREMAN_CTL/opencode.json"; '
                'exec opencode run --model "$FOREMAN_MODEL" "$(cat "$FOREMAN_CTL/prompt.md")"',
            ],
            env={"FOREMAN_MODEL": f"{PROVIDER}/{model}"},
        )
