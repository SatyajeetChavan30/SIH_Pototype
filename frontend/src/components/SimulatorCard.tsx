import { useState } from "react";
import { useJobs } from "../jobs";
import { JobLog } from "./JobsCard";

/** Live Ops: drive the WITS-0 rig simulator into this server's listener instead of running simulate-rig by hand. */
export default function SimulatorCard() {
  const j = useJobs();
  const [speed, setSpeed] = useState(60);
  const [fromMd, setFromMd] = useState("");
  const [showLog, setShowLog] = useState(false);
  const kind = j.kinds.find((k) => k.key === "simulate-rig");
  if (!kind) return null;
  const last = j.latest("simulate-rig");
  const running = last?.status === "running";
  const sent = last?.lines ? [...last.lines].reverse().map((l) => l.match(/sent (\d+)/)).find(Boolean)?.[1] : undefined;
  return <div className="card row wrap" style={{ gap: 10, padding: "8px 12px" }}>
    <b className="small">Rig simulator</b>
    {!kind.available ? <span className="small muted">{kind.reason}</span> : <>
      <span className="small muted">replays the stored well as real WITS-0 frames into this feed</span>
      <label className="row small" style={{ gap: 4 }}>speed
        <input type="number" min={0} max={1000} value={speed} disabled={running} onChange={(e) => setSpeed(Number(e.target.value))} style={{ width: 64 }} />×</label>
      <label className="row small" style={{ gap: 4 }}>from MD
        <input type="number" min={0} placeholder="start" value={fromMd} disabled={running} onChange={(e) => setFromMd(e.target.value)} style={{ width: 80 }} /> m</label>
      {running
        ? <button className="btn sm" onClick={() => j.cancel(last!.id)}>■ Stop</button>
        : <button className="btn sm primary" onClick={() => j.start("simulate-rig", { speed, from_md: fromMd === "" ? null : Number(fromMd) })}>▶ Start</button>}
      {last && <span className="small">{running ? "streaming" : last.status === "cancelled" ? "stopped" : last.status === "done" ? "finished" : last.status}{sent ? ` · ${Number(sent).toLocaleString("en-IN")} frames sent` : ""}
        {last.status === "failed" && last.error ? ` · ${last.error}` : ""}</span>}
      {last && <button className="btn sm ghost" onClick={() => setShowLog(!showLog)}>{showLog ? "hide log" : "log"}</button>}
    </>}
    {j.err && <span className="small" style={{ color: "var(--bad-ink)" }}>{j.err}</span>}
    {showLog && last?.lines && <div style={{ flexBasis: "100%" }}><JobLog lines={last.lines} height={120} /></div>}
  </div>;
}
