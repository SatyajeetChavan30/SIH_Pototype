import { useRef } from "react";
import { fmt } from "../theme";
import type { Sample } from "../types";
import { useTip } from "./Tip";

interface Props {
  samples: Sample[];
  field: keyof Sample;
  label: string;
  unit: string;
  color: string;
  height?: number;
  digits?: number;
  markers?: { t: number; color: string; label: string }[];
  band?: [number, number] | null;   // e.g. offset-derived window
}

/** Time-based strip chart for one drilling channel, with crosshair tooltip. */
export default function StripChart({ samples, field, label, unit, color, height = 64, digits = 1, markers = [], band }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const [tip, show, hide] = useTip();
  const W = 600, H = height, padL = 4, padR = 4, padT = 6, padB = 4;
  const pts = samples.filter((s) => s.state === 0 || field === "pit" || field === "hookload" || field === "gas" || field === "mw");
  const last = pts.length ? (pts[pts.length - 1][field] as number) : null;
  if (pts.length < 2) {
    return <div className="stripwrap"><div className="row small"><b style={{ minWidth: 92 }}>{label}</b><span className="muted">waiting for data…</span></div></div>;
  }
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const vals = pts.map((p) => p[field] as number);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (band) { lo = Math.min(lo, band[0]); hi = Math.max(hi, band[1]); }
  const span = hi - lo || Math.abs(hi) * 0.1 || 1;
  lo -= span * 0.12; hi += span * 0.12;
  const x = (t: number) => padL + ((t - t0) / (t1 - t0 || 1)) * (W - padL - padR);
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[field] as number).toFixed(1)}`).join("");

  const onMove = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const tt = t0 + ((e.clientX - r.left) / r.width) * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - tt) < Math.abs(best.t - tt)) best = p;
    show(e, <div><b>{label}</b>: {(best[field] as number).toFixed(digits)} {unit}<br /><span className="muted">MD {fmt.m(best.md)} · t+{fmt.hours(best.t)}</span></div>);
  };
  return <div className="stripwrap">
    <div className="row small" style={{ justifyContent: "space-between" }}>
      <span className="row" style={{ gap: 6 }}><span className="swatch" style={{ background: color, width: 12, height: 3 }} /><b>{label}</b></span>
      <span className="num" style={{ fontWeight: 650 }}>{last != null ? last.toFixed(digits) : "–"} <span className="muted">{unit}</span></span>
    </div>
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} onMouseMove={onMove} onMouseLeave={hide}
      style={{ display: "block", background: "var(--surface-2)", borderRadius: 6 }} role="img" aria-label={`${label} trend`}>
      {band && <rect x={0} width={W} y={y(band[1])} height={Math.max(0, y(band[0]) - y(band[1]))} fill="#0ca30c" opacity={0.12} />}
      {markers.filter((m) => m.t >= t0 && m.t <= t1).map((m, i) =>
        <line key={i} x1={x(m.t)} x2={x(m.t)} y1={0} y2={H} stroke={m.color} strokeWidth={1.5} strokeDasharray="3 3" />)}
      <path d={d} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
    {tip}
  </div>;
}
