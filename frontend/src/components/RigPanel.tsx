import { useApp } from "../context";
import type { LiveData } from "../live";
import { HAZARD_COLOR, LEVEL, fmt } from "../theme";
import type { Alert } from "../types";
import { AlertCard } from "./AlertPanel";

const LEVEL_RANK: Record<string, number> = { critical: 3, warning: 2, watch: 1, info: 0 };

/** Minutes since the cached picture was taken, for the offline banner. */
function ago(ms: number) {
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ${m % 60} min ago`;
}

export function OfflineBanner({ live }: { live: LiveData }) {
  if (live.connected || live.offlineSince == null) return null;
  const t = new Date(live.offlineSince).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return <div className="banner row" role="status" style={{ justifyContent: "space-between" }}>
    <span>⚠ <b>OFFLINE</b> — showing the last known picture from {t} ({ago(live.offlineSince)}). Reconnecting automatically.</span>
    {live.outbox > 0 && <span className="pill" style={{ color: "var(--warn-ink)" }}>{live.outbox} acknowledgement{live.outbox > 1 ? "s" : ""} queued</span>}
  </div>;
}

/** Big-type rig-site picture: where the bit is, the one thing to watch, and what worked before. */
export default function RigPanel({ live, onSelect }: { live: LiveData; onSelect: (key: string) => void }) {
  const { fmName, hz } = useApp();
  const st = live.status;
  if (!st) return <div className="empty">Waiting for the first data from the rig…</div>;
  const win = st.window;
  const ecdBad = win?.max_ecd != null && st.ecd > win.max_ecd;
  // an acknowledged alert is still an open problem at the rig: keep showing it until it clears
  const top = [...live.alerts.values()].filter((a: Alert) => a.status !== "cleared" && a.level !== "info")
    .sort((a, b) => (a.status === "active" ? 0 : 1) - (b.status === "active" ? 0 : 1) || LEVEL_RANK[b.level] - LEVEL_RANK[a.level])[0];
  const recs = top?.recommendations?.actions.filter((x) => x.verdict === "recommended").slice(0, 2) ?? [];
  const next = st.zones_ahead.find((z) => (z.distance_m ?? 0) > -1e9);
  const stale = !live.connected && live.offlineSince != null;
  return <div className="rig">
    <div className="card">
      <div className="muted">Bit depth{stale ? " (last known)" : ""}</div><div className="big num">{fmt.m(st.md)}</div>
      <div style={{ fontSize: 20 }}>{fmName(st.formation)} <span className="muted">· {Math.round(st.rel * 100)}% into formation</span></div>
      {st.next_top && <div style={{ fontSize: 18, marginTop: 6 }}>Next: {fmName(st.next_top.formation)} in <b>{fmt.m(st.next_top.distance_m)}</b> (±{st.next_top.sd} m)</div>}
      <div style={{ fontSize: 18, marginTop: 10 }}>MW {st.mw.toFixed(2)} · ECD <span style={{ color: ecdBad ? LEVEL.critical.color : undefined }}>{st.ecd.toFixed(2)}</span> ppg
        {win?.max_ecd != null && <span className="muted small"> (offset max ECD {win.max_ecd})</span>}</div>
      {next && <div style={{ fontSize: 18, marginTop: 10 }}>
        <span className="swatch" style={{ background: HAZARD_COLOR[next.hazard] }} /> {hz(next.hazard)?.label ?? next.hazard} zone{" "}
        {(next.distance_m ?? 0) > 0 ? <>in <b>{fmt.m(next.distance_m)}</b></> : <b>— bit inside</b>}
        <span className="muted small"> · {next.n_events}/{next.n_exposed} offsets had it</span></div>}
    </div>
    <div className="col">
      {top ? <AlertCard a={top} big onClick={() => onSelect(top.key)} />
        : <div className="card" style={{ fontSize: 22, color: "var(--good-ink)" }}>● All clear — no active warnings</div>}
      {recs.length > 0 && <div className="card"><div className="muted">Do now (worked in offsets)</div>
        {recs.map((r) => <div key={r.code} style={{ fontSize: 18, marginTop: 4 }}>✔ {r.label} <span className="muted small">cured {r.cured}/{r.attempts}</span></div>)}
        {top?.recommendations?.preventive[0] && <div style={{ marginTop: 6 }}>{top.recommendations.preventive[0].text}</div>}
      </div>}
    </div>
  </div>;
}
