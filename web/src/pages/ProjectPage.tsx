import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { control, del, patch, post } from "../api";
import { ActionButton, ErrorNote, EventLog, Field, StateBadge, StopButton, lines } from "../components";
import { useEventStream, usePoll } from "../hooks";
import type { Node, Project, SystemInfo, Task, TaskState } from "../types";

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

function Settings({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const { data: nodes } = usePoll<Node[]>("/api/nodes", 10000);
  const binding = project.role_bindings.executor ?? { node_id: null, model: "" };
  const [f, setF] = useState({
    test_cmd: project.test_cmd,
    executor_kind: project.executor_kind,
    node_id: binding.node_id ? String(binding.node_id) : "",
    model: binding.model,
    push_after_merge: project.push_after_merge,
  });
  const [error, setError] = useState("");
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const node = nodes?.find((n) => String(n.id) === f.node_id);
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
            role_bindings: {
              ...project.role_bindings,
              executor: { node_id: f.node_id ? Number(f.node_id) : null, model: f.model },
            },
          });
          onSaved();
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <h2>Settings</h2>
      <div className="grid2">
        <Field label="Project test command">
          <input className="mono" value={f.test_cmd} onChange={(e) => set("test_cmd", e.target.value)} />
        </Field>
        <Field label="Executor">
          <select value={f.executor_kind} onChange={(e) => set("executor_kind", e.target.value)}>
            <option>opencode</option>
            <option>shell</option>
          </select>
        </Field>
        <Field label="Executor node">
          <select value={f.node_id} onChange={(e) => set("node_id", e.target.value)}>
            <option value="">— none —</option>
            {nodes?.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
          </select>
        </Field>
        <Field label="Executor model">
          <input list="settings-models" value={f.model} onChange={(e) => set("model", e.target.value)} />
          <datalist id="settings-models">{node?.models.map((m) => <option key={m} value={m} />)}</datalist>
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
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setPanel(panel === "add" ? "" : "add")}>
            {panel === "add" ? "Cancel" : "Add task"}
          </button>
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
    </div>
  );
}
