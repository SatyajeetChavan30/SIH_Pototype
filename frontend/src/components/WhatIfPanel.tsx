import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { HAZARD_COLOR, HAZARD_SHORT, LEVEL, fmt } from "../theme";
import type { Section } from "../types";

interface Check { formation: string; top_md: number; section: string; mw: number; ecd: number; findings: { hazard: string; p: number; message: string }[] }
interface WhatIf {
  note: string;
  deltas: { formation: string; hazard: string; baseline: number; scenario: number; delta: number }[];
  top_changes: { formation: string; hazard: string; baseline: number; scenario: number; delta: number }[];
  window_checks: { baseline: Check[]; scenario: Check[] };
  findings_count: { baseline: number; scenario: number };
  zones: { baseline: number; scenario: number };
}
type Edit = { mw_ppg: number; ecd_ppg: number; shoe_md: number };

/** Change the mud programme or casing points and see the risk picture recompute against the offsets. */
export default function WhatIfPanel({ loc, sections, radius, onScenario }: {
  loc: Record<string, string | undefined>; sections: Section[]; radius: number;
  onScenario: (plan: Check[] | null) => void;
}) {
  const { fmName } = useApp();
  const init = () => Object.fromEntries(sections.map((s) => [s.idx, { mw_ppg: s.mw_ppg, ecd_ppg: s.ecd_ppg, shoe_md: s.shoe_md }])) as Record<number, Edit>;
  const [edits, setEdits] = useState<Record<number, Edit>>(init);
  const [res, setRes] = useState<WhatIf | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setEdits(init()); setRes(null); onScenario(null); }, [sections]);

  const changed = sections.filter((s) => {
    const e = edits[s.idx];
    return e && (e.mw_ppg !== s.mw_ppg || e.ecd_ppg !== s.ecd_ppg || e.shoe_md !== s.shoe_md);
  });
  const set = (idx: number, k: keyof Edit, v: string) => setEdits((E) => ({ ...E, [idx]: { ...E[idx], [k]: Number(v) } }));
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = { radius_km: radius, overrides: { sections: changed.map((s) => ({ idx: s.idx, ...edits[s.idx] })) } };
      if (loc.well_id) body.well_id = loc.well_id; else { body.lat = Number(loc.lat); body.lon = Number(loc.lon); }
      const r = await api<WhatIf>("/api/risk/whatif", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setRes(r); onScenario(r.window_checks.scenario);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const reset = () => { setEdits(init()); setRes(null); onScenario(null); };

  return <div className="card">
    <h3>What-if planner <span className="sub">change mud weight, ECD or casing points; the offset models re-run</span></h3>
    <table className="t">
      <thead><tr><th>Section</th><th className="num">Shoe MD</th><th className="num">MW ppg</th><th className="num">ECD ppg</th></tr></thead>
      <tbody>{sections.map((s) => { const e = edits[s.idx]; if (!e) return null; return <tr key={s.idx}>
        <td>{s.hole} <span className="muted small">→ {s.casing}</span></td>
        <td className="num"><input type="number" step={10} value={e.shoe_md} onChange={(v) => set(s.idx, "shoe_md", v.target.value)} style={{ width: 86 }} /></td>
        <td className="num"><input type="number" step={0.1} value={e.mw_ppg} onChange={(v) => set(s.idx, "mw_ppg", v.target.value)} style={{ width: 70 }} /></td>
        <td className="num"><input type="number" step={0.1} value={e.ecd_ppg} onChange={(v) => set(s.idx, "ecd_ppg", v.target.value)} style={{ width: 70 }} /></td>
      </tr>; })}</tbody>
    </table>
    <div className="row" style={{ marginTop: 8 }}>
      <button className="btn sm primary" disabled={busy || changed.length === 0} onClick={run}>{busy ? "Re-running offsets…" : `Run what-if (${changed.length} change${changed.length === 1 ? "" : "s"})`}</button>
      <button className="btn sm ghost" onClick={reset}>Reset to plan</button>
      {err && <span className="small" style={{ color: LEVEL.critical.color }}>{err}</span>}
    </div>
    {res && <div className="col" style={{ gap: 8, marginTop: 10 }}>
      <div className="row wrap small" style={{ gap: 16 }}>
        <span>Mud-window findings: <b>{res.findings_count.baseline} → {res.findings_count.scenario}</b></span>
        <span>Hazard zones: <b>{res.zones.baseline} → {res.zones.scenario}</b></span>
      </div>
      {res.top_changes.length > 0 ? <table className="t">
        <thead><tr><th>Formation</th><th>Hazard</th><th className="num">Plan</th><th className="num">What-if</th><th className="num">Change</th></tr></thead>
        <tbody>{res.top_changes.map((d, i) => <tr key={i}>
          <td>{fmName(d.formation)}</td>
          <td><span className="swatch" style={{ background: HAZARD_COLOR[d.hazard] }} /> {HAZARD_SHORT[d.hazard]}</td>
          <td className="num">{fmt.pct(d.baseline)}</td><td className="num">{fmt.pct(d.scenario)}</td>
          <td className="num" style={{ color: d.delta > 0 ? LEVEL.critical.color : "var(--good-ink)", fontWeight: 650 }}>{d.delta > 0 ? "▲ +" : "▼ "}{Math.round(d.delta * 100)} pts</td>
        </tr>)}</tbody>
      </table> : <div className="small muted">No formation-level peak risk moved by 2 points or more.</div>}
      {res.window_checks.scenario.filter((c) => c.findings.length).map((c) => <div key={c.formation} className="small">
        <b>{fmName(c.formation)}</b>: {c.findings.map((f) => f.message).join("; ")}</div>)}
      <div className="small muted">{res.note}</div>
    </div>}
  </div>;
}
