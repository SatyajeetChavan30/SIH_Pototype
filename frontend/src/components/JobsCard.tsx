import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import { useApp } from "../context";
import { since, useJobs, type Job, type JobKind } from "../jobs";

const STATUS: Record<Job["status"], { label: string; color: string }> = {
  running: { label: "running", color: "var(--info-ink)" }, done: { label: "done", color: "var(--good-ink)" },
  failed: { label: "failed", color: "var(--bad-ink)" }, cancelled: { label: "cancelled", color: "var(--ink-3)" },
};
const BUILDS = ["build-demo", "build-public"];

/** Analytics > Maintenance: re-run evaluations, retrain models and (admins) rebuild the knowledge base. */
export default function JobsCard({ onFinished }: { onFinished: () => void }) {
  const { can } = useAuth();
  const [confirm, setConfirm] = useState<JobKind | null>(null);
  const j = useJobs((job) => {
    if (job.status === "done" && job.result?.reload) setTimeout(() => location.reload(), 1500);
    else onFinished();
  });
  const { go } = useApp();
  const rows = j.kinds.filter((k) => k.role !== "admin" || can("admin"));
  if (!j.kinds.length) return j.err ? <div className="card small muted">Maintenance jobs unavailable: {j.err}</div> : null;
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Maintenance <span className="sub">runs on the server in the background · one at a time · logged in the decision log</span></h3>
    {j.err && <div className="banner small">{j.err}</div>}
    {rows.map((k) => {
      const last = j.latest(k.key);
      const isRunning = last?.status === "running";
      return <div key={k.key} style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
        <div className="row" style={{ alignItems: "flex-start", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div><b>{k.label}</b>{k.role === "admin" && <span className="chip" style={{ marginLeft: 6 }}>admin</span>}</div>
            <div className="small muted">{k.description}</div>
            {!k.available && <div className="small" style={{ color: "var(--warn-ink)" }}>Not available: {k.reason}</div>}
            {last && <div className="small" style={{ marginTop: 2 }}>
              Last run <span style={{ color: STATUS[last.status].color, fontWeight: 600 }}>{STATUS[last.status].label}</span>
              {" "}· {last.actor} · started {last.started.replace("T", " ")} · {since(last.started, last.finished)}
              {last.result?.message && <> · {last.result.message}</>}
              {last.status === "failed" && last.error && <div className="small" style={{ color: "var(--bad-ink)" }}>{last.error}</div>}
            </div>}
          </div>
          {k.key === "simulate-rig"
            ? <button className="btn sm" disabled={!k.available} onClick={() => go("live")}>Open Live Ops</button>
            : isRunning
            ? <button className="btn sm" onClick={() => j.cancel(last!.id)}>Cancel</button>
            : <button className={`btn sm ${BUILDS.includes(k.key) ? "" : "primary"}`} disabled={!k.available || j.busy}
                title={j.busy ? "another job is running" : k.reason ?? undefined}
                onClick={() => BUILDS.includes(k.key) ? setConfirm(k) : j.start(k.key)}>{BUILDS.includes(k.key) ? "Rebuild…" : "Run"}</button>}
        </div>
        {last && k.key !== "simulate-rig" && (isRunning || last.status === "failed") && last.lines?.length > 0 && <JobLog lines={last.lines} />}
      </div>;
    })}
    {confirm && <BuildConfirm kind={confirm} onCancel={() => setConfirm(null)}
      onConfirm={(params) => { j.start(confirm.key, params); setConfirm(null); }} />}
  </div>;
}

/** The last lines of a job's output, kept scrolled to the end. */
export function JobLog({ lines, height = 150 }: { lines: string[]; height?: number }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines]);
  return <pre ref={ref} className="mono small" style={{ maxHeight: height, overflow: "auto", background: "var(--surface-2)", padding: 8,
    borderRadius: 6, margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{lines.join("\n")}</pre>;
}

function BuildConfirm({ kind, onCancel, onConfirm }: { kind: JobKind; onCancel: () => void; onConfirm: (params: Record<string, any>) => void }) {
  const { meta } = useApp();
  const [quadrants, setQuadrants] = useState("15,16");
  const [download, setDownload] = useState(false);
  const pub = kind.key === "build-public";
  const servesThis = pub ? !meta.synthetic : meta.synthetic;
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onCancel]);
  return <div className="modal-bg" onClick={onCancel}>
    <div className="modal" style={{ width: "min(620px, 100%)" }} onClick={(e) => e.stopPropagation()} role="dialog" aria-label={kind.label}>
      <div className="hd"><div style={{ flex: 1, fontWeight: 700 }}>{kind.label}</div><button className="btn sm" onClick={onCancel} aria-label="Close">✕</button></div>
      <div className="bd col" style={{ gap: 8 }}>
        <div className="small">{kind.description}</div>
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>The build runs in a separate folder. NWIS keeps serving the current data until it finishes; a failed or cancelled build changes nothing.</li>
          <li>{servesThis
            ? "When it finishes, NWIS switches to the new knowledge base and this page reloads (if a live rig feed is connected, the switch waits for the next restart)."
            : pub ? "This server runs the Assam demo, so the North Sea data is used the next time NWIS starts with ./run.sh --public."
              : "This server runs the North Sea data, so the rebuilt demo is used the next time NWIS starts with ./run.sh."}</li>
          <li><b>Kept:</b> user accounts and sign-ins, the decision log (still verifiable), alert feedback.</li>
          <li><b>Not kept:</b> uploaded documents, review decisions, expert memos and lessons added since the last build.</li>
          <li>The previous folder is kept as a backup (<code>…bak-&lt;date&gt;</code>); only the newest backup is kept.</li>
        </ul>
        {pub && <div className="row wrap" style={{ gap: 12 }}>
          <label className="row small" style={{ gap: 6 }}>Quadrants <input type="text" value={quadrants} onChange={(e) => setQuadrants(e.target.value)} style={{ width: 90 }} /></label>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={download} onChange={(e) => setDownload(e.target.checked)} />
            Download the Sodir CSV exports first (needs internet, tens of MB)</label>
        </div>}
        <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
          <button className="btn sm" onClick={onCancel}>Cancel</button>
          <button className="btn primary" onClick={() => onConfirm(pub ? { quadrants, download } : {})}>Rebuild</button>
        </div>
      </div>
    </div>
  </div>;
}
