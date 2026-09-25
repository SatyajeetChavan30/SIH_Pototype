import { useAuth } from "../auth";
import { useMemo, useState } from "react";
import { useApp } from "../context";
import { AlertCard, AlertDrawer } from "../components/AlertPanel";
import BriefModal from "../components/BriefModal";
import DepthRibbon from "../components/DepthRibbon";
import RigPanel, { OfflineBanner } from "../components/RigPanel";
import StripChart from "../components/StripChart";
import { getActor, setActor, useLive } from "../live";
import { HAZARD_COLOR, LEVEL, fmt } from "../theme";
import type { Alert } from "../types";

const LEVEL_RANK: Record<string, number> = { critical: 3, warning: 2, watch: 1, info: 0 };

export default function LiveOps() {
  const { user } = useAuth();
  const { meta, fmName } = useApp();
  const [live, send, setMode] = useLive();
  const [sel, setSel] = useState<string | null>(null);
  const [rig, setRig] = useState(false);
  const [showCleared, setShowCleared] = useState(true);
  const [actor, setActorState] = useState(getActor());
  const st = live.status;
  const budget = st?.budget?.budget_per_hour ?? 1;
  const load = st?.alert_load;

  const alerts = useMemo(() => [...live.alerts.values()]
    .filter((a) => showCleared || a.status !== "cleared")
    .sort((a, b) => (a.status === "cleared" ? 1 : 0) - (b.status === "cleared" ? 1 : 0) || LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || b.updated_t - a.updated_t),
  [live.version, showCleared]);
  const selected = sel ? live.alerts.get(sel) ?? null : null;
  const markers = useMemo(() => [...live.alerts.values()].filter((a) => a.source !== "geology")
    .map((a) => ({ t: a.t, color: LEVEL[a.level].color, label: a.title })), [live.version]);

  if (live.noStream && !meta.stream?.live_available) return <div className="card" style={{ maxWidth: 760 }}>
    <h3>No real-time stream in this dataset</h3>
    <div>{live.noStream}</div>
    <div className="small muted" style={{ marginTop: 8 }}>{meta.ontology.region?.data_notice} Offset Map, Correlation, Risk &amp; Planning and Knowledge work on this data.</div>
  </div>;
  if (!live.connected && !st) return <div className="empty">Connecting to the eRTMAC stream…</div>;
  const win = st?.window;
  const ecdBad = win?.max_ecd != null && st && st.ecd > win.max_ecd;
  const mwBad = win?.min_mw != null && st && st.mw < win.min_mw;
  const top = alerts.find((a) => a.status === "active" && a.level !== "info");
  const pausedAlert = live.pausedOn && !live.playing ? live.alerts.get(live.pausedOn) : undefined;
  const pausedBanner = pausedAlert && <div className="banner row" role="status" style={{ justifyContent: "space-between", borderColor: LEVEL.critical.color }}>
    <span>{LEVEL.critical.icon} Replay paused on a new critical alert: <b>{pausedAlert.title}</b> at {fmt.m(pausedAlert.md)}</span>
    <span className="row"><button className="btn sm" onClick={() => setSel(pausedAlert.key)}>Show evidence</button>
      <button className="btn sm primary" onClick={() => send({ cmd: "play" })}>▶ Resume</button></span>
  </div>;

  const isLive = live.mode === "live";
  const controls = <div className="row wrap" style={{ gap: 8 }}>
    {meta.stream?.live_available && <div className="seg" role="group" aria-label="Data source">
      <button className={!isLive ? "on" : ""} onClick={() => { setSel(null); setMode("replay"); }}>Replay</button>
      <button className={isLive ? "on" : ""} onClick={() => { setSel(null); setMode("live"); }} title={meta.stream.describe}>● Live rig feed</button>
    </div>}
    {!isLive && <>
    <button className="btn sm primary" onClick={() => send({ cmd: live.playing ? "pause" : "play" })}>{live.playing ? "❚❚ Pause" : "▶ Play"}</button>
    <div className="seg" role="group" aria-label="Replay speed">
      {[1, 4, 10, 25].map((s) => <button key={s} className={live.speed === s ? "on" : ""} onClick={() => send({ cmd: "speed", value: s })}>{s}×</button>)}
    </div>
    <span className="small muted">Jump to:</span>
    {live.episodes.map((e) => <button key={e.id} className={`btn sm ${st?.episode === e.id ? "primary" : ""}`} title={e.label}
      onClick={() => { setSel(null); send({ cmd: "jump", episode: e.id }); }}>
      <span className="swatch" style={{ background: HAZARD_COLOR[e.hazard] }} /> {e.id} · {e.label.split(" ").slice(0, 3).join(" ")}</button>)}
    <button className="btn sm ghost" onClick={() => { setSel(null); send({ cmd: "restart" }); }}>↺ Restart</button>
    <label className="small row" style={{ gap: 4 }} title="Pause the replay whenever a new critical alert opens">
      <input type="checkbox" checked={live.autoPause} onChange={(e) => send({ cmd: "autoPause", value: e.target.checked })} /> auto-pause on critical</label>
    </>}
    <button className={`btn sm ${rig ? "primary" : ""}`} onClick={() => setRig(!rig)}>{rig ? "RTOC view" : "Rig-site view"}</button>
    <button className="btn sm" onClick={() => send({ cmd: "handover", hours: 12 })} title="Cited summary of the last 12 h for the next shift">📝 Handover brief</button>
  </div>;
  const budgetBar = <div className="row wrap small" style={{ gap: 8 }}>
    <span className="muted" title="Non-critical alerts per hour this console can act on. Critical and look-ahead-corroborated alerts always show; weaker signals over budget go to the digest, never silently dropped.">Alarm budget:</span>
    <div className="seg" role="group" aria-label="Alarm budget per hour">
      {[0.5, 1, 2, 4].map((b) => <button key={b} className={Math.abs(budget - b) < 1e-6 ? "on" : ""} onClick={() => send({ cmd: "budget", value: b })}>{b}/h</button>)}
    </div>
    {load && <span className={load.within_budget ? "muted" : ""} style={{ color: load.within_budget ? undefined : LEVEL.warning.color }}>
      load {load.non_critical_per_hour}/h non-critical · {load.opened} opened in last {load.window_h} h</span>}
    <span className="muted">· on duty as</span>
    {user ? <b title="Signed in: acknowledgements are logged under this name">{user.display_name}</b>
      : <input type="text" value={actor} style={{ width: 120, padding: "3px 8px" }} aria-label="Your name for the decision log"
        onChange={(e) => { setActorState(e.target.value); setActor(e.target.value || "RTOC"); }} />}
  </div>;

  const stream = st?.stream;
  const statusBanners = <>
    <OfflineBanner live={live} />
    {isLive && st?.waiting && <div className="banner">Linked to {stream?.describe ?? "the rig feed"}; waiting for the first WITS packet…</div>}
    {isLive && live.events.find((e) => e.type === "stream_gap" || e.type === "stream_resumed")?.type === "stream_gap" &&
      <div className="banner" style={{ borderColor: LEVEL.warning.color }}>⚠ {live.events.find((e) => e.type === "stream_gap")!.message}</div>}
  </>;

  if (rig && st) {
    return <div className="col">
      {controls}
      {budgetBar}
      {pausedBanner}
      {statusBanners}
      <RigPanel live={live} onSelect={setSel} />
      {selected && <AlertDrawer a={selected} sessionId={live.sessionId} onClose={() => setSel(null)} onAck={() => send({ cmd: "ack", id: selected.id })} />}
      {live.handover && <BriefModal brief={live.handover} subtitle="Shift handover" onClose={() => send({ cmd: "closeHandover" })} />}
    </div>;
  }

  return <div className="col">
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <div>
        <h2 className="view">Live Ops — {meta.active_well} (active well)</h2>
        <div className="small muted">{isLive ? `Live rig feed (${stream?.describe ?? meta.stream?.describe}), shared by every console` : "eRTMAC stream replay"} · detectors + formation-aligned offset look-ahead + mud window, fused into one alert feed</div>
      </div>
      {controls}
    </div>
    {budgetBar}
    {pausedBanner}
    {statusBanners}
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
      {isLive && stream ? <div className="kpi"><div className="k">Rig feed ({stream.kind === "wits0" ? "WITS-0" : "WITSML"})</div>
          <div className="v num" style={{ fontSize: 17 }}>{stream.packets.toLocaleString("en-IN")} packets</div>
          <div className="d" title={`derived by NWIS (not sent by the rig): ${stream.derived.join(", ")}`}>
            {stream.connected ? `from ${stream.peer}` : "not connected"} · {stream.errors} bad lines · derived: {stream.derived.join(", ") || "none"}</div></div>
        : <div className="kpi"><div className="k">Replay progress</div><div className="v num">{Math.round(st.progress * 100)}%</div>
        <div className="progress"><div style={{ width: `${st.progress * 100}%` }} /></div><div className="d num">t+{fmt.hours(st.t)}</div></div>}
    </div>}

    <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1.25fr) 330px minmax(300px, 1fr)", gap: 12 }}>
      <div className="card col" style={{ gap: 8 }}>
        <h3>Drilling channels <span className="sub">WITS/WITSML · last {live.samples.length} samples</span></h3>
        <StripChart samples={live.samples} field="flow_out" label="Flow out" unit="%" color="#3987e5" markers={markers} />
        <StripChart samples={live.samples} field="pit" label="Active pit" unit="bbl" color="#199e70" markers={markers} />
        <StripChart samples={live.samples} field="torque" label="Torque" unit="kft·lbf" color="#c98500" markers={markers} expected="exp_torque" />
        <StripChart samples={live.samples} field="spp" label="Standpipe pressure" unit="psi" color="#9085e9" digits={0} markers={markers} expected="exp_spp" />
        <StripChart samples={live.samples} field="hookload" label="Hookload" unit="klbs" color="#1f5fbf" markers={markers} expected="exp_hookload" />
        <StripChart samples={live.samples} field="gas" label="Total gas" unit="%" color="#d95926" digits={2} markers={markers} />
        <StripChart samples={live.samples} field="dxc" label="Corrected d-exponent" unit="" color="#d55181" digits={3} markers={markers} />
        <StripChart samples={live.samples} field="rop" label="ROP" unit="m/hr" color="#6b6b6b" markers={markers} />
        <StripChart samples={live.samples} field="ecd" label="ECD" unit="ppg" color="#e66767" digits={2} markers={markers}
          band={win && win.max_ecd ? [win.min_mw ?? 8.5, win.max_ecd] : null} expected="exp_ecd" />
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
          <h3>Geology events <span className="sub">top picks: {(st?.top_pick_mode ?? meta.top_pick_mode) === "dtw" ? "GR correlation (DTW) only" : meta.top_pick_mode === "mudlogger" ? "mud logger" : "mud logger + DTW QC"}</span></h3>
          {live.events.length === 0 && <div className="small muted">Top picks re-anchor the look-ahead automatically.</div>}
          {live.events.map((e, i) => <div key={i} className="small" style={{ marginTop: 2 }}>
            {e.source === "dtw" && <span className="pill" style={{ color: e.conflict ? LEVEL.warning.color : "var(--good-ink)", marginRight: 4 }}>{e.conflict ? "DTW CONFLICT" : "DTW"}</span>}
            {e.message}</div>)}
        </div>
        {(st?.digest?.length ?? 0) > 0 && <div className="card">
          <h3>Held in digest <span className="sub">alarm budget · reviewed, not discarded</span></h3>
          {st!.digest!.slice().reverse().map((d, i) => <div key={i} className="small" style={{ marginTop: 2 }}>
            <span className="muted num">{fmt.m(d.md)}</span> · {d.title} <span className="muted">— {d.reason}{d.p_value != null ? `, p=${d.p_value}` : ""}</span></div>)}
        </div>}
      </div>
    </div>
    {selected && <AlertDrawer a={selected} sessionId={live.sessionId} onClose={() => setSel(null)} onAck={() => send({ cmd: "ack", id: selected.id })} />}
    {live.handover && <BriefModal brief={live.handover} subtitle="Shift handover" onClose={() => send({ cmd: "closeHandover" })} />}
  </div>;
}
