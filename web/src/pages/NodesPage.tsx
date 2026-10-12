import { useState } from "react";
import { del, post } from "../api";
import { ActionButton, ErrorNote, Field, fmtTime } from "../components";
import { usePoll } from "../hooks";
import type { Node } from "../types";

export default function NodesPage() {
  const { data: nodes, error, reload } = usePoll<Node[]>("/api/nodes", 5000);
  const [f, setF] = useState({ name: "", base_url: "", api_key: "", capacity: 1 });
  const [formError, setFormError] = useState("");
  const [checkErrors, setCheckErrors] = useState<Record<number, string>>({});

  return (
    <div>
      <div className="page-head">
        <h1>Inference nodes</h1>
      </div>
      <p className="muted">
        OpenAI-compatible endpoints (llama.cpp server, LM Studio, Ollama, MLX, vLLM…). Executors in containers reach
        the host's own services at <code>host.docker.internal</code>.
      </p>
      <ErrorNote error={error} />
      <div className="cards">
        {nodes?.map((n) => (
          <div key={n.id} className="card">
            <div className="row">
              <h3>{n.name}</h3>
              <span className={`dot ${n.healthy ? "ok" : n.healthy === false ? "bad" : ""}`}>
                {n.healthy == null ? "unchecked" : n.healthy ? "healthy" : "unreachable"}
              </span>
            </div>
            <div className="mono small">{n.base_url}</div>
            <div className="small muted">
              capacity {n.capacity} · checked {fmtTime(n.last_checked)} {n.has_api_key && "· api key set"}
            </div>
            <div className="small">{n.models.length ? n.models.join(", ") : <span className="muted">no models listed</span>}</div>
            <ErrorNote error={checkErrors[n.id] ?? ""} />
            <div className="actions">
              <ActionButton
                onClick={async () => {
                  const r = await post<Node>(`/api/nodes/${n.id}/check`);
                  setCheckErrors((e) => ({ ...e, [n.id]: r.error ?? "" }));
                  reload();
                }}
              >
                Check
              </ActionButton>
              <ActionButton kind="danger" onClick={async () => { if (confirm(`Remove ${n.name}?`)) { await del(`/api/nodes/${n.id}`); reload(); } }}>
                Remove
              </ActionButton>
            </div>
          </div>
        ))}
      </div>
      <form
        className="card form"
        onSubmit={async (e) => {
          e.preventDefault();
          setFormError("");
          try {
            const n = await post<Node>("/api/nodes", f);
            await post(`/api/nodes/${n.id}/check`);
            setF({ name: "", base_url: "", api_key: "", capacity: 1 });
            reload();
          } catch (err) {
            setFormError((err as Error).message);
          }
        }}
      >
        <h2>Add node</h2>
        <div className="grid2">
          <Field label="Name">
            <input required value={f.name} placeholder="mac-studio" onChange={(e) => setF({ ...f, name: e.target.value })} />
          </Field>
          <Field label="Base URL" hint="Including /v1">
            <input required className="mono" value={f.base_url} placeholder="http://192.168.1.20:8080/v1" onChange={(e) => setF({ ...f, base_url: e.target.value })} />
          </Field>
          <Field label="API key" hint="Optional">
            <input type="password" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} />
          </Field>
          <Field label="Capacity" hint="Concurrent tasks this node can serve">
            <input type="number" min={1} value={f.capacity} onChange={(e) => setF({ ...f, capacity: Number(e.target.value) })} />
          </Field>
        </div>
        <ErrorNote error={formError} />
        <button className="btn primary">Add node</button>
      </form>
    </div>
  );
}
