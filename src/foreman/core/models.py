"""Database tables. Timestamps are UNIX epoch seconds (float)."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> float:
    return time.time()


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATING = "validating"
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"
    STOPPED_BY_USER = "stopped_by_user"
    DISCARDED = "discarded"


class RunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"


class ControlAction(StrEnum):
    STOP = "stop"
    ESTOP = "estop"


class ControlScope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    TASK = "task"


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    repo_url: str
    default_branch: str = "main"
    milestone_branch: str = "foreman/m1"
    # Project-wide test command run after every task (regression guard). Optional.
    test_cmd: str = ""
    executor_kind: str = "opencode"
    # role -> {"node_id": int, "model": str}. M1 only uses "executor".
    role_bindings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    gates: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    paused: bool = False
    # When `paused` last changed; lets a late Stop avoid undoing a newer Resume.
    paused_at: float = 0.0
    push_after_merge: bool = False
    clone_error: str = ""
    created_at: float = Field(default_factory=now)


class Node(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    # OpenAI-compatible base URL, e.g. http://192.168.1.20:8080/v1
    base_url: str
    api_key: str = ""
    capacity: int = 1
    models: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    healthy: bool | None = None
    last_checked: float | None = None


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str
    description: str = ""
    todos: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Files the task may change (fnmatch globs). Empty = unrestricted.
    allowed_globs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    validation_cmd: str = ""
    deps: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # Overrides of the project defaults.
    executor_kind: str = ""
    node_id: int | None = None
    model: str = ""
    # For the "shell" executor: the script to run (testing / scripted tasks).
    executor_script: str = ""
    timeout_s: int = 1800
    state: TaskState = TaskState.QUEUED
    # Task-level pause: don't start; if running, park after the current step.
    hold: bool = False
    reason: str = ""
    attempts: int = 0
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class TaskRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    project_id: int = Field(index=True)
    node_id: int | None = None
    model: str = ""
    # How to kill it: "docker:<container name>" or "local:<pgid>".
    handle: str = ""
    state: RunState = RunState.RUNNING
    exit_code: int | None = None
    reason: str = ""
    started_at: float = Field(default_factory=now)
    ended_at: float | None = None


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, index=True)
    task_id: int | None = Field(default=None, index=True)
    run_id: int | None = None
    # log | state | test | diff | control | system
    kind: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    ts: float = Field(default_factory=now)


class Control(SQLModel, table=True):
    """Stop requests. Lives in control.db: written by the API, executed by the supervisor."""

    id: int | None = Field(default=None, primary_key=True)
    scope: ControlScope
    target_id: int | None = None
    action: ControlAction
    created_at: float = Field(default_factory=now)
    acked_at: float | None = None
    result: str = ""


class SystemFlag(SQLModel, table=True):
    """Global flags (E-stop). Lives in control.db."""

    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: float = Field(default_factory=now)
