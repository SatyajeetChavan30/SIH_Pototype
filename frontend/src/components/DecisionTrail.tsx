import { useEffect, useState } from "react";
import { api, qs } from "../api";
import { fmt } from "../theme";
import type { AuditRow } from "../types";

const EVENT_LABEL: Record<string, string> = {
  opened: "shown to the console", escalated: "escalated", acknowledged: "acknowledged", cleared: "cleared",
  feedback: "feedback", held_in_digest: "held in digest (alarm budget)",
};

/** Tamper-evident decision log for one alert: what NWIS showed, when, who acted and what they said. */
export default function DecisionTrail({ alertId, alertKey, refresh }: { alertId: string; alertKey: string; refresh: string }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  useEffect(() => {
    let off = false;
    Promise.all([api<{ rows: AuditRow[] }>(`/api/audit?${qs({ alert_id: alertId, limit: 50 })}`),
      api<{ rows: AuditRow[] }>(`/api/audit?${qs({ alert_key: alertKey, limit: 50 })}`)])
      .then(([a, b]) => {
        if (off) return;
        const seen = new Map<number, AuditRow>();
        [...a.rows, ...b.rows.filter((r) => r.event === "held_in_digest")].forEach((r) => seen.set(r.seq, r));
        setRows([...seen.values()].sort((x, y) => x.seq - y.seq));
      })
      .catch(() => !off && setRows([]));
    return () => { off = true; };
  }, [alertId, alertKey, refresh]);
  if (rows === null) return <div className="small muted">Loading decision log…</div>;
  if (!rows.length) return <div className="small muted">Nothing logged yet for this alert.</div>;
  return <div className="col" style={{ gap: 4 }}>
    {rows.map((r) => <div key={r.seq} className="small row" style={{ gap: 8, alignItems: "baseline" }}>
      <span className="muted num mono" title={`hash ${r.hash}`}>#{r.seq}</span>
      <span className="muted num">{r.t != null ? `t+${fmt.hours(r.t)}` : r.ts_wall.slice(11)}</span>
      <span><b>{r.actor}</b> · {EVENT_LABEL[r.event] ?? r.event}{r.level ? ` (${r.level})` : ""}
        {r.payload?.verdict ? ` — ${String(r.payload.verdict).replace("_", " ")}` : ""}
        {r.payload?.reason ? ` — ${r.payload.reason}` : ""}</span>
    </div>)}
    <div className="small muted">Append-only and hash-chained: editing any past entry breaks verification (Analytics → Decision log).</div>
  </div>;
}
