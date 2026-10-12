"""Planner jobs: Phase 1 chat turns and plan decomposition, run by the orchestrator.

Jobs stream from the planner model and are stoppable mid-stream: Stop / E-stop
on the project (or the supervisor marking the job stopped) closes the model
connection. Results are applied only if the job is still running when the
model finishes (compare-and-set), so a stopped job never writes anything.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import delete, update
from sqlmodel import col, select

from foreman import gitops
from foreman.core.contextpack import (
    build_pack,
    estimate_tokens,
    matching_files,
    repo_files,
    size_violations,
)
from foreman.core.models import (
    JobKind,
    JobState,
    Node,
    PlannerJob,
    Project,
    ProjectPhase,
    SpecMessage,
    Task,
    TaskState,
    now,
)
from foreman.core.state import emit, stop_requested
from foreman.db import session
from foreman.llm import Aborted, LLMError, stream_chat
from foreman.planner import (
    PlanError,
    TaskSpec,
    chat_system_prompt,
    decompose_system_prompt,
    doc_path,
    parse_chat,
    parse_tasks,
    render_tasks_md,
    retry_message,
    topo_order,
)

log = logging.getLogger("foreman.jobs")

MAX_DECOMPOSE_ATTEMPTS = 3
FLUSH_S = 0.25


class JobFailed(Exception):
    pass


def finish_job(job_id: int, frm: tuple[JobState, ...], to: JobState, error: str = "") -> bool:
    with session() as s:
        res = s.exec(  # type: ignore[call-overload]
            update(PlannerJob)
            .where(col(PlannerJob.id) == job_id, col(PlannerJob.state).in_(frm))
            .values(state=to, error=error, ended_at=now())
        )
        s.commit()
        return bool(res.rowcount)


def set_phase(project_id: int, phase: ProjectPhase) -> None:
    with session() as s:
        p = s.get(Project, project_id)
        if p is not None:
            p.phase = phase
            s.add(p)
            s.commit()
    emit(project_id, None, "state", f"phase: {phase.value}", phase=phase.value)


def planner_note(project_id: int, text: str, job_id: int | None = None) -> None:
    """A message from foreman itself in the Phase 1 conversation."""
    with session() as s:
        s.add(SpecMessage(project_id=project_id, role="assistant", content=text, job_id=job_id))
        s.commit()


def read_docs(project: Project) -> dict[str, str]:
    assert project.id is not None
    return {
        name: gitops.read_repo_file(project.id, doc_path(project, name))
        for name in ("spec", "plan", "validation", "tasks")
    }


def _binding(project: Project, role: str) -> tuple[Node | None, str]:
    b = (project.role_bindings or {}).get(role) or {}
    node = None
    if b.get("node_id") is not None:
        with session() as s:
            node = s.get(Node, b["node_id"])
    return node, b.get("model", "")


def _fit_history(messages: list[dict[str, str]], budget: int) -> list[dict[str, str]]:
    """Drop the oldest turns until the conversation fits; always keep the last turn."""
    msgs = list(messages)
    while len(msgs) > 1 and sum(estimate_tokens(m["content"]) for m in msgs) > budget:
        msgs.pop(0)
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _alternate(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge consecutive same-role turns (e.g. foreman notes after a planner reply)."""
    out: list[dict[str, str]] = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(dict(m))
    return out


class JobRunner:
    def __init__(self, job_id: int):
        self.job_id = job_id
        with session() as s:
            job = s.get(PlannerJob, job_id)
            assert job is not None
            self.project_id = job.project_id
            self.kind = job.kind
        self.started_at = time.time()
        self._buf: list[str] = []
        self._last_flush = time.monotonic()

    # -- helpers ------------------------------------------------------------

    def project(self) -> Project:
        with session() as s:
            p = s.get(Project, self.project_id)
            assert p is not None
            return p

    def should_abort(self) -> bool:
        with session() as s:
            job = s.get(PlannerJob, self.job_id)
        if job is None or job.state != JobState.RUNNING:
            return True
        return stop_requested(self.project_id, 0, self.started_at)

    def _on_text(self, text: str) -> None:
        self._buf.append(text)
        if time.monotonic() - self._last_flush > FLUSH_S:
            self._flush()

    def _flush(self) -> None:
        if self._buf:
            emit(self.project_id, None, "planner", "".join(self._buf), job_id=self.job_id)
            self._buf.clear()
        self._last_flush = time.monotonic()

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        with session() as s:
            job = s.get(PlannerJob, self.job_id)
            if job is not None:
                job.input_tokens += input_tokens
                job.output_tokens += output_tokens
                s.add(job)
                s.commit()

    async def _call(
        self, node: Node, model: str, system: str, messages: list[dict[str, str]]
    ) -> str:
        result = await stream_chat(
            node,
            model,
            system,
            messages,
            on_text=self._on_text,
            should_abort=self.should_abort,
        )
        self._flush()
        self._record_usage(result.input_tokens, result.output_tokens)
        if not result.text.strip():
            raise JobFailed("the planner returned an empty response")
        return result.text

    # -- entry point --------------------------------------------------------

    async def run(self) -> None:
        with session() as s:
            job = s.get(PlannerJob, self.job_id)
            assert job is not None
            if job.state != JobState.QUEUED:
                return
            job.state, job.started_at = JobState.RUNNING, now()
            s.add(job)
            s.commit()
        try:
            project = self.project()
            node, model = _binding(project, "planner")
            with session() as s:
                job = s.get(PlannerJob, self.job_id)
                assert job is not None
                job.node_id, job.model = (node.id if node else None), model
                s.add(job)
                s.commit()
            emit(
                self.project_id,
                None,
                "planner_start",
                f"planner {self.kind.value} started",
                job_id=self.job_id,
                job_kind=self.kind.value,
            )
            if node is None or not model:
                raise JobFailed("no planner node/model configured for this project")
            if self.kind == JobKind.CHAT:
                await self._chat(node, model)
            else:
                await self._decompose(node, model)
        except Aborted:
            self._flush()
            finish_job(self.job_id, (JobState.RUNNING,), JobState.STOPPED, "stopped by user")
            self._after_failure("stopped by user")
        except (JobFailed, LLMError) as e:
            self._fail(str(e))
        except Exception as e:  # noqa: BLE001 - never leave a job running
            log.exception("planner job %s crashed", self.job_id)
            self._fail(f"internal error: {type(e).__name__}: {e}")
        emit(
            self.project_id,
            None,
            "planner_end",
            f"planner {self.kind.value} finished",
            job_id=self.job_id,
        )

    def _fail(self, error: str) -> None:
        self._flush()
        # DONE too: the job may have been claimed as done and then failed to apply its
        # results (e.g. a git error); that must be reported, not swallowed.
        if finish_job(self.job_id, (JobState.RUNNING, JobState.DONE), JobState.FAILED, error):
            self._after_failure(error)

    def _after_failure(self, error: str) -> None:
        if self.kind == JobKind.DECOMPOSE:
            set_phase(self.project_id, ProjectPhase.SPECIFY)
            planner_note(self.project_id, f"Task breakdown did not complete: {error}", self.job_id)
        else:
            planner_note(self.project_id, f"(planner error: {error})", self.job_id)

    # -- chat ---------------------------------------------------------------

    async def _chat(self, node: Node, model: str) -> None:
        project = self.project()
        docs = read_docs(project)
        tree = repo_files(gitops.repo_dir(self.project_id))
        system = chat_system_prompt(project, docs, tree, project.acceptance_cmd)
        with session() as s:
            history = s.exec(
                select(SpecMessage)
                .where(SpecMessage.project_id == self.project_id)
                .order_by(col(SpecMessage.id))
            ).all()
        messages = _alternate([{"role": m.role, "content": m.content} for m in history])
        budget = node.context_window // 2 - estimate_tokens(system)
        messages = _fit_history(messages, max(budget, 2000))
        if not messages or messages[-1]["role"] != "user":
            raise JobFailed("nothing to answer")

        text = await self._call(node, model, system, messages)
        out = parse_chat(project, text)
        if not finish_job(self.job_id, (JobState.RUNNING,), JobState.DONE):
            return  # stopped while we were finishing: apply nothing
        if out.files:
            first = out.message.splitlines()[0][:60] if out.message else "update"
            await asyncio.to_thread(
                gitops.write_and_commit, self.project_id, out.files, f"spec: {first}"
            )
        if out.acceptance_cmd is not None:
            with session() as s:
                p = s.get(Project, self.project_id)
                assert p is not None
                p.acceptance_cmd = out.acceptance_cmd
                s.add(p)
                s.commit()
        content = out.message or "(updated files)"
        if out.rejected:
            content += "\n\n(foreman refused writes to: " + ", ".join(out.rejected) + ")"
        with session() as s:
            s.add(
                SpecMessage(
                    project_id=self.project_id,
                    role="assistant",
                    content=content,
                    files=sorted(out.files),
                    job_id=self.job_id,
                )
            )
            s.commit()

    # -- decomposition ------------------------------------------------------

    def _check_sizes(
        self, project: Project, specs: list[TaskSpec], window: int | None
    ) -> tuple[dict[str, list[str]], dict[str, int]]:
        repo = gitops.repo_dir(self.project_id)
        files = repo_files(repo)
        spec_text = gitops.read_repo_file(self.project_id, doc_path(project, "spec"))
        violations: dict[str, list[str]] = {}
        tokens: dict[str, int] = {}
        for t in specs:
            task = Task(
                project_id=self.project_id,
                key=t.key,
                title=t.title,
                description=t.description,
                todos=t.todos,
                req_refs=t.req_refs,
                allowed_globs=t.allowed_files,
                context_files=t.context_files,
                validation_cmd=t.validation_cmd,
            )
            pack = build_pack(project, task, repo, context_window=window, spec=spec_text)
            tokens[t.key] = pack.tokens
            v = size_violations(project, task, pack, files)
            if any(any(c in g for c in "*?[") for g in t.allowed_files):
                v.append("allowed_files must list files explicitly (no wildcards)")
            if matching_files(t.allowed_files, [f"specs/{project.feature}/x.md"]):
                v.append("tasks must not edit the spec documents")
            if v:
                violations[t.key] = v
        return violations, tokens

    async def _decompose(self, node: Node, model: str) -> None:
        project = self.project()
        docs = read_docs(project)
        tree = repo_files(gitops.repo_dir(self.project_id))
        exec_node, _ = _binding(project, "executor")
        window = exec_node.context_window if exec_node else None
        budget = (window or 32768) * project.context_budget_pct // 100
        system = decompose_system_prompt(project, docs, tree, budget)
        messages = [{"role": "user", "content": "Break the plan into tasks now."}]

        # The most recent list that parsed, with its size problems (if any).
        best: tuple[list[TaskSpec], dict[str, list[str]], dict[str, int]] | None = None
        last_error = ""
        for attempt in range(1, MAX_DECOMPOSE_ATTEMPTS + 1):
            text = await self._call(node, model, system, messages)
            try:
                specs = parse_tasks(text)
                violations, tokens = await asyncio.to_thread(
                    self._check_sizes, project, specs, window
                )
                best = (specs, violations, tokens)
                problems = [f"task {k}: {'; '.join(v)}" for k, v in violations.items()]
            except PlanError as e:
                last_error = str(e)
                problems = [last_error]
            if not problems:
                break
            emit(
                self.project_id,
                None,
                "system",
                f"task breakdown attempt {attempt}: {len(problems)} problem(s)",
                problems=problems,
            )
            if attempt < MAX_DECOMPOSE_ATTEMPTS:
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": retry_message(problems)},
                ]
        if best is None:
            raise JobFailed(f"could not get a valid task list: {last_error}")
        specs, violations, tokens = best

        if not finish_job(self.job_id, (JobState.RUNNING,), JobState.DONE):
            return
        self._create_tasks(project, specs, violations, tokens)

    def _create_tasks(
        self,
        project: Project,
        specs: list[TaskSpec],
        violations: dict[str, list[str]],
        tokens: dict[str, int],
    ) -> None:
        review = bool((project.gates or {}).get("plan_approval", True)) or bool(violations)
        initial = TaskState.PROPOSED if review else TaskState.QUEUED
        ordered = topo_order(specs)
        ids: dict[str, int] = {}
        with session() as s:
            s.exec(  # type: ignore[call-overload]
                delete(Task).where(
                    col(Task.project_id) == self.project_id,
                    col(Task.state) == TaskState.PROPOSED,
                )
            )
            for t in ordered:
                row = Task(
                    project_id=self.project_id,
                    key=t.key,
                    title=t.title,
                    description=t.description,
                    todos=t.todos,
                    req_refs=t.req_refs,
                    allowed_globs=t.allowed_files,
                    context_files=t.context_files,
                    validation_cmd=t.validation_cmd,
                    deps=[ids[d] for d in t.deps],
                    size_violations=violations.get(t.key, []),
                    pack_tokens=tokens.get(t.key, 0),
                    state=initial,
                )
                s.add(row)
                s.flush()
                assert row.id is not None
                ids[t.key] = row.id
            s.commit()
        rows: list[dict[str, Any]] = [
            {**t.__dict__, "violations": violations.get(t.key, [])} for t in ordered
        ]
        gitops.write_and_commit(
            self.project_id,
            {doc_path(project, "tasks"): render_tasks_md(project, rows)},
            f"tasks: {len(ordered)} tasks from plan",
        )
        set_phase(self.project_id, ProjectPhase.PLAN_REVIEW if review else ProjectPhase.EXECUTE)
        summary = f"Broke the plan into {len(ordered)} tasks."
        if violations:
            summary += f" {len(violations)} still exceed the size limits and need attention."
        planner_note(self.project_id, summary, self.job_id)
