import { useApp } from "../context";
import { HAZARD_ABBR, HAZARD_COLOR, HAZARD_SHORT, fmt, riskColor } from "../theme";
import type { RibbonBin, TopPred, Zone } from "../types";
import { useTip } from "./Tip";

interface Props {
  bitMd: number;
  bins: RibbonBin[];
  tops: Record<string, TopPred>;
  zones: Zone[];
  hazards: string[];
  above?: number;
  below?: number;
  height?: number;
}

/** Vertical look-ahead ribbon: formation column + per-hazard risk (formation-aligned), bit position, predicted tops +/- sd. */
export default function DepthRibbon({ bitMd, bins, tops, zones, hazards, above = 60, below = 320, height = 520 }: Props) {
  const { fm, fmName } = useApp();
  const [tip, show, hide] = useTip();
  const d0 = Math.max(0, bitMd - above), d1 = bitMd + below;
  const W = 330, fmW = 92, colW = (W - fmW - 44) / hazards.length, top = 24;
  const y = (md: number) => top + ((md - d0) / (d1 - d0)) * (height - top - 8);
  const vis = bins.filter((b) => b.md1 >= d0 && b.md0 <= d1);
  const topsVis = Object.entries(tops).filter(([, t]) => t.md >= d0 - 200 && t.md <= d1 + 200);
  const fmBands: { code: string; a: number; b: number }[] = [];
  for (const b of vis) {
    const last = fmBands[fmBands.length - 1];
    if (last && last.code === b.formation) last.b = b.md1; else fmBands.push({ code: b.formation, a: b.md0, b: b.md1 });
  }
  const ticks: number[] = [];
  for (let m = Math.ceil(d0 / 50) * 50; m <= d1; m += 50) ticks.push(m);
  return <div>
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label="Look-ahead risk ribbon">
      {hazards.map((h, i) => <g key={h}>
        <rect x={44 + fmW + i * colW + colW / 2 - 4} y={2} width={8} height={6} rx={2} fill={HAZARD_COLOR[h]} />
        <text x={44 + fmW + i * colW + colW / 2} y={19} fontSize={9} fill="var(--ink-2)" textAnchor="middle">{HAZARD_ABBR[h]}</text>
        <title>{HAZARD_SHORT[h]}</title></g>)}
      {ticks.map((m) => <g key={m}>
        <line x1={40} x2={W} y1={y(m)} y2={y(m)} stroke="var(--line)" strokeWidth={0.6} />
        <text x={36} y={y(m) + 3} fontSize={9.5} fill="var(--ink-3)" textAnchor="end" className="num">{m}</text>
      </g>)}
      {fmBands.map((f, i) => {
        const y0 = Math.max(y(f.a), top), y1 = Math.min(y(f.b), height - 8);
        return <g key={i}>
          <rect x={44} y={y0} width={fmW - 4} height={Math.max(0, y1 - y0)} fill={fm(f.code)?.color ?? "#555"} opacity={0.35} />
          {y1 - y0 > 18 && <text x={48} y={y0 + 13} fontSize={10.5} fill="var(--ink)">{fmName(f.code)}</text>}
        </g>;
      })}
      {vis.map((b) => hazards.map((h, i) => {
        const p = b.risk[h] ?? 0;
        const y0 = Math.max(y(b.md0), top), y1 = Math.min(y(b.md1), height - 8);
        return <rect key={`${b.md0}-${h}`} x={44 + fmW + i * colW + 1} y={y0 + 0.5} width={colW - 2} height={Math.max(0, y1 - y0 - 1)} rx={2}
          fill={riskColor(p)} onMouseMove={(e) => show(e, <div><b style={{ color: HAZARD_COLOR[h] }}>{HAZARD_SHORT[h]}</b> {fmt.pct(p)} within ±30 m<br />
            <span className="muted">{fmt.m(b.md0)}–{fmt.m(b.md1)} · {fmName(b.formation)}</span></div>)} onMouseLeave={hide} />;
      }))}
      {zones.filter((z) => z.md1 >= d0 && z.md0 <= d1).map((z, i) => {
        const hi = hazards.indexOf(z.hazard);
        if (hi < 0) return null;
        const x = 44 + fmW + hi * colW;
        return <rect key={i} x={x} y={Math.max(y(z.md0), top)} width={colW} height={Math.max(4, Math.min(y(z.md1), height - 8) - Math.max(y(z.md0), top))}
          fill="none" stroke={HAZARD_COLOR[z.hazard]} strokeWidth={2} rx={3} />;
      })}
      {topsVis.map(([code, t]) => {
        if (t.md < d0 || t.md > d1) return null;
        const sdPx = Math.abs(y(t.md + t.sd) - y(t.md));
        return <g key={code} onMouseMove={(e) => show(e, <div><b>{fmName(code)} top</b> {fmt.m(t.md)} MD / {fmt.m(t.tvd)} TVD<br />
          <span className="muted">{t.picked ? "picked (actual)" : `predicted ±${t.sd} m from ${t.n} offsets`}{t.shifted_m ? ` · re-anchored ${t.shifted_m > 0 ? "+" : ""}${t.shifted_m} m` : ""}</span></div>)} onMouseLeave={hide}>
          {!t.picked && <rect x={44} y={y(t.md) - sdPx} width={W - 44} height={sdPx * 2} fill="#9fb3c8" opacity={0.08} />}
          <line x1={44} x2={W} y1={y(t.md)} y2={y(t.md)} stroke={t.picked ? "#e8eef4" : "#9fb3c8"} strokeWidth={t.picked ? 1.5 : 1} strokeDasharray={t.picked ? "" : "4 3"} />
        </g>;
      })}
      <g>
        <line x1={40} x2={W} y1={y(bitMd)} y2={y(bitMd)} stroke="#fff" strokeWidth={2} />
        <polygon points={`${34},${y(bitMd) - 6} ${44},${y(bitMd)} ${34},${y(bitMd) + 6}`} fill="#fff" />
        <text x={W - 2} y={y(bitMd) - 4} fontSize={10} fill="#fff" textAnchor="end" fontWeight={700}>BIT {Math.round(bitMd)} m</text>
      </g>
    </svg>
    <div className="small muted" style={{ marginTop: 4 }}>Cells: blended offset-evidence + ML risk of each hazard within ±30 m (darker = lower). Outlined: offset-event clusters projected by formation. Dashed lines: predicted tops ±1σ; solid: picked tops.</div>
    {tip}
  </div>;
}
