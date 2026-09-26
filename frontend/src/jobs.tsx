import { useEffect, useRef, useState } from "react";
import { api } from "./api";

/** A background job on the server (build, import, evaluation, install); see backend stratasense/jobs.py. */
export interface Job {
  id: string; kind: string; title: string; status: "running" | "done" | "failed" | "cancelled";
  started: number; finished: number | null; elapsed_s: number; progress: number | null; stage: string | null;
  error: string | null; result: any; n_lines: number; lines?: string[]; cancellable: boolean;
}

const post = (path: string, body?: unknown) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}) });

export const startJob = (path: string, body?: unknown) => post(path, body) as Promise<Job>;

export function uploadJob(path: string, files: File[]): Promise<any> {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  return api(path, { method: "POST", body: fd });
}

/** Poll a job once a second until it finishes; `onEnd` fires once with the final state. */
export function useJob(id: string | null, onEnd?: (j: Job) => void): [Job | null, string | null] {
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  useEffect(() => {
    setJob(null); setErr(null);
    if (!id) return;
    let stop = false, timer = 0;
    const tick = () => api<Job>(`/api/jobs/${id}`).then((j) => {
      if (stop) return;
      setJob(j);
      if (j.status === "running") timer = window.setTimeout(tick, 1000);
      else endRef.current?.(j);
    }).catch((e) => { if (!stop) { setErr(e.message); timer = window.setTimeout(tick, 3000); } });
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [id]);
  return [job, err];
}

export const fmtDuration = (s: number) => s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;

const STATUS_COLOR: Record<string, string> = { running: "var(--info-ink)", done: "var(--good-ink)", failed: "var(--bad-ink)", cancelled: "var(--ink-3)" };
const STATUS_LABEL: Record<string, string> = { running: "running", done: "finished", failed: "failed", cancelled: "cancelled" };

/** Progress, current step and the tail of the log for one job. */
export function JobView({ job, showLog = false, compact = false }: { job: Job; showLog?: boolean; compact?: boolean }) {
  const [open, setOpen] = useState(showLog);
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [job.n_lines, open]);
  const pct = job.progress != null ? Math.round(job.progress * 100) : null;
  const cancel = () => post(`/api/jobs/${job.id}/cancel`).catch(() => undefined);
  return <div className="col" style={{ gap: 6 }}>
    <div className="row wrap" style={{ gap: 8 }}>
      {!compact && <b>{job.title}</b>}
      <span className="pill" style={{ color: STATUS_COLOR[job.status] }}>{STATUS_LABEL[job.status]}</span>
      <span className="small muted num">{fmtDuration(job.elapsed_s)}{pct != null && job.status === "running" ? ` · ${pct}%` : ""}</span>
      <span className="spacer" style={{ flex: 1 }} />
      {job.cancellable && <button className="btn sm" onClick={cancel}>Cancel</button>}
      <button className="btn sm ghost" onClick={() => setOpen(!open)}>{open ? "Hide log" : "Show log"}</button>
    </div>
    {job.status === "running" && <div className="progress" aria-label="progress">
      <div style={{ width: `${pct ?? 100}%`, opacity: pct == null ? 0.35 : 1 }} className={pct == null ? "indeterminate" : ""} /></div>}
    {job.stage && job.status === "running" && <div className="small">{job.stage}…</div>}
    {job.error && <div className="banner small" style={{ borderColor: "var(--bad-ink)" }}>{job.error}</div>}
    {open && <pre ref={logRef} className="joblog">{(job.lines ?? []).join("\n") || "(no output yet)"}</pre>}
  </div>;
}

/** After the server restarts (dataset switch, rebuild), wait until the new process answers, then reload the page. */
export async function reloadAfterRestart(prevBoot: string | null, onWait?: (s: number) => void) {
  const t0 = Date.now();
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    onWait?.((Date.now() - t0) / 1000);
    try {
      const h = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json());
      if (h.boot !== prevBoot && h.ready !== undefined) { location.reload(); return; }
    } catch { /* still restarting */ }
    if (Date.now() - t0 > 180_000) { location.reload(); return; }
  }
}
