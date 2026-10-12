import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamUrl } from "./api";
import type { FEvent } from "./types";

/** Fetch `path` now and every `intervalMs`; `reload()` refetches immediately. */
export function usePoll<T>(path: string | null, intervalMs = 2000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const pathRef = useRef(path);
  pathRef.current = path;

  const reload = useCallback(async () => {
    const p = pathRef.current;
    if (!p) return;
    try {
      const d = await api<T>(p);
      if (pathRef.current === p) {
        setData(d);
        setError("");
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    setData(null);
    reload();
    if (!path) return;
    const t = setInterval(reload, intervalMs);
    return () => clearInterval(t);
  }, [path, intervalMs, reload]);

  return { data, error, reload };
}

const MAX_EVENTS = 3000;

/** Live events over Server-Sent Events, deduplicated by id. */
export function useEventStream(params: { project_id?: number; task_id?: number }) {
  const [events, setEvents] = useState<FEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const key = `${params.project_id ?? ""}:${params.task_id ?? ""}`;

  useEffect(() => {
    setEvents([]);
    const seen = new Set<number>();
    const es = new EventSource(streamUrl(params));
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("event", (msg) => {
      const e = JSON.parse((msg as MessageEvent).data) as FEvent;
      if (seen.has(e.id)) return;
      seen.add(e.id);
      setEvents((prev) => {
        const next = [...prev, e];
        return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
      });
    });
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { events, connected };
}
