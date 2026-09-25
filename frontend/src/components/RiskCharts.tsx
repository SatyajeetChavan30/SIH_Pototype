import { useApp } from "../context";
import { HAZARD_ABBR, HAZARD_COLOR, HAZARD_SHORT, fmt, riskColor } from "../theme";
import type { Bin, Section, TopPred, Zone } from "../types";
import { useTip } from "./Tip";

/** Depth x hazard risk heat-strip for a (planned) well, with formations, casing shoes and offset zones. */
export function RiskHeatmap({ bins, hazards, zones, sections, tops, height = 760, onPick }: {
  bins: Bin[]; hazards: string[]; zones: Zone[]; sections: Section[]; tops: Record<string, TopPred>; height?: number;
  onPick?: (b: Bin, h: string) => void;
}) {
  const { fm, fmName } = useApp();
  const [tip, show, hide] = useTip();
  if (!bins.length) return null;
  const W = 460, left = 48, fmW = 100, colW = (W - left - fmW - 8) / hazards.length, top = 28;
  const d1 = bins[bins.length - 1].md1;
  const y = (md: number) => top + (md / d1) * (height - top - 6);
  const bands: { code: string; a: number; b: number }[] = [];
  for (const b of bins) {
    const l = bands[bands.length - 1];
    if (l && l.code === b.formation) l.b = b.md1; else bands.push({ code: b.formation, a: b.md0, b: b.md1 });
  }
  const ticks: number[] = [];
  for (let m = 0; m <= d1; m += 250) ticks.push(m);
  return <div>
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label="Risk by depth and hazard">
      {hazards.map((h, i) => <g key={h}><rect x={left + fmW + i * colW + colW / 2 - 4} y={4} width={8} height={8} rx={2} fill={HAZARD_COLOR[h]} />
        <text x={left + fmW + i * colW + colW / 2} y={24} fontSize={9.5} fill="var(--ink-2)" textAnchor="middle">{HAZARD_ABBR[h]}</text><title>{HAZARD_SHORT[h]}</title></g>)}
      {ticks.map((m) => <g key={m}><line x1={left - 4} x2={W} y1={y(m)} y2={y(m)} stroke="var(--line)" strokeWidth={0.6} />
        <text x={left - 8} y={y(m) + 3} fontSize={10} fill="var(--ink-3)" textAnchor="end">{m}</text></g>)}
      {bands.map((f, i) => <g key={i}>
        <rect x={left} y={y(f.a)} width={fmW - 6} height={y(f.b) - y(f.a)} fill={fm(f.code)?.color ?? "#555"} opacity={0.4} />
        {y(f.b) - y(f.a) > 14 && <text x={left + 4} y={y(f.a) + 12} fontSize={10.5} fill="var(--ink)">{fmName(f.code)}</text>}
      </g>)}
      {bins.map((b) => hazards.map((h, i) => {
        const c = b.hazards[h];
        return <rect key={`${b.md0}${h}`} x={left + fmW + i * colW + 1} y={y(b.md0) + 0.3} width={colW - 2} height={Math.max(0.5, y(b.md1) - y(b.md0) - 0.6)}
          fill={riskColor(c.final)} style={{ cursor: onPick ? "pointer" : undefined }} onClick={() => onPick?.(b, h)}
          onMouseMove={(e) => show(e, <div><b style={{ color: HAZARD_COLOR[h] }}>{HAZARD_SHORT[h]}</b> · {fmt.m(b.md0)}–{fmt.m(b.md1)} MD ({fmName(b.formation)})<br />
            Risk within ±30 m: <b>{fmt.pct(c.final)}</b><br />
            <span className="muted">offset evidence {fmt.pct(c.p)} (90% CI {fmt.pct(c.lo)}–{fmt.pct(c.hi)}) from {c.n_exposed} exposed offsets, {c.n_events} with events · ML {c.ml != null ? fmt.pct(c.ml) : "–"}</span>
            {c.drivers && c.drivers.length > 0 && <div className="small">Drivers: {c.drivers.map((d) => `${d.factor} ${d.delta! > 0 ? "+" : ""}${Math.round((d.delta ?? 0) * 100)}pt`).join(", ")}</div>}</div>)}
          onMouseLeave={hide} />;
      }))}
      {zones.map((z, i) => {
        const hi = hazards.indexOf(z.hazard);
        if (hi < 0) return null;
        return <rect key={i} x={left + fmW + hi * colW} y={y(z.md0)} width={colW} height={Math.max(3, y(z.md1) - y(z.md0))} fill="none" stroke={HAZARD_COLOR[z.hazard]} strokeWidth={2} rx={2} />;
      })}
      {sections.filter((s) => s.shoe_md > 0).map((s) => <g key={s.idx}>
        <line x1={left} x2={W} y1={y(s.shoe_md)} y2={y(s.shoe_md)} stroke="#e8eef4" strokeWidth={1.2} />
        <polygon points={`${left - 2},${y(s.shoe_md)} ${left + 8},${y(s.shoe_md)} ${left - 2},${y(s.shoe_md) - 9}`} fill="#e8eef4" />
        <text x={W - 2} y={y(s.shoe_md) - 3} fontSize={9.5} fill="var(--ink-2)" textAnchor="end">{s.casing} shoe</text>
      </g>)}
      {Object.entries(tops).filter(([, t]) => t.md < d1 && t.md > 0).map(([c, t]) =>
        <rect key={c} x={left} y={y(t.md - t.sd)} width={fmW - 6} height={Math.max(1, y(t.md + t.sd) - y(t.md - t.sd))} fill="#fff" opacity={0.12} />)}
    </svg>
    {tip}
  </div>;
}

/** Mud-weight window vs depth: offset-derived bounds per formation against the planned MW/ECD. */
export function MWWindowChart({ win, plan, tops, height = 420 }: { win: any; plan: { formation: string; top_md: number; mw: number; ecd: number }[]; tops: Record<string, TopPred>; height?: number }) {
  const { fmName } = useApp();
  const [tip, show, hide] = useTip();
  const W = 520, left = 52, right = 12, top = 18, bot = 30;
  const xs = [8.5, 13.5];
  const x = (v: number) => left + ((v - xs[0]) / (xs[1] - xs[0])) * (W - left - right);
  const entries = plan.filter((p) => win.formations[p.formation]);
  if (!entries.length) return <div className="empty">No window data.</div>;
  const d1 = Math.max(...entries.map((p, i) => entries[i + 1]?.top_md ?? p.top_md + 300));
  const y = (md: number) => top + (md / d1) * (height - top - bot);
  return <div>
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label="Mud weight window">
      {[9, 10, 11, 12, 13].map((v) => <g key={v}><line x1={x(v)} x2={x(v)} y1={top} y2={height - bot} stroke="var(--line)" strokeWidth={0.6} />
        <text x={x(v)} y={height - bot + 14} fontSize={10} fill="var(--ink-3)" textAnchor="middle">{v}</text></g>)}
      <text x={(left + W) / 2} y={height - 3} fontSize={10.5} fill="var(--ink-2)" textAnchor="middle">ppg (equivalent mud weight)</text>
      {entries.map((p, i) => {
        const f = win.formations[p.formation];
        const a = p.top_md, b = entries[i + 1]?.top_md ?? d1;
        const lo = f.window.min_mw ?? xs[0], hi = f.window.max_ecd ?? xs[1];
        return <g key={p.formation} onMouseMove={(e) => show(e, <div><b>{fmName(p.formation)}</b> ({f.n_offsets} offsets)<br />
          Planned MW {p.mw} / ECD {p.ecd} ppg<br />Min MW (kick/collapse P10): {f.window.min_mw ?? "–"}{f.kick.kill_mw_max ? ` · max kill MW ${f.kick.kill_mw_max}` : ""}<br />
          Max ECD (loss P50 − 0.2): {f.window.max_ecd ?? "–"}<br /><span className="muted small">{f.loss.note ?? ""}</span></div>)} onMouseLeave={hide}>
          <rect x={left} y={y(a)} width={W - left - right} height={y(b) - y(a)} fill="transparent" />
          {(f.window.min_mw || f.window.max_ecd) ? <rect x={x(Math.max(lo, xs[0]))} y={y(a) + 1} width={Math.max(0, x(Math.min(hi, xs[1])) - x(Math.max(lo, xs[0])))} height={Math.max(0, y(b) - y(a) - 2)} fill="#0ca30c" opacity={0.18} />
            : <text x={x(12.6)} y={(y(a) + y(b)) / 2 + 3} fontSize={9.5} fill="var(--ink-3)" textAnchor="middle">no offset constraint</text>}
          {f.window.min_mw && <line x1={x(f.window.min_mw)} x2={x(f.window.min_mw)} y1={y(a)} y2={y(b)} stroke="#d95926" strokeWidth={2} />}
          {f.window.max_ecd && <line x1={x(f.window.max_ecd)} x2={x(f.window.max_ecd)} y1={y(a)} y2={y(b)} stroke="#3987e5" strokeWidth={2} />}
          <line x1={x(p.mw)} x2={x(p.mw)} y1={y(a)} y2={y(b)} stroke="#f3f6f9" strokeWidth={2} />
          <line x1={x(p.ecd)} x2={x(p.ecd)} y1={y(a)} y2={y(b)} stroke="#f3f6f9" strokeWidth={1.5} strokeDasharray="4 3" />
          <line x1={left} x2={W - right} y1={y(a)} y2={y(a)} stroke="var(--line-strong)" strokeWidth={0.8} />
          <text x={left - 6} y={y(a) + 11} fontSize={9.5} fill="var(--ink-2)" textAnchor="end">{fmName(p.formation).split(" ")[0]}</text>
        </g>;
      })}
    </svg>
    <div className="row wrap small" style={{ gap: 12 }}>
      <span><span className="swatch" style={{ background: "#0ca30c", opacity: .5 }} /> offset-derived safe window</span>
      <span style={{ color: "#d95926" }}>━ min MW (kick / collapse)</span>
      <span style={{ color: "#3987e5" }}>━ max ECD (induced losses)</span>
      <span>━ planned MW</span><span>┅ planned ECD</span>
    </div>
    {tip}
  </div>;
}
