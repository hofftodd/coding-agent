import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { control, del, patch, post } from "../api";
import { ActionButton, ErrorNote, EventLog, Field, StateBadge, StopButton, lines } from "../components";
import { useEventStream, usePoll } from "../hooks";
import type { Node, Project, ProjectPhase, SystemInfo, Task, TaskState } from "../types";
import PlanReview from "./PlanReview";
import SpecWorkspace from "./SpecWorkspace";

const COLUMNS: { title: string; states: TaskState[] }[] = [
  { title: "Queued", states: ["queued"] },
  { title: "Running", states: ["running", "validating"] },
  { title: "Needs attention", states: ["failed", "stopped_by_user", "paused"] },
  { title: "Done", states: ["done"] },
];

function TaskCard({ t, all }: { t: Task; all: Task[] }) {
  const blockedBy = t.deps.filter((d) => all.find((x) => x.id === d)?.state !== "done");
  return (
    <Link to={`/tasks/${t.id}`} className={`task-card state-border-${t.state}`}>
      <div className="row">
        <span className="muted small">#{t.id}</span>
        <StateBadge state={t.state} />
      </div>
      <div className="task-title">{t.title}</div>
      {t.hold && t.state === "queued" && <div className="small warn">on hold</div>}
      {t.state === "queued" && blockedBy.length > 0 && (
        <div className="small muted">waits for {blockedBy.map((d) => `#${d}`).join(", ")}</div>
      )}
      {t.reason && t.state !== "done" && <div className="small reason">{t.reason}</div>}
      {t.attempts > 1 && <div className="small muted">attempt {t.attempts}</div>}
    </Link>
  );
}

function AddTask({ project, tasks, onDone }: { project: Project; tasks: Task[]; onDone: () => void }) {
  const { data: sys } = usePoll<SystemInfo>("/api/system", 10000);
  const [f, setF] = useState({
    title: "",
    description: "",
    todos: "",
    allowed_globs: "",
    validation_cmd: "",
    deps: [] as number[],
    executor_kind: "",
    executor_script: "",
    timeout_s: 1800,
  });
  const [error, setError] = useState("");
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const kind = f.executor_kind || project.executor_kind;

  return (
    <form
      className="card form"
      onSubmit={async (e) => {
        e.preventDefault();
        setError("");
        try {
          await post(`/api/projects/${project.id}/tasks`, {
            ...f,
            todos: lines(f.todos),
            allowed_globs: lines(f.allowed_globs),
          });
          onDone();
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <h2>Add task</h2>
      <div className="grid2">
        <Field label="Title">
          <input required value={f.title} onChange={(e) => set("title", e.target.value)} />
        </Field>
        <Field label="Validation command" hint="Exit 0 = task done. Runs in the sandbox.">
          <input required className="mono" value={f.validation_cmd} placeholder="pytest -q tests/test_parser.py" onChange={(e) => set("validation_cmd", e.target.value)} />
        </Field>
        <Field label="Description / requirement slice">
          <textarea rows={4} value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="Todos" hint="One per line">
          <textarea rows={4} value={f.todos} onChange={(e) => set("todos", e.target.value)} />
        </Field>
        <Field label="Allowed files" hint="Globs, one per line. Empty = unrestricted.">
          <textarea rows={3} className="mono" value={f.allowed_globs} placeholder={"src/parser.py\ntests/test_parser.py"} onChange={(e) => set("allowed_globs", e.target.value)} />
        </Field>
        <Field label="Depends on">
          <select multiple value={f.deps.map(String)} onChange={(e) => set("deps", Array.from(e.target.selectedOptions, (o) => Number(o.value)))}>
            {tasks.filter((t) => t.state !== "discarded").map((t) => (
              <option key={t.id} value={t.id}>
                #{t.id} {t.title}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Executor" hint={`Default: ${project.executor_kind}`}>
          <select value={f.executor_kind} onChange={(e) => set("executor_kind", e.target.value)}>
            <option value="">project default</option>
            {sys?.executors.map((x) => <option key={x}>{x}</option>)}
          </select>
        </Field>
        <Field label="Timeout (s)">
          <input type="number" min={10} value={f.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} />
        </Field>
        {kind === "shell" && (
          <Field label="Shell script" hint="The shell executor runs this instead of an AI agent">
            <textarea rows={4} className="mono" value={f.executor_script} onChange={(e) => set("executor_script", e.target.value)} />
          </Field>
        )}
      </div>
      <ErrorNote error={error} />
      <button className="btn primary">Queue task</button>
    </form>
  );
}

function RoleFields({
  label,
  nodes,
  binding,
  onChange,
  listId,
  allowCloud,
}: {
  label: string;
  nodes: Node[] | null;
  binding: { node_id: string; model: string };
  onChange: (b: { node_id: string; model: string }) => void;
  listId: string;
  allowCloud: boolean;
}) {
  const node = nodes?.find((n) => String(n.id) === binding.node_id);
  return (
    <>
      <Field label={`${label} node`}>
        <select value={binding.node_id} onChange={(e) => onChange({ ...binding, node_id: e.target.value })}>
          <option value="">— none —</option>
          {nodes
            ?.filter((n) => allowCloud || n.provider === "openai")
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.name} {n.provider === "anthropic" ? "(Claude)" : ""}
              </option>
            ))}
        </select>
      </Field>
      <Field label={`${label} model`}>
        <input list={listId} value={binding.model} onChange={(e) => onChange({ ...binding, model: e.target.value })} />
        <datalist id={listId}>{node?.models.map((m) => <option key={m} value={m} />)}</datalist>
      </Field>
    </>
  );
}

function Settings({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const { data: nodes } = usePoll<Node[]>("/api/nodes", 10000);
  const b = (role: string) => {
    const x = project.role_bindings[role] ?? { node_id: null, model: "" };
    return { node_id: x.node_id ? String(x.node_id) : "", model: x.model };
  };
  const [executor, setExecutor] = useState(b("executor"));
  const [planner, setPlanner] = useState(b("planner"));
  const [f, setF] = useState({
    test_cmd: project.test_cmd,
    executor_kind: project.executor_kind,
    push_after_merge: project.push_after_merge,
    max_task_files: project.max_task_files,
    context_budget_pct: project.context_budget_pct,
    plan_approval: project.gates?.plan_approval !== false,
  });
  const [error, setError] = useState("");
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const toBinding = (x: { node_id: string; model: string }) => ({
    node_id: x.node_id ? Number(x.node_id) : null,
    model: x.model,
  });
  return (
    <form
      className="card form"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await patch(`/api/projects/${project.id}`, {
            test_cmd: f.test_cmd,
            executor_kind: f.executor_kind,
            push_after_merge: f.push_after_merge,
            max_task_files: f.max_task_files,
            context_budget_pct: f.context_budget_pct,
            gates: { ...project.gates, plan_approval: f.plan_approval },
            role_bindings: {
              ...project.role_bindings,
              executor: toBinding(executor),
              planner: toBinding(planner),
            },
          });
          onSaved();
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <h2>Settings</h2>
      <h3>Models</h3>
      <div className="grid2">
        <RoleFields label="Planner" nodes={nodes} binding={planner} onChange={setPlanner} listId="planner-models" allowCloud />
        <RoleFields label="Executor" nodes={nodes} binding={executor} onChange={setExecutor} listId="executor-models" allowCloud={false} />
        <Field label="Executor">
          <select value={f.executor_kind} onChange={(e) => set("executor_kind", e.target.value)}>
            <option>opencode</option>
            <option>shell</option>
          </select>
        </Field>
      </div>
      <h3>Tasks</h3>
      <div className="grid2">
        <Field label="Max files per task">
          <input type="number" min={1} max={20} value={f.max_task_files} onChange={(e) => set("max_task_files", Number(e.target.value))} />
        </Field>
        <Field label="Context budget (% of executor window)">
          <input type="number" min={5} max={90} value={f.context_budget_pct} onChange={(e) => set("context_budget_pct", Number(e.target.value))} />
        </Field>
        <Field label="Project test command" hint="Runs after every task to catch regressions">
          <input className="mono" value={f.test_cmd} onChange={(e) => set("test_cmd", e.target.value)} />
        </Field>
      </div>
      <h3>Checkpoints</h3>
      <div className="grid2">
        <Field label="Review the task breakdown before it runs">
          <input type="checkbox" checked={f.plan_approval} onChange={(e) => set("plan_approval", e.target.checked)} />
        </Field>
        <Field label="Push milestone branch after each merge">
          <input type="checkbox" checked={f.push_after_merge} onChange={(e) => set("push_after_merge", e.target.checked)} />
        </Field>
      </div>
      <ErrorNote error={error} />
      <button className="btn primary">Save</button>
    </form>
  );
}

const PHASES: { phase: ProjectPhase; label: string }[] = [
  { phase: "specify", label: "1. Specify" },
  { phase: "plan_review", label: "2. Review plan" },
  { phase: "execute", label: "3. Execute" },
];

function PhaseStepper({ phase }: { phase: ProjectPhase }) {
  const current = phase === "decomposing" ? "plan_review" : phase;
  const idx = PHASES.findIndex((p) => p.phase === current);
  return (
    <div className="stepper">
      {PHASES.map((p, i) => (
        <span key={p.phase} className={`step ${i < idx ? "done" : i === idx ? "current" : ""}`}>
          {p.label}
          {p.phase === "plan_review" && phase === "decomposing" && " (planning…)"}
        </span>
      ))}
    </div>
  );
}

export default function ProjectPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const { data: project, error, reload } = usePoll<Project>(`/api/projects/${id}`, 2000);
  const { data: tasks, reload: reloadTasks } = usePoll<Task[]>(`/api/projects/${id}/tasks`, 2000);
  const { events, connected } = useEventStream({ project_id: id });
  const [panel, setPanel] = useState<"" | "add" | "settings">("");

  if (!project) return <ErrorNote error={error} />;
  const all = tasks ?? [];
  const active = all.some((t) => t.state === "running" || t.state === "validating");
  const activity = events.filter((e) => e.kind !== "log" && e.kind !== "test");

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <Link to="/">Projects</Link> /
          </div>
          <h1>
            {project.name} {project.paused && <span className="badge state-paused">paused</span>}
          </h1>
          <div className="muted mono small">
            {project.repo_url} → {project.milestone_branch}
          </div>
          <PhaseStepper phase={project.phase} />
        </div>
        <div className="actions">
          {project.phase === "execute" && (
            <button className="btn" onClick={() => setPanel(panel === "add" ? "" : "add")}>
              {panel === "add" ? "Cancel" : "Add task"}
            </button>
          )}
          <button className="btn" onClick={() => setPanel(panel === "settings" ? "" : "settings")}>
            Settings
          </button>
          {project.paused ? (
            <ActionButton kind="primary" onClick={async () => { await control("project", "resume", id); reload(); }}>
              Resume
            </ActionButton>
          ) : (
            <ActionButton onClick={async () => { await control("project", "pause", id); reload(); }}>
              Pause
            </ActionButton>
          )}
          <StopButton
            size="big"
            label="STOP PROJECT"
            onStop={async () => {
              await control("project", "stop", id);
              reload();
              reloadTasks();
            }}
          />
          <ActionButton
            kind="danger"
            disabled={active}
            onClick={async () => {
              if (!confirm(`Delete project "${project.name}" and its local clone?`)) return;
              await del(`/api/projects/${id}`);
              navigate("/");
            }}
          >
            Delete
          </ActionButton>
        </div>
      </div>

      {panel === "add" && (
        <AddTask project={project} tasks={all} onDone={() => { setPanel(""); reloadTasks(); }} />
      )}
      {panel === "settings" && <Settings project={project} onSaved={() => { setPanel(""); reload(); }} />}

      {(project.phase === "specify" || project.phase === "decomposing") && <SpecWorkspace project={project} />}
      {project.phase === "plan_review" && <PlanReview project={project} onChange={() => { reload(); reloadTasks(); }} />}
      {project.phase === "execute" && (
      <>
      <div className="board">
        {COLUMNS.map((col) => {
          const items = all.filter((t) => col.states.includes(t.state));
          return (
            <section key={col.title} className="column">
              <h3>
                {col.title} <span className="muted">{items.length}</span>
              </h3>
              {items.map((t) => <TaskCard key={t.id} t={t} all={all} />)}
            </section>
          );
        })}
      </div>

      <section className="card">
        <h2>
          Activity <span className={`dot ${connected ? "ok" : "bad"}`}>{connected ? "live" : "reconnecting"}</span>
        </h2>
        <EventLog events={activity} />
      </section>
      </>
      )}
    </div>
  );
}
