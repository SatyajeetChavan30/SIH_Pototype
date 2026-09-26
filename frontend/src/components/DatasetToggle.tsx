import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../context";
import { fmtDuration, reloadAfterRestart } from "../jobs";

const SHORT: Record<string, string> = { assam: "Synthetic · Assam", norway: "Real · North Sea" };

/** Header switch between the synthetic Assam demo and the real North Sea (Sodir) knowledge base.
 *  Admins flip it (the server restarts on the other dataset); everyone else sees which data is in use. */
export default function DatasetToggle() {
  const { meta } = useApp();
  const { can } = useAuth();
  const [switching, setSwitching] = useState<{ label: string; s: number } | null>(null);
  const ds = meta.datasets;
  const notice = meta.ontology.region?.data_notice;
  if (!ds) return meta.synthetic
    ? <span className="tag synthetic" title={notice ?? "All data in this demo is synthetic"}>SYNTHETIC DEMO DATA</span>
    : <span className="tag live" title={notice}>REAL PUBLIC DATA · Sodir (NLOD)</span>;

  const why = (o: { built: boolean }) =>
    !can("admin") ? "Sign in as an admin to switch datasets"
      : ds.locked ? "The dataset is fixed by an environment variable (STRATASENSE_REGION / STRATASENSE_DATA_DIR)"
        : !o.built ? "Not built yet: build it in System → Dataset" : null;

  const switchTo = async (o: { code: string; label: string; synthetic: boolean; stream?: boolean }) => {
    const caveat = o.synthetic ? "" : o.stream ? "\n\nLive Ops replays real Volve rig data (Equinor) in this dataset."
      : "\n\nThis dataset has no rig stream yet, so Live Ops shows no feed while it is in use.";
    if (!confirm(`Switch StrataSense to "${o.label}"? The server restarts (about 10–30 s); everyone's pages reload.${caveat}`)) return;
    try {
      const r = await api<{ restart: boolean }>("/api/admin/dataset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset: o.code }),
      });
      if (!r.restart) return;
      location.hash = o.synthetic || o.stream ? "/live" : "/map";   // without a stream, open where its wells show
      setSwitching({ label: o.label, s: 0 });
      reloadAfterRestart(meta.boot ?? null, (s) => setSwitching({ label: o.label, s }));
    } catch (e: any) { alert(`Could not switch dataset: ${e.message}`); }
  };

  return <>
    <span className="dstoggle" role="group" aria-label="Dataset" title={notice}>
      {ds.options.map((o) => {
        const on = o.code === ds.current;
        const blocked = on ? null : why(o);
        return <button key={o.code} className={`${on ? "on" : ""} ${o.synthetic ? "syn" : "real"}`} aria-pressed={on}
          disabled={!on && !!blocked} title={on ? `${o.label} (in use)` : blocked ?? `Switch to ${o.label}`}
          onClick={() => !on && switchTo(o)}>{SHORT[o.code] ?? o.label}</button>;
      })}
    </span>
    {switching && <div className="restart-overlay" role="alertdialog" aria-live="polite">
      <div className="card" style={{ maxWidth: 480 }}>
        <h3>Switching to {switching.label}</h3>
        <div>The server is restarting on the other knowledge base. <span className="muted num">{fmtDuration(switching.s)}</span></div>
        <div className="progress" style={{ marginTop: 10 }}><div className="indeterminate" style={{ width: "100%" }} /></div>
        <div className="small muted" style={{ marginTop: 8 }}>The page reloads by itself when the server is back.</div>
      </div>
    </div>}
  </>;
}
