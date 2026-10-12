# Foreman

A self-hosted dashboard that takes a GitHub project through a spec-driven
SDLC with AI agents: interactive specification, then autonomous execution of
small, context-bounded subtasks on local (or cloud) models, with an
orchestrator that watches for runaway agents, replans after each task, and a
big red stop button.

Status: **M1 (walking skeleton + stop controls) implemented.** Specs, plan and
acceptance criteria live in [`specs/000-orchestrator/`](specs/000-orchestrator/).

## What M1 does

- Add a project from a git URL; it is cloned and a milestone branch is created.
- Register inference nodes (any OpenAI-compatible endpoint) and bind a node +
  model to the project's executor role.
- Queue tasks by hand: description, todos, allowed files, validation command,
  dependencies.
- The orchestrator runs ready tasks one at a time per project (respecting node
  capacity) in a Docker container built from `ubuntu:24.04`, using OpenCode as
  the coding agent. It then commits, checks the scope fence, runs the task's
  validation command and the project test command in the sandbox, and merges
  into the milestone branch.
- Live dashboard: task board, streaming agent log, diff, run history.
- **Pause / Stop / E-stop** at task, project and global level, enforced by a
  separate supervisor process that works even if the orchestrator hangs.
  Stopped work is preserved and can be retried or discarded.

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, Docker, git.

```bash
uv sync                          # Python deps
(cd web && npm ci && npm run build)   # dashboard
uv run foreman build-image       # foreman/executor-base (ubuntu:24.04 + git + opencode)
uv run foreman up                # api + orchestrator + supervisor
```

Open http://127.0.0.1:8700, add a node under **Nodes** (e.g.
`http://192.168.1.20:8080/v1` for a llama.cpp server), then add a project and
tasks.

A model server running on the same host as foreman is reachable from executor
containers at `http://host.docker.internal:<port>/v1`.

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `FOREMAN_DATA_DIR` | `./data` | Databases, project clones, run logs |
| `FOREMAN_HOST` / `FOREMAN_PORT` | `127.0.0.1` / `8700` | API bind address |
| `FOREMAN_TOKEN` | *(empty)* | If set, required as `Authorization: Bearer …` |
| `FOREMAN_RUNTIME` | `docker` | `local` runs executors as host processes (tests/dev only, **no isolation**) |
| `FOREMAN_EXECUTOR_IMAGE` | `foreman/executor-base:latest` | Image for executor containers |
| `FOREMAN_CONTAINER_CPUS` / `_MEMORY` | `2` / `4g` | Per-container limits |
| `FOREMAN_STOP_GRACE_S` | `5` | SIGTERM → SIGKILL grace on Stop |
| `FOREMAN_GITHUB_TOKEN` | *(empty)* | Used by the orchestrator for clone/push of private repos; never passed to executors or written to `.git/config` |

## Architecture

Three OS processes share two SQLite databases:

- **api** (FastAPI): REST + Server-Sent Events + the React dashboard.
- **orchestrator**: schedules ready tasks and runs each attempt
  (clone → execute → commit → scope check → validate → merge).
- **supervisor**: executes Stop/E-stop. It kills containers/process groups
  first, then records the outcome.

`foreman.db` holds projects, tasks, runs and events. `control.db` holds stop
requests and the E-stop flag and is never written by the orchestrator, so a
hung orchestrator holding a write lock cannot block a Stop.

Each task works in its own standalone clone (`data/projects/<id>/tasks/<task>`),
mounted at `/workspace` in the container. The rendered prompt and tool config
are mounted read-only at `/foreman`. Nothing else from the host is visible.

## Development

```bash
uv run pytest -q                 # unit, integration, multi-process stop tests;
                                 # Docker tests run when the image is present
uv run ruff check src tests && uv run mypy src
(cd web && npm run dev)          # dashboard with hot reload, proxies /api to :8700
scripts/e2e.sh                   # full stack + Playwright browser tests
python -m tests.fakellm 8999     # scripted OpenAI-compatible model for demos
```
