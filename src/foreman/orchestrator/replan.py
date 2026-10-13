"""Replanning (FR-6): after each completed task, a reviewer model checks that the
plan still leads to the goal and proposes changes to the remaining tasks.

The reviewer answers with a JSON object::

    {"summary": "...",
     "ops": [{"op": "add", "task": {...task fields...}, "reason": "..."},
             {"op": "remove", "key": "T5", "reason": "..."},
             {"op": "modify", "key": "T6", "changes": {...}, "reason": "..."}],
     "spec_change": null | {"reason": "...", "proposal": "..."}}

Rules enforced here, not just asked for:
- only tasks that haven't started (queued / proposed / blocked) can be changed;
- every op needs a reason;
- added or modified tasks must pass the same size checks as the original plan;
- tasks added by replanning are capped at ``growth_cap_pct`` of the approved
  plan; beyond that the revision waits for approval;
- spec changes are never applied automatically (a checkpoint, always on);
- with the ``replan_approval`` checkpoint on, every non-empty revision waits.

Every revision is recorded (PlanRevision) and tasks.md is re-rendered.
"""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from foreman import gitops
from foreman.core import gates, watchdog
from foreman.core.contextpack import budget_tokens, repo_files
from foreman.core.models import (
    GateKind,
    JobKind,
    JobState,
    Node,
    PlannerJob,
    PlanRevision,
    Project,
    RevisionState,
    Task,
    TaskState,
)
from foreman.core.state import emit, transition
from foreman.db import session
from foreman.planner import PlanError, TaskSpec, doc_path, parse_tasks, retry_message

if TYPE_CHECKING:
    from foreman.orchestrator.jobs import JobRunner

MODIFIABLE = (TaskState.QUEUED, TaskState.PROPOSED, TaskState.BLOCKED)
EDITABLE_FIELDS = (
    "title",
    "description",
    "todos",
    "allowed_files",
    "context_files",
    "validation_cmd",
    "req_refs",
)


def queue_review(
    project_id: int, trigger: str, task_id: int | None = None, output: str = ""
) -> bool:
    """Queue a replanning job unless one is already waiting for this project."""
    with session() as s:
        project = s.get(Project, project_id)
        if project is None or not watchdog.limit(project, "replan"):
            return False
        if not ((project.role_bindings or {}).get("planner") or {}).get("node_id"):
            return False
        waiting = s.exec(
            select(PlannerJob).where(
                PlannerJob.project_id == project_id,
                PlannerJob.kind == JobKind.REVIEW,
                PlannerJob.state == JobState.QUEUED,
            )
        ).first()
        if waiting is not None:
            return False
        s.add(
            PlannerJob(
                project_id=project_id,
                kind=JobKind.REVIEW,
                task_id=task_id,
                payload={"trigger": trigger, "output": output[-6000:]},
            )
        )
        s.commit()
    return True


def review_system_prompt(
    project: Project,
    docs: dict[str, str],
    tasks: list[Task],
    trigger: str,
    evidence: str,
    budget: int,
    tree: list[str],
) -> str:
    keys = {t.id: t.key or f"#{t.id}" for t in tasks}
    table = [
        {
            "key": keys[t.id],
            "state": t.state.value,
            "title": t.title,
            "allowed_files": t.allowed_globs,
            "deps": [keys.get(d, d) for d in t.deps],
            "validation_cmd": t.validation_cmd,
            "req_refs": t.req_refs,
        }
        for t in tasks
    ]
    d = f"specs/{project.feature}"
    return "\n".join(
        [
            "You review the plan of a software project while coding agents work through it. "
            f"Something just happened: {trigger}. Decide whether the remaining tasks still lead to "
            "the goal (all acceptance tests passing) and propose the SMALLEST changes needed. "
            "If the plan is on track, return no ops.",
            "",
            "Answer with ONLY a JSON object:",
            '{"summary": "one sentence", "ops": [...], "spec_change": null}',
            "Ops:",
            '- {"op": "add", "task": {"key": "T9", "title": "...", "description": "...", '
            '"req_refs": [], "todos": [], "allowed_files": ["..."], "context_files": [], '
            '"validation_cmd": "...", "deps": ["T3"]}, "reason": "..."}',
            '- {"op": "remove", "key": "T5", "reason": "..."}',
            '- {"op": "modify", "key": "T6", "changes": {"description": "..."}, "reason": "..."}',
            "Only tasks in state queued, proposed or blocked can be removed or modified. "
            "Every op needs a reason.",
            f"New tasks change at most {project.max_task_files} files (listed explicitly) and fit "
            f"in about {budget} tokens of context.",
            'If the SPEC itself is wrong or incomplete, set "spec_change": {"reason": "...", '
            '"proposal": "..."}: a person will decide.',
            "",
            "## What happened",
            evidence.strip() or "(no details)",
            "",
            "## Tasks",
            json.dumps(table, indent=1),
            "",
            f"## {d}/spec.md",
            docs.get("spec", "").strip() or "(empty)",
            "",
            f"## {d}/plan.md",
            docs.get("plan", "").strip() or "(empty)",
            "",
            f"Acceptance command: {project.acceptance_cmd or '(not set)'}",
            "",
            "## Repository files",
            *(tree[:200] or ["(empty)"]),
        ]
    )


def extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise PlanError("no JSON object found in the response")
        candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise PlanError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise PlanError("expected a JSON object")
    return data


def validate_ops(
    data: dict[str, Any], tasks: list[Task]
) -> tuple[list[dict[str, Any]], list[TaskSpec]]:
    """Check the reviewer's ops against the task list (raises PlanError)."""
    by_key = {t.key or f"#{t.id}": t for t in tasks}
    ops = data.get("ops") or []
    if not isinstance(ops, list):
        raise PlanError("ops must be a list")
    problems: list[str] = []
    added: list[dict[str, Any]] = []
    clean: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            problems.append(f"op {i} is not an object")
            continue
        kind, reason = op.get("op"), str(op.get("reason") or "").strip()
        if not reason:
            problems.append(f"op {i} ({kind}) has no reason")
        if kind == "add":
            task = op.get("task")
            if not isinstance(task, dict):
                problems.append(f"op {i}: add needs a task object")
                continue
            key = str(task.get("key") or "").strip()
            if not key or key in by_key:
                problems.append(f"op {i}: new task needs a unique key (got {key!r})")
                continue
            added.append(task)
            clean.append({"op": "add", "key": key, "task": task, "reason": reason})
        elif kind in ("remove", "modify"):
            key = str(op.get("key") or "")
            t = by_key.get(key)
            if t is None:
                problems.append(f"op {i}: unknown task {key!r}")
                continue
            if t.state not in MODIFIABLE:
                problems.append(f"op {i}: task {key} is {t.state.value}; it can't be changed")
                continue
            entry: dict[str, Any] = {"op": kind, "key": key, "reason": reason}
            if kind == "modify":
                changes = {
                    k: v for k, v in (op.get("changes") or {}).items() if k in EDITABLE_FIELDS
                }
                if not changes:
                    problems.append(f"op {i}: modify of {key} changes nothing editable")
                    continue
                entry["changes"] = changes
            clean.append(entry)
        else:
            problems.append(f"op {i}: unknown op {kind!r}")
    specs: list[TaskSpec] = []
    if added:
        # Validate new tasks as a plan of their own; deps may point at existing tasks.
        stub_keys = [k for k in by_key if k not in {a.get("key") for a in added}]
        stubs = [{"key": k, "title": "existing", "deps": []} for k in stub_keys]
        try:
            parsed = parse_tasks(json.dumps(stubs + added))
            specs = [p for p in parsed if p.key not in stub_keys]
        except PlanError as e:
            problems.append(str(e))
    if problems:
        raise PlanError("; ".join(problems))
    return clean, specs


async def run_review(job: JobRunner, node: Node, model: str) -> None:
    """Body of a REVIEW planner job."""
    from foreman.orchestrator.jobs import JobFailed, finish_job, read_docs

    project = job.project()
    assert project.id is not None
    with session() as s:
        tasks = list(
            s.exec(
                select(Task)
                .where(
                    Task.project_id == project.id,
                    col(Task.state).not_in((TaskState.DISCARDED, TaskState.SPLIT)),
                )
                .order_by(col(Task.id))
            ).all()
        )
    trigger = job.payload.get("trigger", "a task finished")
    evidence = job.payload.get("output", "")
    if job.task_id is not None:
        done = next((t for t in tasks if t.id == job.task_id), None)
        if done is not None:
            diff = gitops.diff(gitops.task_dir(project.id, job.task_id))[:6000]
            evidence = (
                f"Task {done.key}: {done.title} is done.\n"
                f"Its changes:\n{diff or '(no diff available)'}\n{evidence}"
            )
    docs = read_docs(project)
    window = job._executor_window(project)
    system = review_system_prompt(
        project,
        docs,
        tasks,
        trigger,
        evidence,
        budget_tokens(project, window),
        repo_files(gitops.repo_dir(project.id)),
    )
    messages = [{"role": "user", "content": "Review the plan now."}]
    data: dict[str, Any] = {}
    ops: list[dict[str, Any]] = []
    specs: list[TaskSpec] = []
    for attempt in range(2):
        text = await job._call(node, model, system, messages)
        try:
            data = extract_json_object(text)
            ops, specs = validate_ops(data, tasks)
            violations, _ = job._check_sizes(project, specs, window)
            if violations:
                raise PlanError(
                    "; ".join(f"task {k}: {', '.join(v)}" for k, v in violations.items())
                )
            break
        except PlanError as e:
            if attempt == 1:
                raise JobFailed(f"unusable plan review: {e}") from e
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": retry_message([str(e)]).replace("JSON array", "JSON object"),
                },
            ]
    if not finish_job(job.job_id, (JobState.RUNNING,), JobState.DONE):
        return
    record_revision(project, trigger, data, ops)


def record_revision(
    project: Project, trigger: str, data: dict[str, Any], ops: list[dict[str, Any]]
) -> PlanRevision:
    assert project.id is not None
    spec_change = data.get("spec_change") if isinstance(data.get("spec_change"), dict) else {}
    with session() as s:
        version = 1 + len(
            s.exec(select(PlanRevision).where(PlanRevision.project_id == project.id)).all()
        )
        added_before = len(
            s.exec(select(Task).where(Task.project_id == project.id, Task.origin == "replan")).all()
        )
    adds = sum(1 for o in ops if o["op"] == "add")
    cap = math.ceil(project.baseline_tasks * watchdog.limit(project, "growth_cap_pct") / 100)
    over_cap = adds > 0 and added_before + adds > cap
    needs_approval = bool(ops) and (over_cap or gates.enabled(project, "replan_approval"))
    rev = PlanRevision(
        project_id=project.id,
        version=version,
        trigger=trigger,
        summary=str(data.get("summary") or "")[:500],
        ops=ops,
        spec_change=spec_change or {},
        state=(
            RevisionState.EMPTY
            if not ops
            else RevisionState.PENDING
            if needs_approval
            else RevisionState.APPLIED
        ),
    )
    with session() as s:
        s.add(rev)
        s.commit()
        s.refresh(rev)
    assert rev.id is not None
    emit(
        project.id,
        None,
        "replan",
        f"plan revision {version}: {rev.summary or 'no changes'} ({len(ops)} change(s))",
        revision_id=rev.id,
        ops=len(ops),
    )
    if rev.state == RevisionState.APPLIED:
        apply_revision(rev.id)
    elif rev.state == RevisionState.PENDING:
        why = (
            f"it would add {added_before + adds} tasks to a plan of "
            f"{project.baseline_tasks} (cap {cap})"
            if over_cap
            else "plan revisions need approval in this project"
        )
        gate = gates.open_gate(
            project.id,
            GateKind.PLAN_REVISION,
            f"Plan revision {version}: {len(ops)} change(s)",
            f"{rev.summary}\n\nWaiting because {why}.\n\n" + describe_ops(ops),
            payload={"revision_id": rev.id},
        )
        with session() as s:
            row = s.get(PlanRevision, rev.id)
            assert row is not None
            row.gate_id = gate.id
            s.add(row)
            s.commit()
    if not ops and trigger.startswith("the acceptance tests failed"):
        gates.open_gate(
            project.id,
            GateKind.ESCALATION,
            "Acceptance fails and the replanner adds no work",
            f"{rev.summary}\n\nAll tasks are done, `{project.acceptance_cmd}` still fails, and "
            "the reviewer proposed no new tasks. Approve to let it try again, or reject to "
            "leave it for you to fix.",
            payload={"acceptance": True},
        )
    if spec_change:
        gates.open_gate(
            project.id,
            GateKind.SPEC_CHANGE,
            "The reviewer proposes a spec change",
            f"Why: {spec_change.get('reason', '')}\n\nProposal:\n{spec_change.get('proposal', '')}"
            "\n\nApprove to record it in spec.md; the plan is not changed automatically.",
            payload={"revision_id": rev.id, **spec_change},
        )
    return rev


def describe_ops(ops: list[dict[str, Any]]) -> str:
    lines = []
    for o in ops:
        if o["op"] == "add":
            lines.append(f"+ add {o['key']}: {o['task'].get('title', '')} — {o['reason']}")
        elif o["op"] == "remove":
            lines.append(f"- remove {o['key']} — {o['reason']}")
        else:
            lines.append(f"~ modify {o['key']} ({', '.join(o.get('changes', {}))}) — {o['reason']}")
    return "\n".join(lines)


def apply_revision(revision_id: int) -> None:
    """Apply a revision's ops to the task table (orchestrator side)."""
    from foreman.orchestrator.jobs import write_tasks_md

    with session() as s:
        rev = s.get(PlanRevision, revision_id)
        assert rev is not None
        project = s.get(Project, rev.project_id)
        assert project is not None
        tasks = s.exec(select(Task).where(Task.project_id == rev.project_id)).all()
    by_key = {t.key or f"#{t.id}": t for t in tasks}
    for op in rev.ops:
        if op["op"] == "add":
            t = op["task"]
            with session() as s:
                row = Task(
                    project_id=rev.project_id,
                    key=op["key"],
                    title=str(t.get("title", "")),
                    description=str(t.get("description", "")),
                    todos=list(t.get("todos") or []),
                    req_refs=list(t.get("req_refs") or []),
                    allowed_globs=list(t.get("allowed_files") or []),
                    context_files=list(t.get("context_files") or []),
                    validation_cmd=str(t.get("validation_cmd", "")),
                    deps=[d.id for k in (t.get("deps") or []) if (d := by_key.get(k)) and d.id],
                    origin="replan",
                    state=TaskState.QUEUED,
                )
                s.add(row)
                s.commit()
                s.refresh(row)
            by_key[op["key"]] = row
            emit(rev.project_id, row.id, "replan", f"added {op['key']}: {op['reason']}")
        elif op["op"] == "remove":
            t = by_key.get(op["key"])
            if (
                t is not None
                and t.id is not None
                and transition(
                    t.id, MODIFIABLE, TaskState.DISCARDED, f"removed by replanning: {op['reason']}"
                )
            ):
                emit(rev.project_id, t.id, "replan", f"removed {op['key']}: {op['reason']}")
        else:
            t = by_key.get(op["key"])
            if t is None or t.id is None:
                continue
            with session() as s:
                current = s.get(Task, t.id)
                if current is None or current.state not in MODIFIABLE:
                    continue
                for k, v in op.get("changes", {}).items():
                    setattr(current, "allowed_globs" if k == "allowed_files" else k, v)
                s.add(current)
                s.commit()
            emit(rev.project_id, t.id, "replan", f"modified {op['key']}: {op['reason']}")
    with session() as s:
        applied = s.get(PlanRevision, revision_id)
        assert applied is not None
        applied.state = RevisionState.APPLIED
        s.add(applied)
        s.commit()
    write_tasks_md(project)


def reject_revision(revision_id: int) -> None:
    with session() as s:
        rev = s.get(PlanRevision, revision_id)
        if rev is not None and rev.state == RevisionState.PENDING:
            rev.state = RevisionState.REJECTED
            s.add(rev)
            s.commit()


def record_spec_change(project_id: int, payload: dict[str, Any]) -> None:
    """Append an approved spec change to spec.md (the user can edit it further)."""
    with session() as s:
        project = s.get(Project, project_id)
        assert project is not None
    path = doc_path(project, "spec")
    spec = gitops.read_repo_file(project_id, path).rstrip()
    heading = "## Changes approved during execution"
    entry = f"- {payload.get('proposal', '').strip()} (why: {payload.get('reason', '').strip()})"
    spec = f"{spec}\n\n{entry}\n" if heading in spec else f"{spec}\n\n{heading}\n\n{entry}\n"
    gitops.write_and_commit(project_id, {path: spec}, "spec: record approved change")
