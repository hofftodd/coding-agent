"""Build the prompt (context pack) handed to an executor for one task attempt.

M1 version: task fields only. Requirement slices, interface extraction and
token budgeting arrive with decomposition in M2.
"""

from __future__ import annotations

from foreman.core.models import Project, Task


def build_prompt(project: Project, task: Task, previous_failure: str = "") -> str:
    lines = [
        f"# Task {task.id}: {task.title}",
        "",
        "You are working in a git repository checked out in the current directory.",
        "Complete exactly this task. Do not work on anything else.",
        "",
    ]
    if task.description:
        lines += ["## Description", task.description.strip(), ""]
    if task.todos:
        lines += ["## Todos", *[f"- [ ] {t}" for t in task.todos], ""]
    if task.allowed_globs:
        lines += [
            "## Scope",
            "You may only create or modify files matching:",
            *[f"- `{g}`" for g in task.allowed_globs],
            "Changes outside this list will cause the task to be rejected.",
            "",
        ]
    if task.validation_cmd:
        lines += [
            "## Done when",
            "This command exits with status 0:",
            "```",
            task.validation_cmd,
            "```",
            "Run it yourself before finishing.",
            "",
        ]
    if project.test_cmd:
        lines += [
            "The project test suite must also keep passing:",
            "```",
            project.test_cmd,
            "```",
            "",
        ]
    if previous_failure:
        lines += ["## Previous attempt failed", previous_failure.strip(), ""]
    lines += ["Do not commit; the orchestrator commits your changes."]
    return "\n".join(lines) + "\n"
