import { useEffect, useState } from "react";
import { api } from "../api";

interface SimStatus { available: boolean; running: boolean; frames: number; total: number; bit_md: number | null; speed: number; error: string | null }

const post = (path: string, body?: unknown) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}) });

/** Live Ops (admin, live feed): start and stop the WITS-0 rig simulator without leaving the console. Same simulator
 *  as System → Rig simulator; it sends the stored active well as real WITS-0 frames into this server's listener. */
export default function SimulatorCard() {
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [speed, setSpeed] = useState(60);
  const [fromMd, setFromMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = () => api<{ simulator: SimStatus }>("/api/admin/status").then((s) => setSim(s.simulator)).catch((e) => setErr(e.message));
  useEffect(() => { load(); const t = window.setInterval(load, 2000); return () => clearInterval(t); }, []);
  const act = async (p: Promise<any>) => { setErr(null); try { const r = await p; if (r?.simulator) setSim(r.simulator); } catch (e: any) { setErr(e.message); } };
  const start = () => act(post("/api/admin/simulator/start", { speed, from_md: fromMd === "" ? null : Number(fromMd) }));
  const stop = () => act(post("/api/admin/simulator/stop"));
  if (!sim) return err ? <div className="card small muted" style={{ padding: "8px 12px" }}>Rig simulator unavailable: {err}</div> : null;
  return <div className="card row wrap" style={{ gap: 10, padding: "8px 12px" }}>
    <b className="small">Rig simulator</b>
    {!sim.available ? <span className="small muted">this dataset has no recorded rig stream to transmit (use the Assam demo)</span> : <>
      <span className="small muted">replays the stored well as real WITS-0 frames into this feed</span>
      <label className="row small" style={{ gap: 4 }}>speed
        <input type="number" min={0} max={1000} value={speed} disabled={sim.running} onChange={(e) => setSpeed(Number(e.target.value))} style={{ width: 64 }} />×</label>
      <label className="row small" style={{ gap: 4 }}>from MD
        <input type="number" min={0} placeholder="start" value={fromMd} disabled={sim.running} onChange={(e) => setFromMd(e.target.value)} style={{ width: 80 }} /> m</label>
      {sim.running
        ? <button className="btn sm" onClick={stop}>■ Stop</button>
        : <button className="btn sm primary" onClick={start}>▶ Start</button>}
      {(sim.running || sim.frames > 0) && <span className="small">{sim.running ? "streaming" : "stopped"} · {sim.frames.toLocaleString("en-IN")}
        {sim.total ? ` / ${sim.total.toLocaleString("en-IN")}` : ""} frames sent{sim.bit_md != null ? ` · bit ${Math.round(sim.bit_md).toLocaleString("en-IN")} m` : ""}</span>}
      {sim.error && <span className="small" style={{ color: "var(--bad-ink)" }}>{sim.error}</span>}
    </>}
    {err && <span className="small" style={{ color: "var(--bad-ink)" }}>{err}</span>}
  </div>;
}
