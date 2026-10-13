"""Carry out decided checkpoints. Runs in the orchestrator (the only repo writer)."""

from __future__ import annotations

import asyncio
import logging

from foreman import gitops
from foreman.core import gates
from foreman.core.models import Gate, GateKind, GateState, Project, TaskState
from foreman.core.state import emit, transition
from foreman.db import session

log = logging.getLogger("foreman.gates")


async def handle(gate: Gate) -> None:
    from foreman.orchestrator import escalation, milestone, replan
    from foreman.orchestrator.runner import merge_task

    approved = gate.state == GateState.APPROVED
    assert gate.id is not None
    try:
        if gate.kind == GateKind.TASK_MERGE and gate.task_id is not None:
            if approved:
                try:
                    await asyncio.to_thread(merge_task, gate.task_id)
                except gitops.GitError as e:
                    transition(
                        gate.task_id,
                        (TaskState.AWAITING_MERGE,),
                        TaskState.FAILED,
                        f"merge failed: {e}",
                    )
            else:
                transition(
                    gate.task_id,
                    (TaskState.AWAITING_MERGE,),
                    TaskState.FAILED,
                    f"merge rejected by user{': ' + gate.note if gate.note else ''}",
                )
        elif gate.kind == GateKind.ESCALATION:
            if gate.task_id is not None:
                escalation.resolve_blocked(gate.task_id, approved)
            elif approved:  # acceptance keeps failing: let the replanner try again
                with session() as s:
                    p = s.get(Project, gate.project_id)
                    if p is not None:
                        p.acceptance_failures, p.acceptance_commit = 0, ""
                        s.add(p)
                        s.commit()
        elif gate.kind == GateKind.PLAN_REVISION:
            rev_id = gate.payload.get("revision_id")
            if rev_id is not None:
                if approved:
                    replan.apply_revision(rev_id)
                else:
                    replan.reject_revision(rev_id)
        elif gate.kind == GateKind.SPEC_CHANGE and approved:
            await asyncio.to_thread(replan.record_spec_change, gate.project_id, gate.payload)
        elif gate.kind == GateKind.MILESTONE and approved:
            await asyncio.to_thread(milestone.publish, gate.project_id)
    except Exception:  # noqa: BLE001 - one bad gate must not wedge the others
        log.exception("handling gate %s failed", gate.id)
        emit(gate.project_id, gate.task_id, "system", f"could not carry out: {gate.title}")
    gates.mark_handled(gate.id)
