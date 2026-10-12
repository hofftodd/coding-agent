"""Runs the task's `executor_script` with bash. For scripted tasks and tests."""

from __future__ import annotations

from pathlib import Path

from foreman.core.models import Node, Project, Task
from foreman.executors.base import ExecutorInvocation


class ShellExecutor:
    kind = "shell"
    needs_node = False

    def prepare(
        self,
        project: Project,
        task: Task,
        node: Node | None,
        model: str,
        ctl_dir: Path,
        prompt: str,
    ) -> ExecutorInvocation:
        (ctl_dir / "prompt.md").write_text(prompt)
        (ctl_dir / "script.sh").write_text(task.executor_script or "true\n")
        return ExecutorInvocation(
            command=["bash", "-c", 'exec bash "$FOREMAN_CTL/script.sh"'],
            env={},
        )
