import { useMemo, useState } from "react";
import { useApp } from "../context";
import { AlertCard, AlertDrawer } from "../components/AlertPanel";
import DepthRibbon from "../components/DepthRibbon";
import StripChart from "../components/StripChart";
import { useLive } from "../live";
import { HAZARD_COLOR, LEVEL, fmt } from "../theme";
import type { Alert } from "../types";

const LEVEL_RANK: Record<string, number> = { critical: 3, warning: 2, watch: 1, info: 0 };

export default function LiveOps() {
  const { meta, fmName } = useApp();
  const [live, send] = useLive();
  const [sel, setSel] = useState<string | null>(null);
  const [rig, setRig] = useState(false);
  const [showCleared, setShowCleared] = useState(false);
  const st = live.status;

  const alerts = useMemo(() => [...live.alerts.values()]
    .filter((a) => showCleared || a.status !== "cleared")
    .sort((a, b) => (a.status === "cleared" ? 1 : 0) - (b.status === "cleared" ? 1 : 0) || LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || b.updated_t - a.updated_t),
  [live.version, showCleared]);
  const selected = sel ? live.alerts.get(sel) ?? null : null;
  const markers = useMemo(() => [...live.alerts.values()].filter((a) => a.source !== "geology")
    .map((a) => ({ t: a.t, color: LEVEL[a.level].color, label: a.title })), [live.version]);

  if (!live.connected && !st) return <div className="empty">Connecting to the eRTMAC stream…</div>;
  const win = st?.window;
  const ecdBad = win?.max_ecd != null && st && st.ecd > win.max_ecd;
  const mwBad = win?.min_mw != null && st && st.mw < win.min_mw;
  const top = alerts.find((a) => a.status === "active" && a.level !== "info");

  const controls = <div className="row wrap" style={{ gap: 8 }}>
    <button className="btn sm primary" onClick={() => send({ cmd: live.playing ? "pause" : "play" })}>{live.playing ? "❚❚ Pause" : "▶ Play"}</button>
    <div className="seg" role="group" aria-label="Replay speed">
      {[1, 4, 10, 25].map((s) => <button key={s} className={live.speed === s ? "on" : ""} onClick={() => send({ cmd: "speed", value: s })}>{s}×</button>)}
    </div>
    <span className="small muted">Jump to:</span>
    {live.episodes.map((e) => <button key={e.id} className={`btn sm ${st?.episode === e.id ? "primary" : ""}`} title={e.label}
      onClick={() => { setSel(null); send({ cmd: "jump", episode: e.id }); }}>
      <span className="swatch" style={{ background: HAZARD_COLOR[e.hazard] }} /> {e.id} · {e.label.split(" ").slice(0, 3).join(" ")}</button>)}
    <button className="btn sm ghost" onClick={() => { setSel(null); send({ cmd: "restart" }); }}>↺ Restart</button>
    <button className={`btn sm ${rig ? "primary" : ""}`} onClick={() => setRig(!rig)}>{rig ? "RTOC view" : "Rig-site view"}</button>
  </div>;

  if (rig && st) {
    const recs = top?.recommendations?.actions.filter((x) => x.verdict === "recommended").slice(0, 2) ?? [];
    return <div className="col">
      {controls}
      <div className="rig">
        <div className="card">
          <div className="muted">Bit depth</div><div className="big num">{fmt.m(st.md)}</div>
          <div style={{ fontSize: 20 }}>{fmName(st.formation)} <span className="muted">· {Math.round(st.rel * 100)}% into formation</span></div>
          {st.next_top && <div style={{ fontSize: 18, marginTop: 6 }}>Next: {fmName(st.next_top.formation)} in <b>{fmt.m(st.next_top.distance_m)}</b> (±{st.next_top.sd} m)</div>}
          <div style={{ fontSize: 18, marginTop: 10 }}>MW {st.mw.toFixed(2)} · ECD <span style={{ color: ecdBad ? LEVEL.critical.color : undefined }}>{st.ecd.toFixed(2)}</span> ppg</div>
        </div>
        <div className="col">
          {top ? <AlertCard a={top} big onClick={() => setSel(top.key)} /> : <div className="card" style={{ fontSize: 22, color: "#57d36a" }}>● All clear — no active warnings</div>}
          {recs.length > 0 && <div className="card"><div className="muted">Do now (worked in offsets)</div>
            {recs.map((r) => <div key={r.code} style={{ fontSize: 18, marginTop: 4 }}>✔ {r.label} <span className="muted small">cured {r.cured}/{r.attempts}</span></div>)}
            {top?.recommendations?.preventive[0] && <div style={{ marginTop: 6 }}>{top.recommendations.preventive[0].text}</div>}
          </div>}
        </div>
      </div>
      {selected && <AlertDrawer a={selected} onClose={() => setSel(null)} onAck={() => send({ cmd: "ack", id: selected.id })} />}
    </div>;
  }

  return <div className="col">
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <div>
        <h2 className="view">Live Ops — {meta.active_well} (active well)</h2>
        <div className="small muted">eRTMAC stream replay · detectors + formation-aligned offset look-ahead + mud window, fused into one alert feed</div>
      </div>
      {controls}
    </div>
    {st && <div className="kpis">
      <div className="kpi"><div className="k">Bit depth</div><div className="v num">{fmt.m(st.md)}</div><div className="d num">TVD {fmt.m(st.tvd)}</div></div>
      <div className="kpi"><div className="k">Formation (estimated)</div><div className="v" style={{ fontSize: 17 }}>{fmName(st.formation)}</div>
        <div className="d">{Math.round(st.rel * 100)}% into formation · {Object.keys(st.picked).length} tops picked</div></div>
      <div className="kpi"><div className="k">Next top</div><div className="v" style={{ fontSize: 17 }}>{st.next_top ? fmName(st.next_top.formation) : "–"}</div>
        <div className="d num">{st.next_top ? `in ${fmt.m(st.next_top.distance_m)} (±${st.next_top.sd} m)` : ""}</div></div>
      <div className="kpi"><div className="k">MW / ECD vs offset window</div>
        <div className="v num" style={{ fontSize: 17 }}><span style={{ color: mwBad ? LEVEL.critical.color : undefined }}>{st.mw.toFixed(2)}</span> / <span style={{ color: ecdBad ? LEVEL.critical.color : undefined }}>{st.ecd.toFixed(2)}</span> ppg</div>
        <div className="d">{win ? `min MW ${win.min_mw ?? "–"} · max ECD ${win.max_ecd ?? "–"}` : "no window for this formation"}
          {(ecdBad || mwBad) && <b style={{ color: LEVEL.critical.color }}> ▲ outside</b>}</div></div>
      <div className="kpi"><div className="k">Next offset hazard zone</div>
        {st.zones_ahead[0] ? <><div className="v" style={{ fontSize: 17 }}><span className="swatch" style={{ background: HAZARD_COLOR[st.zones_ahead[0].hazard] }} /> {meta.ontology.hazards.find((h) => h.code === st.zones_ahead[0].hazard)?.label}</div>
          <div className="d num">{st.zones_ahead[0].distance_m! > 0 ? `in ${fmt.m(st.zones_ahead[0].distance_m)}` : "bit inside zone"} · {st.zones_ahead[0].n_events}/{st.zones_ahead[0].n_exposed} offsets</div></> : <div className="v">none</div>}</div>
      <div className="kpi"><div className="k">Replay progress</div><div className="v num">{Math.round(st.progress * 100)}%</div>
        <div className="progress"><div style={{ width: `${st.progress * 100}%` }} /></div><div className="d num">t+{fmt.hours(st.t)}</div></div>
    </div>}

    <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1.25fr) 330px minmax(300px, 1fr)", gap: 12 }}>
      <div className="card col" style={{ gap: 8 }}>
        <h3>Drilling channels <span className="sub">WITS/WITSML · last {live.samples.length} samples</span></h3>
        <StripChart samples={live.samples} field="flow_out" label="Flow out" unit="%" color="#3987e5" markers={markers} />
        <StripChart samples={live.samples} field="pit" label="Active pit" unit="bbl" color="#199e70" markers={markers} />
        <StripChart samples={live.samples} field="torque" label="Torque" unit="kft·lbf" color="#c98500" markers={markers} />
        <StripChart samples={live.samples} field="spp" label="Standpipe pressure" unit="psi" color="#9085e9" digits={0} markers={markers} />
        <StripChart samples={live.samples} field="gas" label="Total gas" unit="%" color="#d95926" digits={2} markers={markers} />
        <StripChart samples={live.samples} field="dxc" label="Corrected d-exponent" unit="" color="#d55181" digits={3} markers={markers} />
        <StripChart samples={live.samples} field="rop" label="ROP" unit="m/hr" color="#8fa3b8" markers={markers} />
        <StripChart samples={live.samples} field="ecd" label="ECD" unit="ppg" color="#e66767" digits={2} markers={markers}
          band={win && win.max_ecd ? [win.min_mw ?? 8.5, win.max_ecd] : null} />
      </div>
      <div className="card">
        <h3>Look-ahead ribbon <span className="sub">next 320 m</span></h3>
        {st && <DepthRibbon bitMd={st.md} bins={live.ribbon} tops={live.tops} zones={live.zones} hazards={meta.ribbon_hazards} height={600} />}
      </div>
      <div className="col">
        <div className="card col" style={{ gap: 8, maxHeight: 560, overflow: "auto" }}>
          <h3>Alert feed <span className="sub">{alerts.filter((a) => a.status !== "cleared").length} open</span>
            <label className="small muted" style={{ marginLeft: "auto" }}><input type="checkbox" checked={showCleared} onChange={(e) => setShowCleared(e.target.checked)} /> show cleared</label></h3>
          {alerts.length === 0 && <div className="empty">No alerts yet. Offset look-ahead alerts appear ~150 m before each projected hazard zone.</div>}
          {alerts.map((a: Alert) => <AlertCard key={a.key} a={a} selected={a.key === sel} onClick={() => setSel(a.key)} />)}
        </div>
        <div className="card">
          <h3>Geology events</h3>
          {live.events.length === 0 && <div className="small muted">Top picks re-anchor the look-ahead automatically.</div>}
          {live.events.map((e, i) => <div key={i} className="small">• {e.message}</div>)}
        </div>
      </div>
    </div>
    {selected && <AlertDrawer a={selected} onClose={() => setSel(null)} onAck={() => send({ cmd: "ack", id: selected.id })} />}
  </div>;
}
