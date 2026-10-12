# coding-agent

A self-hosted dashboard that takes a GitHub project through a spec-driven
SDLC with AI agents: interactive specification, then autonomous execution of
small, context-bounded subtasks on local (or cloud) models, with an
orchestrator that watches for runaway agents, replans after each task, and a
big red stop button.

Status: **Phase 1 — specifying.** See [`specs/000-orchestrator/`](specs/000-orchestrator/):

- [spec.md](specs/000-orchestrator/spec.md) — requirements
- [plan.md](specs/000-orchestrator/plan.md) — architecture & milestones (Python / FastAPI)
- [validation.md](specs/000-orchestrator/validation.md) — what "done" means
