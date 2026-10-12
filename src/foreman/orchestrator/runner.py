"""Run one task attempt end to end.

    prepare clone -> executor step -> commit -> scope check
      -> validation step(s) -> merge into milestone branch -> done

Before every side-effecting step the runner re-reads the task: if the
supervisor marked it stopped (or E-stop is on) it bails out without touching
anything. State changes use compare-and-set transitions, so a Stop that lands
mid-step always wins.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from foreman import gitops
from foreman.config import get_settings
from foreman.core.contextpack import build_prompt
from foreman.core.models import Node, Project, RunState, Task, TaskRun, TaskState
from foreman.core.state import ACTIVE_STATES, emit, finish_run, stop_requested, transition
from foreman.db import session
from foreman.executors import get_executor
from foreman.sandbox import Proc, Runtime, StepSpec, get_runtime, kill_handle

LOG_FLUSH_S = 0.25
LOG_BATCH = 50


class Stopped(Exception):
    """The task was stopped (by the user or E-stop) while we were working."""


class StepFailed(Exception):
    pass


def resolve_executor(project: Project, task: Task) -> tuple[str, int | None, str]:
    kind = task.executor_kind or project.executor_kind
    binding = project.role_bindings.get("executor", {}) if project.role_bindings else {}
    node_id = task.node_id or binding.get("node_id")
    model = task.model or binding.get("model", "")
    return kind, node_id, model


class TaskRunner:
    def __init__(self, task_id: int, run_id: int, runtime: Runtime | None = None):
        self.task_id = task_id
        self.run_id = run_id
        self.runtime = runtime or get_runtime()
        self.settings = get_settings()
        self.run_dir = self.settings.runs_dir / str(run_id)
        with session() as s:
            run = s.get(TaskRun, run_id)
            assert run is not None
            self.started_at = run.started_at

    # -- helpers ---------------------------------------------------------

    def _load(self) -> tuple[Task, Project]:
        with session() as s:
            task = s.get(Task, self.task_id)
            assert task is not None
            project = s.get(Project, task.project_id)
            assert project is not None
            return task, project

    def _check(self) -> Task:
        """Raise Stopped if the task should not advance any further."""
        task, _ = self._load()
        if task.state not in ACTIVE_STATES:
            raise Stopped(task.state.value)
        if stop_requested(task.project_id, self.task_id, self.started_at):
            raise Stopped("stop requested")
        return task

    def _set_handle(self, handle: str) -> None:
        with session() as s:
            run = s.get(TaskRun, self.run_id)
            assert run is not None
            run.handle = handle
            s.add(run)
            s.commit()

    def _emit(self, kind: str, message: str, **data: object) -> None:
        task, _ = self._load()
        emit(task.project_id, self.task_id, kind, message, run_id=self.run_id, **data)

    async def _step(
        self, name: str, command: list[str], env: dict[str, str], kind: str, timeout_s: float
    ) -> int:
        """Run one container step, streaming output into events and a log file."""
        task, _ = self._load()
        spec = StepSpec(
            name=f"foreman-run{self.run_id}-{name}-{int(time.time() * 1000) % 10**8}",
            workspace=gitops.task_dir(task.project_id, self.task_id),
            ctl_dir=self.run_dir / "ctl",
            command=command,
            env=env,
            labels={
                "foreman.run": str(self.run_id),
                "foreman.task": str(self.task_id),
                "foreman.project": str(task.project_id),
            },
        )
        proc = await self.runtime.start(spec)
        self._set_handle(proc.handle)
        # A Stop may have landed before the handle was recorded: honour it ourselves.
        try:
            self._check()
        except Stopped:
            await asyncio.to_thread(kill_handle, proc.handle, 0)
            await proc.wait()
            raise
        try:
            return await asyncio.wait_for(self._pump(proc, kind, name), timeout_s)
        except TimeoutError:
            await asyncio.to_thread(kill_handle, proc.handle)
            raise StepFailed(f"{name} timed out after {int(timeout_s)}s") from None

    async def _pump(self, proc: Proc, kind: str, name: str) -> int:
        task, _ = self._load()
        log_path = self.run_dir / f"{name}.log"
        buf: list[str] = []
        last = time.monotonic()

        def flush() -> None:
            nonlocal last
            if buf:
                emit(
                    task.project_id,
                    self.task_id,
                    kind,
                    "\n".join(buf),
                    run_id=self.run_id,
                    step=name,
                )
                buf.clear()
            last = time.monotonic()

        with log_path.open("a") as log:
            async for line in proc.lines():
                log.write(line + "\n")
                buf.append(line)
                if len(buf) >= LOG_BATCH or time.monotonic() - last > LOG_FLUSH_S:
                    flush()
            flush()
        return await proc.wait()

    # -- main ------------------------------------------------------------

    async def run(self) -> None:
        try:
            await self._run()
        except Stopped as e:
            # The supervisor normally records this first; whoever gets there first wins and
            # the other write is a no-op. Never report a user stop as a failure.
            task, _ = self._load()
            if stop_requested(task.project_id, self.task_id, self.started_at):
                transition(
                    self.task_id, ACTIVE_STATES, TaskState.STOPPED_BY_USER, "stop requested by user"
                )
            finish_run(self.run_id, RunState.STOPPED, f"stopped ({e})")
        except StepFailed as e:
            self._fail(str(e))
        except Exception as e:  # noqa: BLE001 - any crash fails the task, never wedges it
            self._fail(f"internal error: {type(e).__name__}: {e}")

    def _fail(self, reason: str) -> None:
        task, _ = self._load()
        if stop_requested(task.project_id, self.task_id, self.started_at):
            # The failure is a consequence of the stop (e.g. killed process): report the stop.
            transition(
                self.task_id, ACTIVE_STATES, TaskState.STOPPED_BY_USER, "stop requested by user"
            )
            finish_run(self.run_id, RunState.STOPPED, "stop requested by user")
            return
        finish_run(self.run_id, RunState.FAILED, reason)
        transition(
            self.task_id, (TaskState.RUNNING, TaskState.VALIDATING), TaskState.FAILED, reason
        )

    async def _run(self) -> None:
        task, project = self._load()
        (self.run_dir / "ctl").mkdir(parents=True, exist_ok=True)

        clone = await asyncio.to_thread(
            gitops.prepare_task_clone, task.project_id, self.task_id, project.milestone_branch
        )
        self._check()

        # 1. Executor
        kind, node_id, model = resolve_executor(project, task)
        executor = get_executor(kind)
        node = None
        if node_id is not None:
            with session() as s:
                node = s.get(Node, node_id)
        if executor.needs_node and (node is None or not model):
            raise StepFailed("no executor node/model configured for this project or task")
        prompt = build_prompt(project, task, previous_failure=self._previous_failure())
        inv = executor.prepare(project, task, node, model, self.run_dir / "ctl", prompt)
        self._emit(
            "system", f"executor {kind} starting" + (f" on {node.name} / {model}" if node else "")
        )
        code = await self._step("execute", inv.command, inv.env, "log", task.timeout_s)
        task = self._check()
        await asyncio.to_thread(gitops.commit_all, clone, f"Task {self.task_id}: {task.title}")
        if code != 0:
            raise StepFailed(f"executor exited with status {code}")

        # 2. Scope fence
        files = await asyncio.to_thread(gitops.changed_files, clone)
        outside = gitops.out_of_scope(files, task.allowed_globs)
        if outside:
            raise StepFailed(f"changed files outside allowed scope: {', '.join(outside)}")
        self._emit("diff", f"{len(files)} file(s) changed", files=files)

        if task.hold:
            transition(self.task_id, (TaskState.RUNNING,), TaskState.PAUSED, "paused after execute")
            finish_run(self.run_id, RunState.SUCCEEDED, "paused after execute")
            return

        # 3. Validation
        if not transition(self.task_id, (TaskState.RUNNING,), TaskState.VALIDATING):
            raise Stopped("not running")
        checks = [("validate", task.validation_cmd), ("tests", project.test_cmd)]
        for name, cmd in checks:
            if not cmd:
                continue
            self._check()
            self._emit("test", f"$ {cmd}", step=name)
            code = await self._step(name, ["bash", "-lc", cmd], {}, "test", task.timeout_s)
            if code != 0:
                raise StepFailed(f"{name} failed (exit {code}): {cmd}")
            self._emit("test", f"{name} passed", step=name, passed=True)

        task = self._check()
        if task.hold:
            transition(
                self.task_id, (TaskState.VALIDATING,), TaskState.PAUSED, "paused after validation"
            )
            finish_run(self.run_id, RunState.SUCCEEDED, "paused after validation")
            return

        # 4. Merge
        try:
            await asyncio.to_thread(gitops.merge_task, task.project_id, self.task_id, task.title)
        except gitops.GitError as e:
            raise StepFailed(str(e)) from e
        self._emit("system", f"merged into {project.milestone_branch}")
        if project.push_after_merge:
            try:
                await asyncio.to_thread(
                    gitops.push, task.project_id, project.repo_url, project.milestone_branch
                )
                self._emit("system", f"pushed {project.milestone_branch}")
            except gitops.GitError as e:
                # The merge stands locally; a failed push must not fail the task.
                self._emit("system", f"push failed: {e}")

        transition(self.task_id, (TaskState.VALIDATING,), TaskState.DONE)
        finish_run(self.run_id, RunState.SUCCEEDED)

    def _previous_failure(self) -> str:
        from sqlmodel import col, select

        with session() as s:
            prev = s.exec(
                select(TaskRun)
                .where(TaskRun.task_id == self.task_id, col(TaskRun.id) != self.run_id)
                .order_by(col(TaskRun.id).desc())
            ).first()
        if prev and prev.state in (RunState.FAILED, RunState.STOPPED) and prev.reason:
            return prev.reason
        return ""


def log_path(run_id: int, step: str) -> Path:
    return get_settings().runs_dir / str(run_id) / f"{step}.log"
