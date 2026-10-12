# Plan 000 — Local Agent Coding Orchestrator

Status: **Draft** · Implements: [spec.md](spec.md)

## 1. Stack

| Concern | Choice |
|---|---|
| Language / packaging | Python 3.12+, `uv` |
| API | FastAPI + Uvicorn |
| Live updates | Server-Sent Events (`sse-starlette`) |
| Persistence | SQLite (WAL) via SQLModel / SQLAlchemy; Alembic migrations |
| Model routing | LiteLLM (Python library) |
| Executors | OpenCode (default), Aider — subprocess in Docker |
| Git / GitHub | `git` CLI (worktrees), GitHub REST via `httpx` |
| Frontend | React + Vite + TypeScript (see OQ-1), served as static files by FastAPI |
| Tests | pytest, pytest-asyncio; Playwright for UI e2e |
| Lint / types | ruff, mypy (strict on `core/`) |

## 2. Process architecture

```
┌──────────── mini PC ─────────────────────────────────────────────┐
│                                                                  │
│  Browser ──HTTP/SSE──► api (FastAPI)                             │
│                          │  writes commands / stop flags         │
│                          ▼                                       │
│                       SQLite  ◄──────────────┐                   │
│                          ▲                   │                   │
│                          │                   │                   │
│                    orchestrator          supervisor              │
│              (scheduler, planner,     (polls stop flags,         │
│               watchdog, replanner)     kills process groups /    │
│                          │             containers; heartbeats)   │
│                          ▼                   │                   │
│                 executor sandboxes ◄─────────┘                   │
│              (Docker, one per running task, own worktree)        │
└──────────────────────────┬───────────────────────────────────────┘
                           │ OpenAI-compatible HTTP (LAN) / cloud
              ┌────────────┴────────────┐
        Mac Studio (MLX/llama.cpp)   R9700 (llama.cpp)      Cloud APIs
```

- `api`, `orchestrator`, `supervisor` are separate OS processes (one repo,
  three entry points) so a hung orchestrator cannot block Stop.
- The DB is the only channel between them: commands table, stop flags,
  heartbeats. Supervisor is deliberately tiny and has no model calls.

## 3. Package layout

```
src/foreman/            # working package name
  api/                  # FastAPI routers, SSE stream, static UI mount
  core/
    models.py           # SQLModel tables
    state.py            # task/project state machines
    contextpack.py      # build + size context packs (token counting)
    watchdog.py         # pure functions: (task run events) -> verdicts
  orchestrator/
    scheduler.py        # ready-queue, node slots
    planner.py          # phase-1 dialogue, decomposition
    replanner.py        # post-task review, plan ops
    runner.py           # launch executor, collect results, run validation
  executors/
    base.py             # Executor protocol
    opencode.py
    aider.py
  llm/router.py         # LiteLLM wrapper, role→endpoint, spend tracking
  gitops/               # clone, worktree, merge, PR
  supervisor/main.py
web/                    # React app
tests/
specs/
```

## 4. Key data model (abridged)

- `Project(id, repo_url, workspace_path, phase, autonomy_gates, budgets, role_bindings)`
- `Node(id, base_url, capacity, models, healthy, last_seen)`
- `Task(id, project_id, title, req_refs, todos, allowed_globs, validation_cmd, deps, state, budgets, attempt)`
- `TaskRun(id, task_id, node_id, model, started, ended, outcome, tokens, cost, diff_hash, error_sig)`
- `Event(id, project_id, task_id, kind, payload, ts)` — transcript, tool calls, watchdog, gates
- `PlanRevision(id, project_id, version, ops, reason, approved_by)`
- `Gate(id, project_id, kind, payload, state)`
- `Control(id, scope, target_id, action[pause|stop|estop], created, acknowledged)`

Task states: `queued → running → validating → done`, plus `blocked`,
`waiting_gate`, `stopped_by_user`, `failed`, `discarded`.

## 5. Context pack

Built per task attempt, persisted for audit:
1. System prompt for the executor role (rules, scope fence, how to run tests).
2. Requirement slice (spec excerpts referenced by `req_refs`).
3. Todos.
4. Interface context: signatures/types of symbols the task depends on
   (extracted with tree-sitter/ast, not whole files).
5. Contents of allowed files (current).
6. Validation command and, on retry, a summary of the previous failure
   (never the full prior transcript).

Size is token-counted against the executor model's configured usable context;
over-budget ⇒ the task is returned to the planner to split.

## 6. Milestones

### M0 — Spike (no app code)
Manually run Spec Kit-style spec → decomposition → OpenCode/Aider on the R9700
and Mac against a toy repo. Record: which models follow tool protocols, real
usable context, tokens/s, typical failure modes. Output: `specs/000-orchestrator/spike-notes.md`
and default role bindings.

### M1 — Walking skeleton + stop
- Project add/clone, node registry, role bindings.
- Manual task creation; sequential execution on one node via OpenCode in Docker.
- Validation run, merge to milestone branch.
- Supervisor + Pause/Stop/E-stop end-to-end.
- Live task view (SSE transcript, diff, test output).

### M2 — Specify & decompose
- Phase 1 chat with planner, doc editing, commits to `specs/`.
- Validation tests generation; Phase 1 approval gate.
- Decomposition with size enforcement and context packs.

### M3 — Autonomy
- Watchdog rules + escalation ladder.
- Replanner with plan revisions, growth cap, spec-change gate.
- Configurable gates + "Needs you" inbox + ntfy notifications.
- Milestone PRs on GitHub.

### M4 — Multi-node & polish
- Multi-node scheduling with capacities, node health view.
- Cloud spend tracking and caps.
- Portfolio dashboard, validation trend charts.
- Aider executor; crash-recovery hardening.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Local executors unreliable at tool use | M0 spike picks models; escalation ladder; tiny tasks |
| Mac prefill latency | Small context packs; Mac reserved for planner/escalation |
| Replanner churn | Growth cap, reasons required, spec-change gate |
| Validation tests themselves wrong | Phase 1 human approval; tests changes always gated |
| Executor CLI interface drift | `Executor` protocol + pinned versions + contract tests |
