import { useEffect, useState } from "react";
import { api } from "../api";
import { JobView, fmtDuration, reloadAfterRestart, type Job } from "../jobs";

export interface SetupStatus {
  ready: boolean; load_error: string | null; region: string; boot: string; building: boolean; dataset_locked: boolean;
  datasets: Record<string, { code: string; label: string; about: string; built: boolean; current: boolean; synthetic: boolean; needs: string | null }>;
  job: Job | null; ocr: { available: boolean; engine: string | null }; stages: Record<string, string[]>;
}

export const Logo = () => <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#000000" /><path d="M16 4 L22 28 H10 Z" fill="none" stroke="#ffffff" strokeWidth="2.5" /><circle cx="16" cy="12" r="3" fill="#d95926" /></svg>;

/** First run: no knowledge base yet. Build one from the browser and watch it happen. */
export default function Setup({ initial }: { initial: SetupStatus }) {
  const [st, setSt] = useState<SetupStatus>(initial);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState<number | null>(null);

  useEffect(() => {
    let stop = false, timer = 0;
    const tick = () => api<SetupStatus>("/api/setup/status").then((s) => {
      if (stop) return;
      setSt(s);
      if (s.ready) { location.reload(); return; }
      if (s.job?.status === "done" && s.job.result?.restart) { setRestarting(0); reloadAfterRestart(s.boot, setRestarting); return; }
      timer = window.setTimeout(tick, s.job?.status === "running" ? 1000 : 2500);
    }).catch(() => { if (!stop) timer = window.setTimeout(tick, 2500); });
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  const build = async (dataset: string) => {
    setBusy(true); setErr(null);
    try {
      await api("/api/setup/build", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset }) });
      setSt(await api<SetupStatus>("/api/setup/status"));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const job = st.job;
  const running = job?.status === "running";
  const region = job?.title.includes("North Sea") ? "norway" : "assam";
  const stages = st.stages[region] ?? [];
  const cur = job?.stage ? stages.indexOf(job.stage) : -1;

  return <div className="login-page">
    <div className="card setup">
      <div className="brand" style={{ marginBottom: 4 }}><Logo /><span>StrataSense <small>Nearby Wells Intelligence System</small></span></div>
      <h2 className="view" style={{ margin: "10px 0 2px" }}>{running ? "Building the knowledge base" : "Welcome — let's set up StrataSense"}</h2>

      {restarting != null ? <div className="col" style={{ gap: 8 }}>
        <p className="lede">The knowledge base is built. StrataSense is restarting to load it… <span className="muted num">{fmtDuration(restarting)}</span></p>
        <div className="progress"><div className="indeterminate" style={{ width: "100%" }} /></div>
      </div> : running && job ? <div className="col" style={{ gap: 10 }}>
        <p className="lede" style={{ margin: 0 }}>StrataSense is generating the offset wells, reading every drilling and completion report (NLP + OCR),
          training the risk models and replaying the active well. This takes a few minutes; you can leave this page open or come back later.</p>
        <ol className="steps">
          {stages.map((s, i) => <li key={s} className={i < cur ? "done" : i === cur ? "now" : ""}>
            <span className="mark" aria-hidden>{i < cur ? "✔" : i === cur ? "●" : "○"}</span>{s}</li>)}
        </ol>
        <JobView job={job} compact />
      </div> : <div className="col" style={{ gap: 10 }}>
        <p className="lede" style={{ margin: 0 }}>There is no knowledge base on this server yet. Choose the data to start with; StrataSense builds it here,
          on this machine, and opens the dashboard when it is ready. Nothing needs to be typed in a terminal.</p>
        {st.load_error && <div className="banner small">The existing knowledge base could not be loaded ({st.load_error}). Build it again below.</div>}
        {job && job.status !== "running" && job.status !== "done" && <div className="card" style={{ background: "var(--surface-2)" }}>
          <div className="small" style={{ marginBottom: 6 }}><b>The last build did not finish.</b> The log below says why; you can simply try again.</div>
          <JobView job={job} showLog={job.status === "failed"} />
        </div>}
        {Object.values(st.datasets).map((d) => {
          const disabled = !!d.needs || busy || (st.dataset_locked && !d.current);
          return <div key={d.code} className={`card choice ${d.code === "assam" ? "rec" : ""}`}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 650 }}>{d.label} {d.code === "assam" && <span className="pill" style={{ color: "var(--good-ink)" }}>RECOMMENDED FIRST</span>}</div>
                <div className="small ink2" style={{ marginTop: 3 }}>{d.about}</div>
                {d.needs && <div className="small muted" style={{ marginTop: 4 }}>{d.needs} You can add it later from System.</div>}
              </div>
              <button className={`btn ${d.code === "assam" ? "primary" : ""}`} disabled={disabled} onClick={() => build(d.code)}>
                {busy ? "Starting…" : "Build knowledge base"}</button>
            </div>
          </div>;
        })}
        <div className="small muted">OCR for scanned reports: {st.ocr.available ? `ready (${st.ocr.engine})` : "not installed — scanned pages will be flagged; an administrator can install it later under System"}.
          Demo sign-in after set-up: <code>field</code>, <code>office</code> or <code>admin</code> with password <code>demo</code>.</div>
        {err && <div className="banner small">{err}</div>}
      </div>}
    </div>
  </div>;
}
