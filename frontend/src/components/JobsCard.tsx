import { useEffect, useState } from "react";
import { api } from "../api";
import { JobView, startJob, useJob } from "../jobs";

interface Maint { key: string; label: string; description: string; available: boolean; reason: string | null }

/** Analytics > Maintenance: re-score OCR and retrain the models on the server, with a live log (office and admin).
 *  Rebuilding the knowledge base and the rig simulator live under System. */
export default function JobsCard({ onFinished }: { onFinished: () => void }) {
  const [kinds, setKinds] = useState<Maint[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api<Maint[]>("/api/analytics/maintenance").then(setKinds).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const [job] = useJob(jobId, (j) => { load(); if (j.status === "done") onFinished(); });
  const running = job?.status === "running";
  const start = async (key: string) => {
    setErr(null);
    try { setJobId((await startJob(`/api/analytics/maintenance/${key}`)).id); } catch (e: any) { setErr(e.message); }
  };
  if (!kinds.length) return err ? <div className="card small muted">Maintenance jobs unavailable: {err}</div> : null;
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Maintenance <span className="sub">runs on the server in the background · one at a time · logged in the decision log</span></h3>
    {kinds.map((k) => <div key={k.key} className="row" style={{ alignItems: "flex-start", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <div style={{ flex: 1 }}>
        <div><b>{k.label}</b></div>
        <div className="small muted">{k.description}</div>
        {!k.available && <div className="small" style={{ color: "var(--warn-ink)" }}>Not available: {k.reason}</div>}
      </div>
      <button className="btn sm" disabled={!k.available || running} onClick={() => start(k.key)}>▶ Run</button>
    </div>)}
    {job && <JobView job={job} />}
    {job?.status === "done" && job.result?.message && <div className="small" style={{ color: "var(--good-ink)" }}>{job.result.message}</div>}
    {err && <div className="banner small">{err}</div>}
  </div>;
}
