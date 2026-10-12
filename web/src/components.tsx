import { useState, type ReactNode } from "react";
import type { FEvent, TaskState } from "./types";

export function StateBadge({ state, pending }: { state: TaskState; pending?: boolean }) {
  const label = pending ? "stopping…" : state.replace(/_/g, " ");
  return <span className={`badge state-${pending ? "stopping" : state}`}>{label}</span>;
}

/** The big red button. One click, no confirmation: stopping must be instant. */
export function StopButton({
  onStop,
  label = "STOP",
  size = "normal",
  disabled,
}: {
  onStop: () => Promise<unknown>;
  label?: string;
  size?: "normal" | "big";
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className={`stop-button ${size}`}
      disabled={disabled || busy}
      title="Kill running agents immediately. Work in progress is kept."
      onClick={async () => {
        setBusy(true);
        try {
          await onStop();
        } catch (e) {
          alert(`Stop failed: ${(e as Error).message}`);
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "STOPPING…" : label}
    </button>
  );
}

export function ActionButton({
  onClick,
  children,
  kind = "secondary",
  disabled,
}: {
  onClick: () => Promise<unknown> | void;
  children: ReactNode;
  kind?: "primary" | "secondary" | "danger";
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className={`btn ${kind}`}
      disabled={disabled || busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onClick();
        } catch (e) {
          alert((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
    </button>
  );
}

export function ErrorNote({ error }: { error: string }) {
  return error ? <div className="error-note">{error}</div> : null;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

export function fmtDuration(start: number, end: number | null): string {
  const s = Math.max(0, Math.round((end ?? Date.now() / 1000) - start));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Scrolling event log. Follows the tail unless the user scrolls up. */
export function EventLog({ events, empty = "No activity yet." }: { events: FEvent[]; empty?: string }) {
  const [follow, setFollow] = useState(true);
  return (
    <div
      className="event-log"
      ref={(el) => {
        if (el && follow) el.scrollTop = el.scrollHeight;
      }}
      onScroll={(e) => {
        const el = e.currentTarget;
        setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
      }}
    >
      {events.length === 0 && <div className="muted">{empty}</div>}
      {events.map((e) => (
        <div key={e.id} className={`ev ev-${e.kind}`}>
          <span className="ev-time">{fmtTime(e.ts)}</span>
          <span className="ev-kind">{e.kind}</span>
          <pre className="ev-msg">{e.message}</pre>
        </div>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export const lines = (s: string) =>
  s
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
