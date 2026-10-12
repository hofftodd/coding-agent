"""Request bodies. Responses are the table models themselves."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RoleBinding(BaseModel):
    node_id: int | None = None
    model: str = ""


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    milestone_branch: str = "foreman/m1"
    test_cmd: str = ""
    executor_kind: str = "opencode"
    role_bindings: dict[str, RoleBinding] = Field(default_factory=dict)
    push_after_merge: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = None
    test_cmd: str | None = None
    executor_kind: str | None = None
    role_bindings: dict[str, RoleBinding] | None = None
    gates: dict[str, Any] | None = None
    push_after_merge: bool | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    todos: list[str] = Field(default_factory=list)
    allowed_globs: list[str] = Field(default_factory=list)
    validation_cmd: str = Field(min_length=1)
    deps: list[int] = Field(default_factory=list)
    executor_kind: str = ""
    node_id: int | None = None
    model: str = ""
    executor_script: str = ""
    timeout_s: int = Field(default=1800, ge=10, le=24 * 3600)


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    todos: list[str] | None = None
    allowed_globs: list[str] | None = None
    validation_cmd: str | None = None
    deps: list[int] | None = None
    executor_kind: str | None = None
    node_id: int | None = None
    model: str | None = None
    executor_script: str | None = None
    timeout_s: int | None = None


class NodeCreate(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""
    capacity: int = Field(default=1, ge=1, le=64)
    models: list[str] = Field(default_factory=list)


class ControlRequest(BaseModel):
    scope: Literal["global", "project", "task"]
    target_id: int | None = None
    action: Literal["pause", "resume", "stop", "estop", "clear_estop"]


class RetryRequest(BaseModel):
    reset: bool = False  # discard the task clone and start from the milestone tip
