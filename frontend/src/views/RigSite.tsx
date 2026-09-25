import { useEffect, useState } from "react";
import { useApp } from "../context";
import { AlertDrawer } from "../components/AlertPanel";
import RigPanel, { OfflineBanner } from "../components/RigPanel";
import { getActor, useLive } from "../live";

/** Full-screen rig-site tablet view (#/rig). Installable, and keeps working from its cache when the VSAT link drops. */
export default function RigSite() {
  const { meta, go } = useApp();
  const [live, send, setMode] = useLive();
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { if (meta.stream?.live_available && live.mode !== "live") setMode("live"); }, [meta.stream?.live_available]);
  const selected = sel ? live.alerts.get(sel) ?? null : null;
  const st = live.status;
  return <div className="rigsite">
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <div>
        <h2 className="view">Rig site — {meta.active_well}</h2>
        <div className="small muted">{live.mode === "live" ? `Live rig feed · ${st?.stream?.describe ?? meta.stream?.describe ?? ""}` : "Stored replay"}
          {" · "}on duty: {getActor()}</div>
      </div>
      <div className="row">
        <span className={`tag ${live.connected ? "live" : ""}`}><span className="dot" style={{ background: live.connected ? "var(--good-ink)" : "var(--critical)" }} />
          {live.connected ? "linked to RTOC" : "no link"}</span>
        <button className="btn sm" onClick={() => go("live")}>Full console</button>
      </div>
    </div>
    <OfflineBanner live={live} />
    {st?.waiting && <div className="banner">Linked, waiting for the first WITS packet from the rig…</div>}
    <RigPanel live={live} onSelect={setSel} />
    {selected && <AlertDrawer a={selected} sessionId={live.sessionId} onClose={() => setSel(null)} onAck={() => send({ cmd: "ack", id: selected.id })} />}
  </div>;
}
