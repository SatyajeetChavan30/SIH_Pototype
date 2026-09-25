import { useEffect, useState } from "react";
import { api, qs } from "../api";
import { useApp } from "../context";
import { RecommendationTable } from "../components/AlertPanel";
import { MWWindowChart, RiskHeatmap } from "../components/RiskCharts";
import { HazardLegend } from "../components/Tip";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";
import type { Bin, Profile, Recommendation } from "../types";

export default function RiskPlanning() {
  const { meta, params, fmName, openCitation, go } = useApp();
  const [radius, setRadius] = useState(Number(params.radius ?? 8));
  const [prof, setProf] = useState<Profile | null>(null);
  const [win, setWin] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<{ b: Bin; h: string } | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const loc = params.lat ? { lat: params.lat, lon: params.lon } : { well_id: params.well ?? meta.active_well };

  useEffect(() => {
    setProf(null); setWin(null); setErr(null);
    api<Profile>(`/api/risk/profile?${qs({ ...loc, radius_km: radius })}`).then(setProf).catch((e) => setErr(e.message));
    api(`/api/risk/mw-window?${qs({ ...loc, radius_km: Math.max(radius, 10) })}`).then(setWin).catch(() => undefined);
  }, [params.lat, params.lon, params.well, radius]);
  useEffect(() => {
    if (!pick) return;
    api<Recommendation>(`/api/recommend?${qs({ hazard: pick.h, formation: pick.b.formation, well_id: prof?.target.id ?? undefined, radius_km: radius })}`).then(setRec);
  }, [pick]);

  const briefUrl = `/api/brief?${qs({ ...loc, radius_km: radius })}`;
  return <div className="col">
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <div>
        <h2 className="view">Risk &amp; Planning — {prof?.target.name ?? "…"}</h2>
        <p className="lede" style={{ marginBottom: 0 }}>Pre-spud offset risk by depth: formation-aligned offset evidence (Beta-Binomial, with credible intervals) blended with an ML model trained on offset wells, plus the offset-derived mud-weight window. The same models drive the live alerts.</p>
      </div>
      <div className="row wrap">
        <label className="row small" style={{ gap: 6 }}>Offset radius <select value={radius} onChange={(e) => setRadius(Number(e.target.value))}>
          {[3, 5, 8, 10, 15].map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
        {params.lat && <button className="btn sm" onClick={() => go("planning")}>Active well plan</button>}
        <a className="btn primary sm" href={briefUrl} target="_blank" rel="noreferrer">📄 Generate Offset Hazard Brief</a>
      </div>
    </div>
    {err && <div className="banner">{err}</div>}
    {!prof && !err && <div className="empty">Computing risk profile from offsets…</div>}
    {prof && <div style={{ display: "grid", gridTemplateColumns: "minmax(340px, 460px) 1fr", gap: 12 }}>
      <div className="card">
        <h3>Risk by depth <span className="sub">click a cell for what worked</span></h3>
        <HazardLegend codes={meta.ribbon_hazards} colors={HAZARD_COLOR} labels={HAZARD_SHORT} />
        <RiskHeatmap bins={prof.bins} hazards={meta.ribbon_hazards} zones={prof.zones} sections={prof.target.sections} tops={prof.tops}
          onPick={(b, h) => setPick({ b, h })} />
        <div className="small muted">Cell = P(hazard within ±30 m) · outlines = clusters of offset events projected by formation · white bands = predicted tops ±1σ.</div>
      </div>
      <div className="col">
        <div className="card">
          <h3>Headline risks <span className="sub">{prof.offsets.length} offsets within {radius} km · {prof.target.structure_id ?? "no structure"}</span></h3>
          <table className="t"><thead><tr><th>Interval (MD)</th><th>Formation</th><th>Hazard</th><th>Offsets</th><th className="num">Evidence</th><th className="num">Model</th><th>Nearest evidence</th></tr></thead>
            <tbody>{prof.zones.map((z, i) => <tr key={i} className="click" onClick={() => {
              const b = prof.bins.find((x) => x.md1 >= z.md0 && x.md0 <= z.md1)!; setPick({ b, h: z.hazard });
            }}>
              <td className="num">{fmt.n0(z.md0)}–{fmt.n0(z.md1)}</td><td>{fmName(z.formation)}</td>
              <td><span className="swatch" style={{ background: HAZARD_COLOR[z.hazard] }} /> {HAZARD_SHORT[z.hazard]}</td>
              <td className="num">{z.n_events}/{z.n_exposed}</td>
              <td className="num">{fmt.pct(z.p_offsets)} <span className="muted small">({fmt.pct(z.lo)}–{fmt.pct(z.hi)})</span></td>
              <td className="num">{fmt.pct(z.p_model)}</td>
              <td className="small">{z.evidence?.slice(0, 2).map((e) => <span key={e.event_id}>{e.well_id} ({e.distance_km} km{e.citation ? <>, <span className="cite" onClick={(ev) => { ev.stopPropagation(); openCitation(e.citation!); }}>src</span></> : ""}) </span>)}</td>
            </tr>)}</tbody></table>
          {prof.zones.length === 0 && <div className="empty">No clustered offset hazards above threshold.</div>}
        </div>
        {pick && <div className="card">
          <h3><span className="swatch" style={{ background: HAZARD_COLOR[pick.h] }} /> {HAZARD_SHORT[pick.h]} at {fmt.m(pick.b.md0)} ({fmName(pick.b.formation)})
            <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => setPick(null)}>✕</button></h3>
          {(() => {
            const c = pick.b.hazards[pick.h];
            return <div className="small" style={{ marginBottom: 8 }}>Risk {fmt.pct(c.final)} · offsets {c.n_events}/{c.n_exposed} exposed (evidence {fmt.pct(c.p)}, 90% CI {fmt.pct(c.lo)}–{fmt.pct(c.hi)}, basin prior {fmt.pct(c.prior)}) · ML {c.ml != null ? fmt.pct(c.ml) : "–"}
              {c.drivers && c.drivers.length > 0 && <> · drivers: {c.drivers.map((d) => `${d.factor} (${d.delta! > 0 ? "+" : ""}${Math.round((d.delta ?? 0) * 100)} pts)`).join(", ")}</>}</div>;
          })()}
          {rec ? <RecommendationTable r={rec} /> : <div className="muted">Loading…</div>}
        </div>}
        <div className="card">
          <h3>Offset-derived mud-weight window <span className="sub">censored offset outcomes → P(loss|ECD), P(kick|MW), P(instability|MW)</span></h3>
          {win ? <MWWindowChart win={win} plan={win.plan} tops={win.tops} /> : <div className="muted">Loading…</div>}
          {win && <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Formation</th><th className="num">Offsets</th><th>Losses</th><th>Kicks</th><th>Instability</th></tr></thead>
            <tbody>{Object.entries(win.formations as Record<string, any>).map(([c, f]) => <tr key={c}>
              <td>{fmName(c)}</td><td className="num">{f.n_offsets}</td>
              <td className="small">{f.loss.n_losses} · {f.loss.ecd_p50 ? `P50 at ECD ${f.loss.ecd_p50}` : f.loss.status}{f.loss.note ? <div className="muted">{f.loss.note}</div> : null}</td>
              <td className="small">{f.kick.n_kicks} kicks, {f.kick.n_gas} gas · {f.kick.mw_p10 ? `P10 at MW ${f.kick.mw_p10}` : f.kick.status}{f.kick.kill_mw_max ? ` · max kill ${f.kick.kill_mw_max}` : ""}</td>
              <td className="small">{f.instability.mw_p10 ? `P10 at MW ${f.instability.mw_p10}` : f.instability.status}</td>
            </tr>)}</tbody></table>}
        </div>
        <div className="card">
          <h3>Predicted formation tops <span className="sub">IDW from offsets · re-anchored live as tops are picked</span></h3>
          <table className="t"><thead><tr><th>Formation</th><th className="num">MD</th><th className="num">TVD</th><th className="num">±1σ</th><th className="num">Offsets</th></tr></thead>
            <tbody>{Object.entries(prof.tops).filter(([, t]) => t.md <= prof.target.td_md + 50).map(([c, t]) => <tr key={c}>
              <td>{fmName(c)}</td><td className="num">{fmt.n0(t.md)}</td><td className="num">{fmt.n0(t.tvd)}</td><td className="num">{t.sd}</td><td className="num">{t.n}</td></tr>)}</tbody></table>
        </div>
      </div>
    </div>}
  </div>;
}
