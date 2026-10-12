import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { control, post } from "../api";
import { ActionButton, ErrorNote, EventLog, StateBadge, StopButton, fmtDuration, fmtTime } from "../components";
import { useEventStream, usePoll } from "../hooks";
import type { Task } from "../types";

type Tab = "log" | "diff" | "details";

function DiffView({ taskId, live }: { taskId: number; live: boolean }) {
  const { data, error } = usePoll<{ diff: string; files: string[] }>(`/api/tasks/${taskId}/diff`, live ? 3000 : 60000);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <div className="muted">Loading…</div>;
  if (!data.diff) return <div className="muted">No changes yet.</div>;
  return (
    <div>
      <div className="small muted">{data.files.length} file(s): {data.files.join(", ")}</div>
      <pre className="diff">
        {data.diff.split("\n").map((l, i) => (
          <div
            key={i}
            className={
              l.startsWith("+++") || l.startsWith("---")
                ? "d-file"
                : l.startsWith("+")
                  ? "d-add"
                  : l.startsWith("-")
                    ? "d-del"
                    : l.startsWith("@@")
                      ? "d-hunk"
                      : ""
            }
          >
            {l || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}

function Details({ t }: { t: Task }) {
  return (
    <div className="details">
      {t.description && (
        <>
          <h3>Description</h3>
          <p className="pre">{t.description}</p>
        </>
      )}
      {t.todos.length > 0 && (
        <>
          <h3>Todos</h3>
          <ul>{t.todos.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </>
      )}
      <h3>Done when</h3>
      <pre className="mono block">{t.validation_cmd}</pre>
      <h3>Allowed files</h3>
      {t.allowed_globs.length ? (
        <ul className="mono">{t.allowed_globs.map((g) => <li key={g}>{g}</li>)}</ul>
      ) : (
        <p className="muted">Unrestricted</p>
      )}
      {t.deps.length > 0 && (
        <>
          <h3>Depends on</h3>
          <p>{t.deps.map((d) => <Link key={d} to={`/tasks/${d}`}>#{d} </Link>)}</p>
        </>
      )}
      <h3>Runs</h3>
      <table className="runs">
        <thead>
          <tr><th>#</th><th>State</th><th>Started</th><th>Duration</th><th>Model</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {(t.runs ?? []).slice().reverse().map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.state}</td>
              <td>{fmtTime(r.started_at)}</td>
              <td>{fmtDuration(r.started_at, r.ended_at)}</td>
              <td className="mono small">{r.model || "—"}</td>
              <td className="small">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TaskPage() {
  const id = Number(useParams().id);
  const { data: t, error, reload } = usePoll<Task>(`/api/tasks/${id}`, 1500);
  const { events, connected } = useEventStream({ task_id: id });
  const [tab, setTab] = useState<Tab>("log");

  if (!t) return <ErrorNote error={error} />;
  const active = t.state === "running" || t.state === "validating";
  const canRetry = ["failed", "stopped_by_user", "paused"].includes(t.state);
  const run = t.runs?.[t.runs.length - 1];

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <Link to="/">Projects</Link> / <Link to={`/projects/${t.project_id}`}>project {t.project_id}</Link> /
          </div>
          <h1>
            #{t.id} {t.title} <StateBadge state={t.state} pending={t.stop_pending && active} />
          </h1>
          <div className="muted small">
            attempt {t.attempts}
            {run && active && <> · running for {fmtDuration(run.started_at, null)}</>}
            {t.hold && <span className="warn"> · on hold (will park after current step)</span>}
          </div>
          {t.reason && t.state !== "done" && <div className="reason">{t.reason}</div>}
        </div>
        <div className="actions">
          {(active || t.state === "queued") &&
            (t.hold ? (
              <ActionButton onClick={async () => { await control("task", "resume", id); reload(); }}>Resume</ActionButton>
            ) : (
              <ActionButton onClick={async () => { await control("task", "pause", id); reload(); }}>Pause</ActionButton>
            ))}
          {canRetry && (
            <>
              <ActionButton kind="primary" onClick={async () => { await post(`/api/tasks/${id}/retry`, { reset: false }); reload(); }}>
                {t.state === "paused" ? "Resume" : "Retry (keep work)"}
              </ActionButton>
              <ActionButton
                onClick={async () => {
                  if (!confirm("Discard this task's changes and start over from the milestone branch?")) return;
                  await post(`/api/tasks/${id}/retry`, { reset: true });
                  reload();
                }}
              >
                Retry from scratch
              </ActionButton>
            </>
          )}
          {t.state !== "done" && t.state !== "discarded" && !active && (
            <ActionButton kind="danger" onClick={async () => { await post(`/api/tasks/${id}/discard`); reload(); }}>
              Discard
            </ActionButton>
          )}
          <StopButton
            size="big"
            disabled={!(active || t.state === "queued")}
            onStop={async () => {
              await control("task", "stop", id);
              reload();
            }}
          />
        </div>
      </div>

      <div className="tabs">
        {(["log", "diff", "details"] as Tab[]).map((x) => (
          <button key={x} className={tab === x ? "active" : ""} onClick={() => setTab(x)}>
            {x === "log" ? "Live log" : x === "diff" ? "Diff" : "Details"}
          </button>
        ))}
        <span className={`dot ${connected ? "ok" : "bad"}`}>{connected ? "live" : "reconnecting"}</span>
      </div>
      <section className="card">
        {tab === "log" && <EventLog events={events} empty="Waiting for the agent…" />}
        {tab === "diff" && <DiffView taskId={id} live={active} />}
        {tab === "details" && <Details t={t} />}
      </section>
    </div>
  );
}
