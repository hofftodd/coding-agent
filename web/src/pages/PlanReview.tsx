import { useState } from "react";
import { patch, post } from "../api";
import { ActionButton, ErrorNote, Field, lines } from "../components";
import { usePoll } from "../hooks";
import type { Project, Task } from "../types";

function TaskEditor({ t, onSaved }: { t: Task; onSaved: () => void }) {
  const [f, setF] = useState({
    title: t.title,
    description: t.description,
    todos: t.todos.join("\n"),
    allowed_globs: t.allowed_globs.join("\n"),
    context_files: t.context_files.join("\n"),
    validation_cmd: t.validation_cmd,
  });
  const [error, setError] = useState("");
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  return (
    <form
      className="form task-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await patch(`/api/tasks/${t.id}`, {
            title: f.title,
            description: f.description,
            todos: lines(f.todos),
            allowed_globs: lines(f.allowed_globs),
            context_files: lines(f.context_files),
            validation_cmd: f.validation_cmd,
          });
          onSaved();
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <div className="grid2">
        <Field label="Title">
          <input value={f.title} onChange={(e) => set("title", e.target.value)} />
        </Field>
        <Field label="Done when">
          <input className="mono" value={f.validation_cmd} onChange={(e) => set("validation_cmd", e.target.value)} />
        </Field>
        <Field label="Description">
          <textarea rows={3} value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="Todos" hint="One per line">
          <textarea rows={3} value={f.todos} onChange={(e) => set("todos", e.target.value)} />
        </Field>
        <Field label="Files it may change" hint="One per line">
          <textarea rows={3} className="mono" value={f.allowed_globs} onChange={(e) => set("allowed_globs", e.target.value)} />
        </Field>
        <Field label="Interfaces it uses" hint="Read-only files, one per line">
          <textarea rows={3} className="mono" value={f.context_files} onChange={(e) => set("context_files", e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={error} />
      <button className="btn primary">Save &amp; re-check size</button>
    </form>
  );
}

function ProposedTask({ t, all, reload }: { t: Task; all: Task[]; reload: () => void }) {
  const [editing, setEditing] = useState(false);
  const deps = t.deps.map((d) => all.find((x) => x.id === d)?.key || `#${d}`);
  const tooBig = t.size_violations.length > 0;
  return (
    <div className={`card proposed ${tooBig ? "too-big" : ""}`}>
      <div className="row">
        <h3>
          <span className="muted">{t.key || `#${t.id}`}</span> {t.title}
        </h3>
        <div className="actions">
          <span className="small muted">~{t.pack_tokens.toLocaleString()} tokens</span>
          <button className="btn" onClick={() => setEditing((x) => !x)}>
            {editing ? "Close" : "Edit"}
          </button>
          <ActionButton
            kind="danger"
            onClick={async () => {
              await post(`/api/tasks/${t.id}/discard`);
              reload();
            }}
          >
            Drop
          </ActionButton>
        </div>
      </div>
      {tooBig && (
        <div className="error-note">
          Too big for the executor: {t.size_violations.join("; ")}. Edit or split it.
        </div>
      )}
      {editing ? (
        <TaskEditor
          t={t}
          onSaved={() => {
            setEditing(false);
            reload();
          }}
        />
      ) : (
        <div className="proposed-body">
          {t.description && <p className="pre">{t.description}</p>}
          <div className="small">
            {t.req_refs.length > 0 && (
              <div>
                <b>Implements:</b> {t.req_refs.join(", ")}
              </div>
            )}
            {deps.length > 0 && (
              <div>
                <b>After:</b> {deps.join(", ")}
              </div>
            )}
            <div>
              <b>Changes:</b> <span className="mono">{t.allowed_globs.join(", ") || "—"}</span>
            </div>
            {t.context_files.length > 0 && (
              <div>
                <b>Uses:</b> <span className="mono">{t.context_files.join(", ")}</span>
              </div>
            )}
            <div>
              <b>Done when:</b> <code>{t.validation_cmd || "(missing)"}</code>
            </div>
          </div>
          {t.todos.length > 0 && (
            <ul className="small">
              {t.todos.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function PlanReview({ project, onChange }: { project: Project; onChange: () => void }) {
  const { data: tasks, reload } = usePoll<Task[]>(`/api/projects/${project.id}/tasks`, 3000);
  const [error, setError] = useState("");
  const proposed = (tasks ?? []).filter((t) => t.state === "proposed");
  const blocked = proposed.filter((t) => t.size_violations.length > 0);
  const refresh = () => {
    reload();
    onChange();
  };

  return (
    <div>
      <section className="card review-head">
        <div>
          <h2>Review the plan</h2>
          <p className="muted small">
            The planner split the approved spec into {proposed.length} tasks, each sized to fit the executor model
            (max {project.max_task_files} files, ≤{project.context_budget_pct}% of its context window). Nothing runs
            until you approve.
          </p>
        </div>
        <div className="actions">
          <ActionButton
            onClick={async () => {
              await post(`/api/projects/${project.id}/spec/reopen`);
              refresh();
            }}
          >
            Back to spec
          </ActionButton>
          <ActionButton
            onClick={async () => {
              await post(`/api/projects/${project.id}/plan/redecompose`);
              refresh();
            }}
          >
            Re-run breakdown
          </ActionButton>
          <ActionButton
            kind="primary"
            disabled={proposed.length === 0 || blocked.length > 0}
            onClick={async () => {
              setError("");
              try {
                await post(`/api/projects/${project.id}/plan/approve`);
                refresh();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Approve plan &amp; start
          </ActionButton>
        </div>
      </section>
      <ErrorNote error={error} />
      {blocked.length > 0 && (
        <div className="error-note">
          {blocked.length} task(s) are too big for the executor. Edit them, drop them, or re-run the breakdown.
        </div>
      )}
      {proposed.map((t) => (
        <ProposedTask key={t.id} t={t} all={tasks ?? []} reload={reload} />
      ))}
    </div>
  );
}
