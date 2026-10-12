"""Executor interface: turns a task into a command plus control files.

Executors write whatever they need (prompt, tool config) into the run's
control directory, which is mounted read-only into the container at
$FOREMAN_CTL, and return the command to run in the task clone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from foreman.core.models import Node, Project, Task


@dataclass
class ExecutorInvocation:
    command: list[str]
    env: dict[str, str]


class Executor(Protocol):
    kind: str
    needs_node: bool

    def prepare(
        self,
        project: Project,
        task: Task,
        node: Node | None,
        model: str,
        ctl_dir: Path,
        prompt: str,
    ) -> ExecutorInvocation: ...
