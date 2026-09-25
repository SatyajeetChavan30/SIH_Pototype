import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import { useApp } from "../context";
import { HazardLegend, useTip } from "../components/Tip";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";

type Mode = "md" | "tvd" | "flat";

export default function Correlation() {
  const { meta, params, fm, fmName } = useApp();
  const [data, setData] = useState<any>(null);
  const [radius, setRadius] = useState(meta.ontology.region?.default_radius_km ?? 8);
  const [mode, setMode] = useState<Mode>("flat");
  const [flatOn, setFlatOn] = useState(meta.ontology.region?.default_formation ?? meta.formation_order[1]);
  const [tip, show, hide] = useTip();
  const well = params.well ?? meta.active_well;

  useEffect(() => { setData(null); api(`/api/correlation?${qs({ well_id: well, radius_km: radius, max_wells: 8 })}`).then(setData); }, [well, radius]);

  const tracks = useMemo(() => {
    if (!data) return [];
    const target = { well_id: data.target.name, distance_km: 0, target: true, tops: Object.entries(data.target.predicted_tops).map(([f, t]: any) => ({ formation: f, md: t.md, tvd: t.tvd, sd: t.sd })).filter((t: any) => t.md <= data.target.td_md),
      td_md: data.target.td_md, td_tvd: data.target.td_tvd, events: [], casing: [], log: null, spud_year: 2026 };
    return [target, ...data.tracks];
  }, [data]);

  if (!data) return <div className="empty">Loading correlation…</div>;
  const depthOf = (t: any, md: number, tvd: number | null) => {
    if (mode === "md") return md;
    const z = tvd ?? md;
    if (mode === "tvd") return z;
    const ref = t.tops.find((x: any) => x.formation === flatOn);
    return ref ? z - ref.tvd : null;
  };
  const allD = tracks.flatMap((t: any) => [depthOf(t, 0, 0), depthOf(t, t.td_md, t.td_tvd)]).filter((v: any) => v != null) as number[];
  const dMin = mode === "flat" ? Math.max(Math.min(...allD), -1200) : 0;
  const dMax = mode === "flat" ? Math.min(Math.max(...allD), 1800) : Math.max(...allD);
  const H = 720, top = 44, trackW = 118, gap = 14, left = 56;
  const W = left + tracks.length * (trackW + gap);
  const y = (d: number) => top + ((d - dMin) / (dMax - dMin)) * (H - top - 10);
  const ticks: number[] = [];
  const step = (dMax - dMin) > 2500 ? 500 : 250;
  for (let d = Math.ceil(dMin / step) * step; d <= dMax; d += step) ticks.push(d);

  return <div className="col">
    <div>
      <h2 className="view">Well Correlation</h2>
      <p className="lede">Offset wells side by side. Hazards follow formations, not measured depth: flatten on a formation top and events that looked scattered in MD line up (e.g. the Tipam thief sand). The first track is the target well with its offset-predicted tops.</p>
    </div>
    <div className="row wrap card">
      <div className="seg">{(["md", "tvd", "flat"] as Mode[]).map((m) => <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
        {m === "md" ? "Measured depth" : m === "tvd" ? "True vertical depth" : "Flatten on top"}</button>)}</div>
      {mode === "flat" && <select value={flatOn} onChange={(e) => setFlatOn(e.target.value)}>
        {meta.formation_order.slice(1, -1).map((f) => <option key={f} value={f}>{fmName(f)}</option>)}</select>}
      <label className="row small" style={{ gap: 6 }}>Radius <select value={radius} onChange={(e) => setRadius(Number(e.target.value))}>{[3, 5, 8, 10, 15, 25, 40].map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
      <HazardLegend codes={[...meta.ribbon_hazards, "FISH"]} colors={HAZARD_COLOR} labels={HAZARD_SHORT} />
    </div>
    <div className="card scroll">
      <svg width={W} height={H} role="img" aria-label="Well correlation panel">
        {ticks.map((d) => <g key={d}><line x1={left - 4} x2={W} y1={y(d)} y2={y(d)} stroke="var(--line)" strokeWidth={0.6} />
          <text x={left - 8} y={y(d) + 3} fontSize={10} fill="var(--ink-3)" textAnchor="end">{mode === "flat" && d > 0 ? `+${d}` : d}</text></g>)}
        {mode === "flat" && <line x1={left} x2={W} y1={y(0)} y2={y(0)} stroke="var(--ink)" strokeWidth={1} strokeDasharray="6 4" />}
        <text x={12} y={(top + H) / 2} fontSize={10.5} fill="var(--ink-3)" textAnchor="middle" transform={`rotate(-90 12 ${(top + H) / 2})`}>{mode === "flat" ? `m relative to ${fmName(flatOn)} top` : `m ${mode.toUpperCase()}`}</text>
        {tracks.map((t: any, i: number) => {
          const x0 = left + i * (trackW + gap);
          const tops = [...t.tops].sort((a: any, b: any) => a.md - b.md);
          const tdD = depthOf(t, t.td_md, t.td_tvd);
          const log = t.log;
          let gr = "";
          if (log) {
            const pts: string[] = [];
            for (let k = 0; k < log.md.length; k++) {
              const d = depthOf(t, log.md[k], log.tvd[k]);
              if (d == null || d < dMin || d > dMax) continue;
              pts.push(`${pts.length ? "L" : "M"}${(x0 + 38 + ((log.gr[k] - 10) / 140) * (trackW - 42)).toFixed(1)},${y(d).toFixed(1)}`);
            }
            gr = pts.join("");
          }
          return <g key={t.well_id}>
            <text x={x0 + trackW / 2} y={14} fontSize={11.5} fontWeight={700} fill="var(--ink)" textAnchor="middle">{t.well_id.replace(" (ACTIVE)", "")}</text>
            <text x={x0 + trackW / 2} y={28} fontSize={9.5} fill="var(--ink-3)" textAnchor="middle">{t.target ? "target (predicted)" : `${t.distance_km} km · ${t.spud_year}${t.same_structure ? " · same str." : ""}`}</text>
            {tops.map((tp: any, k: number) => {
              const a = depthOf(t, tp.md, tp.tvd); const nx = tops[k + 1];
              const b = nx ? depthOf(t, nx.md, nx.tvd) : tdD;
              if (a == null || b == null) return null;
              const ya = y(Math.max(a, dMin)), yb = y(Math.min(b, dMax));
              if (yb <= ya) return null;
              return <g key={tp.formation}>
                <rect x={x0} y={ya} width={34} height={yb - ya} fill={fm(tp.formation)?.color} opacity={t.target ? 0.55 : 0.8} />
                {t.target && tp.sd && <rect x={x0} y={y(a - tp.sd)} width={trackW} height={y(a + tp.sd) - y(a - tp.sd)} fill="#000" opacity={0.06} />}
                {yb - ya > 26 && <text x={x0 + 17} y={ya + (yb - ya) / 2} fontSize={9} fill="#0b0f14" textAnchor="middle" transform={`rotate(-90 ${x0 + 17} ${ya + (yb - ya) / 2})`}>{fmName(tp.formation).split(" ")[0]}</text>}
              </g>;
            })}
            <rect x={x0 + 36} y={top} width={trackW - 36} height={H - top - 10} fill="none" stroke="var(--line)" />
            {gr && <path d={gr} fill="none" stroke="var(--ink-3)" strokeWidth={1} opacity={0.85} />}
            {t.casing.map((c: any, k: number) => { const d = depthOf(t, c.shoe_md, c.shoe_tvd); if (d == null || d < dMin || d > dMax) return null;
              return <polygon key={k} points={`${x0 + trackW},${y(d)} ${x0 + trackW - 9},${y(d)} ${x0 + trackW},${y(d) - 9}`} fill="var(--ink)"
                onMouseMove={(e) => show(e, <div>{c.casing} shoe at {fmt.m(c.shoe_md)} MD · MW {c.mw_ppg} ppg</div>)} onMouseLeave={hide} />; })}
            {t.events.map((e: any) => { const d = depthOf(t, e.md, e.tvd); if (d == null || d < dMin || d > dMax) return null;
              return <g key={e.id} onMouseMove={(ev) => show(ev, <div><b style={{ color: HAZARD_COLOR[e.hazard] }}>{HAZARD_SHORT[e.hazard]}</b> · {t.well_id}<br />{e.summary}</div>)} onMouseLeave={hide}>
                <circle cx={x0 + 60 + (Object.keys(HAZARD_COLOR).indexOf(e.hazard) % 4) * 14} cy={y(d)} r={6} fill={HAZARD_COLOR[e.hazard]} stroke="var(--page)" strokeWidth={2} />
              </g>; })}
          </g>;
        })}
      </svg>
    </div>
    <div className="small muted">Grey curve: gamma ray (API). Triangles: casing shoes. Dots: extracted events (hover for the report summary).</div>
    {tip}
  </div>;
}
