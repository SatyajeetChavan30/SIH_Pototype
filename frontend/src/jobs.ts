import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface JobKind { key: string; label: string; description: string; role: "office" | "admin"; exclusive: boolean; available: boolean; reason: string | null }
export interface Job {
  id: string; kind: string; params: Record<string, any>; actor: string; status: "running" | "done" | "failed" | "cancelled";
  started: string; finished: string | null; returncode: number | null; result: any; error: string | null; n_lines: number; lines: string[];
}

const JSON_POST = { method: "POST", headers: { "Content-Type": "application/json" } };

/** Background jobs (GET/POST /api/jobs): the kinds this server offers, recent runs, and live logs of running ones. */
export function useJobs(onFinish?: (j: Job) => void) {
  const [kinds, setKinds] = useState<JobKind[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [detail, setDetail] = useState<Record<string, Job>>({});
  const [err, setErr] = useState<string | null>(null);
  const finish = useRef(onFinish);
  finish.current = onFinish;

  const load = useCallback(() => api<{ kinds: JobKind[]; jobs: Job[] }>("/api/jobs")
    .then((r) => { setKinds(r.kinds); setJobs(r.jobs); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const running = jobs.filter((j) => j.status === "running").map((j) => j.id).join(",");
  useEffect(() => {
    if (!running) return;
    const ids = running.split(",");
    let stop = false;
    const tick = async () => {
      let ended = false;
      for (const id of ids) {
        try {
          const j = await api<Job>(`/api/jobs/${id}?tail=200`);
          if (stop) return;
          setDetail((d) => ({ ...d, [id]: j }));
          if (j.status !== "running") { ended = true; finish.current?.(j); }
        } catch { /* keep polling; the server may be switching data folders */ }
      }
      if (ended && !stop) load();
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(t); };
  }, [running, load]);

  const start = async (kind: string, params: Record<string, any> = {}) => {
    setErr(null);
    try {
      const j = await api<Job>("/api/jobs", { ...JSON_POST, body: JSON.stringify({ kind, params }) });
      setDetail((d) => ({ ...d, [j.id]: j }));
      await load();
      return j;
    } catch (e: any) { setErr(e.message); return null; }
  };
  const cancel = async (id: string) => {
    try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); } catch (e: any) { setErr(e.message); }
  };
  /** The most recent run of a kind, with its log when it has been polled. */
  const latest = (kind: string): Job | undefined => {
    const j = [...jobs].reverse().find((x) => x.kind === kind);
    return j && (detail[j.id] ?? j);
  };
  const busy = jobs.some((j) => j.status === "running" && kinds.find((k) => k.key === j.kind)?.exclusive);
  return { kinds, jobs, latest, start, cancel, busy, err, reload: load };
}

export const since = (iso: string, end?: string | null) => {
  const s = Math.max(0, Math.round(((end ? Date.parse(end) : Date.now()) - Date.parse(iso)) / 1000));
  return s < 90 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
};
