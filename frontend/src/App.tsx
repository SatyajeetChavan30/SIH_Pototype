import { useApp, type View } from "./context";
import { useLive } from "./live";
import Analytics from "./views/Analytics";
import Correlation from "./views/Correlation";
import Ingestion from "./views/Ingestion";
import Knowledge from "./views/Knowledge";
import LiveOps from "./views/LiveOps";
import OffsetMap from "./views/OffsetMap";
import RiskPlanning from "./views/RiskPlanning";

const NAV: { v: View; label: string; ico: string }[] = [
  { v: "live", label: "Live Ops", ico: "◉" },
  { v: "map", label: "Offset Map", ico: "⌖" },
  { v: "correlation", label: "Correlation", ico: "≣" },
  { v: "planning", label: "Risk & Planning", ico: "▦" },
  { v: "knowledge", label: "Knowledge", ico: "⌕" },
  { v: "ingest", label: "Ingestion", ico: "⇪" },
  { v: "analytics", label: "Analytics", ico: "◔" },
];

export default function App() {
  const { view, go, meta } = useApp();
  const [live] = useLive();
  const activeAlerts = [...live.alerts.values()].filter((a) => a.status === "active" && (a.level === "critical" || a.level === "warning")).length;
  return <div className="shell">
    <header className="topbar">
      <div className="brand">
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#1b232c" /><path d="M16 4 L22 28 H10 Z" fill="none" stroke="#3987e5" strokeWidth="2.5" /><circle cx="16" cy="12" r="3" fill="#d95926" /></svg>
        <span>eRTMAC-NWIS <small>Nearby Wells Intelligence System</small></span>
      </div>
      <div className="spacer" />
      <span className={`tag ${live.connected ? "live" : ""}`}><span className="dot" style={{ background: live.connected ? "#57d36a" : "#7f8d9b" }} />
        {live.connected ? `eRTMAC stream · ${meta.active_well}` : "stream offline"}</span>
      {live.status && <span className="tag num">Bit {Math.round(live.status.md).toLocaleString("en-IN")} m MD · {live.status.formation}</span>}
      <span className="tag">OCR: {meta.ocr.available ? meta.ocr.engine : "not installed"}</span>
      <span className="tag">LLM: {meta.llm.backend === "off" ? "off (grounded extractive)" : meta.llm.model}</span>
      <span className="tag synthetic" title="All wells, reports and streams in this demo are synthetic, generated from published Upper-Assam geology">SYNTHETIC DEMO DATA</span>
    </header>
    <nav className="nav" aria-label="Views">
      {NAV.map((n) => <button key={n.v} className={view === n.v ? "on" : ""} onClick={() => go(n.v)}>
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
