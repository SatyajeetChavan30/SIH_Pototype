import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { HAZARD_COLOR, LEVEL, fmt } from "../theme";
import type { Alert, Recommendation } from "../types";

const SOURCE_LABEL: Record<string, string> = {
  "look-ahead": "Offset look-ahead", "real-time": "Real-time detector", "mud-window": "Mud-weight window",
  fused: "Fused: offsets + real-time", geology: "Geology",
};

export function AlertCard({ a, selected, onClick, big = false }: { a: Alert; selected?: boolean; onClick?: () => void; big?: boolean }) {
  const { hz } = useApp();
  const L = LEVEL[a.level] ?? LEVEL.info;
  return <div className={`alert ${selected ? "sel" : ""} ${a.status === "cleared" ? "cleared" : ""}`} style={{ ["--c" as any]: L.color }} onClick={onClick}
    role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onClick?.()}>
    <div className="row" style={{ gap: 8, justifyContent: "space-between" }}>
      <span className="lvl">{L.icon} {L.label}{a.status !== "active" ? ` · ${a.status.toUpperCase()}` : ""}</span>
      <span className="small muted">{SOURCE_LABEL[a.source]} · {fmt.m(a.md)}</span>
    </div>
    <div className="ttl" style={{ fontSize: big ? 18 : undefined }}>
      {a.hazard !== "GEO" && <span className="swatch" style={{ background: HAZARD_COLOR[a.hazard] ?? "#888", marginRight: 6 }} />}
      {a.title}
    </div>
    <div className="msg">{a.message}</div>
    <div className="row small" style={{ gap: 8, marginTop: 4 }}>
      {a.corroborated && <span className="pill" style={{ color: "#ff9d7a" }}>CORROBORATED</span>}
      {a.hazard !== "GEO" && <span className="muted">{hz(a.hazard)?.label ?? a.hazard} · confidence {fmt.pct(a.confidence)}</span>}
      {a.evidence?.length > 0 && <span className="muted">· {a.evidence.length} offset evidence</span>}
    </div>
  </div>;
}

export function RecommendationTable({ r }: { r: Recommendation }) {
  const { openCitation, fmName } = useApp();
  if (!r) return null;
  return <div>
    <div className="small muted" style={{ marginBottom: 6 }}>Outcome-weighted from {r.n_events} offset events ({r.scope}{r.formation ? `, ${fmName(r.formation)}` : ""}).</div>
    <table className="t">
      <thead><tr><th>Action</th><th className="num">Cured</th><th className="num">1st try</th><th className="num">Median NPT</th><th>Verdict</th></tr></thead>
      <tbody>{r.actions.slice(0, 6).map((a) => <tr key={a.code}>
        <td>{a.label}</td><td className="num">{a.cured}/{a.attempts}</td><td className="num">{a.first_try}</td>
        <td className="num">{a.median_npt_h != null ? `${a.median_npt_h} h` : "–"}</td>
        <td className={`verdict-${a.verdict}`}>{a.verdict === "recommended" ? "✔ recommended" : a.verdict === "avoid" ? "✖ avoid" : "~ mixed"}</td>
      </tr>)}</tbody>
    </table>
    {r.preventive.length > 0 && <div style={{ marginTop: 8 }}><b className="small">Preventive (before entering the zone)</b>
      <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>{r.preventive.map((p) => <li key={p.code} className="small">{p.text}</li>)}</ul></div>}
    {r.lessons.length > 0 && <div style={{ marginTop: 8 }}><b className="small">Lessons learned</b>
      {r.lessons.slice(0, 3).map((l) => <div key={l.id} className="quote small" style={{ marginTop: 4 }}>
        {l.text} <span className="cite" onClick={() => openCitation({ doc_id: l.doc_id, page_no: l.page_no, start: l.start, end: l.end, text: l.text, title: l.title })}>
          [{l.well_id} WCR p.{l.page_no}]</span></div>)}
    </div>}
  </div>;
}

export function AlertDrawer({ a, onClose, onAck }: { a: Alert; onClose: () => void; onAck: () => void }) {
  const { openCitation, fmName, hz } = useApp();
  const [fb, setFb] = useState<string | null>(null);
  const L = LEVEL[a.level] ?? LEVEL.info;
  const feedback = async (useful: boolean) => {
    await api("/api/alerts/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alert_key: a.key, hazard: a.hazard, useful }) });
    setFb(useful ? "Thanks — marked useful" : "Marked as false alarm (used to tune thresholds)");
  };
  return <aside className="drawer" aria-label="Alert evidence">
    <div className="hd">
      <div style={{ flex: 1 }}>
        <div className="lvl small" style={{ color: L.color, fontWeight: 700 }}>{L.icon} {L.label} · {SOURCE_LABEL[a.source]}</div>
        <div style={{ fontWeight: 700, fontSize: 15, marginTop: 2 }}>{a.title}</div>
        <div className="small muted">{hz(a.hazard)?.label ?? a.hazard} · {fmName(a.formation)} · MD {fmt.m(a.md)} · t+{fmt.hours(a.t)}</div>
      </div>
      <button className="btn sm" onClick={onClose} aria-label="Close">✕</button>
    </div>
    <div className="bd">
      <div>{a.message}</div>
      <div className="row wrap">
        {a.status === "active" && <button className="btn sm primary" onClick={onAck}>Acknowledge</button>}
        <button className="btn sm" onClick={() => feedback(true)}>👍 Useful</button>
        <button className="btn sm" onClick={() => feedback(false)}>👎 False alarm</button>
        {fb && <span className="small muted">{fb}</span>}
      </div>

      {a.drivers?.length > 0 && <section>
        <h4>Why — contributing signals</h4>
        <table className="t"><tbody>{a.drivers.map((d, i) => <tr key={i}>
          <td>{d.channel ?? d.factor}</td>
          <td className="num">{typeof d.value === "number" ? d.value.toFixed(2) : d.value} {d.unit}</td>
          <td className="num muted">{d.baseline != null ? `baseline ${typeof d.baseline === "number" ? d.baseline.toFixed(2) : d.baseline}` : ""}</td>
        </tr>)}</tbody></table>
      </section>}

      {a.zone && <section>
        <h4>Formation-aligned offset zone</h4>
        <div className="small">{fmt.m(a.zone.md0)} – {fmt.m(a.zone.md1)} MD in {fmName(a.zone.formation)}: <b>{a.zone.n_events} of {a.zone.n_exposed}</b> offset wells that drilled
          this interval recorded it · offset evidence {fmt.pct(a.zone.p_offsets)} (90% CI {fmt.pct(a.zone.lo)}–{fmt.pct(a.zone.hi)}) · model {fmt.pct(a.zone.p_model)}</div>
      </section>}

      {a.evidence?.length > 0 && <section>
        <h4>Offset evidence ({a.evidence.length})</h4>
        <table className="t"><thead><tr><th>Well</th><th className="num">km</th><th className="num">Their MD</th><th className="num">→ here</th><th>Source</th></tr></thead>
          <tbody>{a.evidence.map((e) => <tr key={e.event_id} title={e.summary}>
            <td>{e.well_id} <span className="muted small">({e.spud_year})</span></td><td className="num">{e.distance_km}</td>
            <td className="num">{fmt.n0(e.md)}</td><td className="num">{fmt.n0(e.projected_md)}</td>
            <td>{e.citation ? <span className="cite small" onClick={() => openCitation(e.citation!)}>p.{e.citation.page_no}</span> : "–"}</td>
          </tr>)}</tbody></table>
        <div className="small muted" style={{ marginTop: 4 }}>"→ here" = the offset event re-projected onto this well by its relative position inside the formation (not by MD).</div>
        {a.evidence.slice(0, 2).map((e) => e.citation && <div key={e.event_id} className="quote small" style={{ marginTop: 6 }}>
          “{e.citation.text}” <span className="cite" onClick={() => openCitation(e.citation!)}>[{e.well_id}]</span></div>)}
      </section>}

      {a.recommendations && <section><h4>What worked in offsets</h4><RecommendationTable r={a.recommendations} /></section>}

      {a.analogs?.length > 0 && <section>
        <h4>Analog replay — most similar past situations</h4>
        <table className="t"><thead><tr><th>Offset</th><th className="num">MD</th><th className="num">Sim.</th><th>What happened next (≤60 m)</th></tr></thead>
          <tbody>{a.analogs.map((x, i) => <tr key={i}>
            <td>{x.well_id}<div className="small muted">{fmName(x.formation)}</div></td><td className="num">{fmt.n0(x.md)}</td><td className="num">{fmt.pct(x.similarity)}</td>
            <td className="small">{x.next.length ? x.next.map((n) => <div key={n.id}><span className="swatch" style={{ background: HAZARD_COLOR[n.hazard] }} /> {n.summary}
              {n.citation && <span className="cite" onClick={() => openCitation(n.citation!)}> [src]</span>}</div>) : <span className="muted">drilled ahead without incident</span>}</td>
          </tr>)}</tbody></table>
      </section>}

      {a.history?.length > 0 && <section>
        <h4>Timeline</h4>
        {a.history.map((h, i) => <div key={i} className="small"><span className="muted num">t+{fmt.hours(h.t)} · {fmt.m(h.md)}</span> — {h.event} ({h.level})</div>)}
      </section>}
    </div>
  </aside>;
}
