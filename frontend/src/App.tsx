import { OFFICE, roleLabel, useAuth, type Role } from "./auth";
import { useApp, type View } from "./context";
import { useLive } from "./live";
import Analytics from "./views/Analytics";
import Correlation from "./views/Correlation";
import Ingestion from "./views/Ingestion";
import Knowledge from "./views/Knowledge";
import LiveOps from "./views/LiveOps";
import OffsetMap from "./views/OffsetMap";
import RiskPlanning from "./views/RiskPlanning";
import RigSite from "./views/RigSite";

const NAV: { v: View; label: string; ico: string; roles?: Role[] }[] = [
  { v: "live", label: "Live Ops", ico: "◉" },
  { v: "map", label: "Offset Map", ico: "⌖" },
  { v: "correlation", label: "Correlation", ico: "≣" },
  { v: "planning", label: "Risk & Planning", ico: "▦" },
  { v: "knowledge", label: "Knowledge", ico: "⌕" },
  { v: "ingest", label: "Ingestion", ico: "⇪", roles: OFFICE },
  { v: "analytics", label: "Analytics", ico: "◔", roles: OFFICE },
  { v: "rig", label: "Rig-site app", ico: "▣" },
];

export default function App() {
  const { view: asked, go, meta } = useApp();
  const { user, can, logout } = useAuth();
  const [live] = useLive();
  const nav = NAV.filter((n) => !n.roles || can(...n.roles));
  const view = nav.some((n) => n.v === asked) ? asked : "live";   // a view this role cannot open falls back to Live Ops
  if (view === "rig") return <main className="main rigmain"><RigSite /></main>;
  const age = live.status?.stream?.last_packet_age_s;
  const feedTag = live.mode === "live"
    ? `LIVE · ${live.status?.stream?.describe ?? meta.stream?.describe ?? "rig feed"}${age != null ? ` · last packet ${age < 90 ? `${Math.round(age)} s` : `${Math.round(age / 60)} min`} ago` : " · waiting for data"}`
    : `eRTMAC replay · ${meta.active_well}`;
  const activeAlerts = [...live.alerts.values()].filter((a) => a.status === "active" && (a.level === "critical" || a.level === "warning")).length;
  return <div className="shell">
    <header className="topbar">
      <div className="brand">
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#000000" /><path d="M16 4 L22 28 H10 Z" fill="none" stroke="#ffffff" strokeWidth="2.5" /><circle cx="16" cy="12" r="3" fill="#d95926" /></svg>
        <span>eRTMAC-NWIS <small>Nearby Wells Intelligence System</small></span>
      </div>
      <div className="spacer" />
      <span className={`tag ${live.connected ? "live" : ""}`}><span className="dot" style={{ background: live.connected ? "var(--good-ink)" : "var(--ink-3)" }} />
        {live.connected ? feedTag : live.offlineSince ? "OFFLINE · showing cached picture" : "stream offline"}</span>
      {live.status && <span className="tag num">Bit {Math.round(live.status.md).toLocaleString("en-IN")} m MD · {live.status.formation}</span>}
      <span className="tag">OCR: {meta.ocr.available ? meta.ocr.engine : "not installed"}</span>
      <span className="tag llm">LLM: {meta.llm.backend === "off" ? "off (grounded extractive)" : meta.llm.model}</span>
      {meta.synthetic ? <span className="tag synthetic" title={meta.ontology.region?.data_notice ?? "All data in this demo is synthetic"}>SYNTHETIC DEMO DATA</span>
        : <span className="tag live" title={meta.ontology.region?.data_notice}>REAL PUBLIC DATA · Sodir (NLOD)</span>}
      {user && <span className="tag user" title={`Signed in as ${user.username}`}><b>{user.display_name}</b> <span className="muted">{roleLabel(user.role)}</span>
        <button className="linkbtn" onClick={logout}>Sign out</button></span>}
    </header>
    <nav className="nav" aria-label="Views">
      {nav.map((n) => <button key={n.v} className={view === n.v ? "on" : ""} onClick={() => go(n.v)}>
        <span className="ico" aria-hidden>{n.ico}</span>{n.label}
        {n.v === "live" && activeAlerts > 0 && <span className="badge">{activeAlerts}</span>}
      </button>)}
      <div className="sep" />
      <div className="foot">SIH PS 26121 · Oil India Ltd<br />On-prem · offline-capable<br />Every insight cites its source.</div>
    </nav>
    <main className="main">
      {view === "live" && <LiveOps />}
      {view === "map" && <OffsetMap />}
      {view === "correlation" && <Correlation />}
      {view === "planning" && <RiskPlanning />}
      {view === "knowledge" && <Knowledge />}
      {view === "ingest" && <Ingestion />}
      {view === "analytics" && <Analytics />}
    </main>
  </div>;
}
