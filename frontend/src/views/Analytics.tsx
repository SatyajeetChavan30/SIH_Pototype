import { useEffect, useState } from "react";
import { api } from "../api";
import { roleLabel, useAuth, type Role, type User } from "../auth";
import { useApp } from "../context";
import JobsCard from "../components/JobsCard";
import { useTip } from "../components/Tip";
import { HAZARD_COLOR, HAZARD_SHORT, fmt, riskColor } from "../theme";

function HBar({ rows, max, fmtV, height = 22 }: { rows: { label: string; value: number; color: string; note?: string; bold?: boolean }[]; max?: number; fmtV: (v: number) => string; height?: number }) {
  const [tip, show, hide] = useTip();
  const m = max ?? Math.max(...rows.map((r) => r.value), 1e-9);
  return <div className="col" style={{ gap: 6 }}>
    {rows.map((r) => <div key={r.label} className="row" style={{ gap: 8 }} onMouseMove={(e) => show(e, <div><b>{r.label}</b>: {fmtV(r.value)}{r.note ? <><br /><span className="muted">{r.note}</span></> : null}</div>)} onMouseLeave={hide}>
      <div className="small" style={{ width: 170, textAlign: "right", fontWeight: r.bold ? 700 : 400 }}>{r.label}</div>
      <div style={{ flex: 1, background: "var(--surface-2)", borderRadius: 4, height }}>
        <div style={{ width: `${Math.max(1, (r.value / m) * 100)}%`, height: "100%", background: r.color, borderRadius: "0 4px 4px 0" }} />
      </div>
      <div className="num small" style={{ width: 64, fontWeight: r.bold ? 700 : 400 }}>{fmtV(r.value)}</div>
    </div>)}
    {tip}
  </div>;
}

const METHOD_LABEL: Record<string, string> = {
  nearest_offset: "Nearest offset well", base_rate: "Formation base rate", offset_evidence: "Offset evidence (Bayes)", ml: "ML model (HistGB)", blend: "NWIS blend (per-hazard CV)",
};

export default function Analytics() {
  const { fmName, meta } = useApp();
  const { can, authOn } = useAuth();
  const [d, setD] = useState<any>(null);
  const [rate, setRate] = useState(18);
  const [avoid, setAvoid] = useState(20);
  const load = () => { api("/api/analytics").then(setD); };
  useEffect(load, []);
  if (!d) return <div className="empty">Loading analytics…</div>;
  const inv = d.inventory;
  const rm = d.risk_metrics;
  const ex = d.extraction_eval;
  const oc = d.ocr_eval;
  const totalNpt = d.npt_by_hazard.reduce((s: number, r: any) => s + r.npt_hours, 0);
  const saving = (totalNpt / 24) * (avoid / 100) * rate;
  const hazards = Object.keys(HAZARD_COLOR);
  const maxCell = Math.max(1, ...d.npt_by_formation.flatMap((r: any) => hazards.map((h) => r[h] ?? 0)));
  return <div className="col">
    <div>
      <h2 className="view">Analytics &amp; model evidence</h2>
      <p className="lede">How much knowledge NWIS holds, where NPT comes from, and how well each model does. {meta.synthetic ? "All metrics are on the synthetic Upper-Assam dataset: they validate the pipeline mechanics and must be re-measured on OIL's own reports." : `Metrics on real public data: ${meta.ontology.region?.label}. Incidents come from wellbore history summaries, so they are under-reported compared with daily drilling reports.`}</p>
    </div>
    <JobsCard onFinished={load} />
    <div className="kpis">
      {[["Offset wells", inv.wells], ["Documents", inv.documents], ["Pages read", inv.pages], ["OCR pages", inv.ocr_pages], ["Events extracted", inv.events],
        ["Lessons learned", inv.lessons], ["Citations", inv.citations], ["Awaiting review", inv.review_open]].map(([k, v]) =>
        <div key={k as string} className="kpi"><div className="k">{k}</div><div className="v num">{fmt.n0(v as number)}</div></div>)}
    </div>
    <div className="grid2">
      <div className="card">
        <h3>Risk prediction skill <span className="sub">pooled ROC-AUC · leave-wells-out · {rm?.n_wells} wells</span></h3>
        {rm && <HBar max={1} fmtV={(v) => v.toFixed(3)} rows={["nearest_offset", "base_rate", "offset_evidence", "ml", "blend"].map((k) => ({
          label: METHOD_LABEL[k], value: rm.pooled[k].auc ?? 0, color: k === "ml" || k === "blend" ? "#3987e5" : "#9e9e9e", bold: k === "ml",
          note: `avg. precision ${rm.pooled[k].ap} · Brier ${rm.pooled[k].brier}`,
        }))} />}
        <div className="small muted" style={{ marginTop: 8 }}>"Nearest offset well" is how offsets are often consulted manually today. {rm?.protocol}</div>
        {rm && <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Hazard</th><th className="num">Pos.</th><th className="num">Nearest</th><th className="num">Base</th><th className="num">Evidence</th><th className="num">ML</th><th className="num">w(ML)</th></tr></thead>
          <tbody>{Object.entries(rm.per_hazard).map(([h, v]: any) => <tr key={h}><td><span className="swatch" style={{ background: HAZARD_COLOR[h] }} /> {HAZARD_SHORT[h]}</td>
            <td className="num">{v.positives}</td><td className="num">{v.nearest_offset.auc}</td><td className="num">{v.base_rate.auc}</td><td className="num">{v.offset_evidence.auc}</td>
            <td className="num"><b>{v.ml.auc}</b></td><td className="num">{v.blend_w_ml}</td></tr>)}</tbody></table>}
      </div>
      <div className="card">
        <h3>Document understanding <span className="sub">extraction vs ground truth</span></h3>
        {ex && <>
          <HBar max={1} fmtV={(v) => v.toFixed(2)} rows={[
            { label: "Held-out phrasing — F1", value: ex.held_out.f1, color: "#3987e5", bold: true, note: `P ${ex.held_out.precision} · R ${ex.held_out.recall} · ${ex.held_out.n_truth} true events in ${ex.held_out.n_docs} DDRs` },
            { label: "Held-out — precision", value: ex.held_out.precision, color: "#9e9e9e" },
            { label: "Held-out — recall", value: ex.held_out.recall, color: "#9e9e9e" },
            { label: "Formation accuracy", value: ex.held_out.formation_accuracy ?? 0, color: "#9e9e9e" },
            { label: "Mitigation Jaccard", value: ex.held_out.action_jaccard ?? 0, color: "#9e9e9e" },
            { label: "In-distribution F1", value: ex.in_distribution.f1, color: "#9e9e9e" },
          ]} />
          <div className="small muted" style={{ marginTop: 8 }}>{ex.held_out.note} Depth error {ex.held_out.depth_mae_m} m MAE. Missed examples: {ex.held_out.fn_examples.slice(0, 3).join("; ") || "none"}.</div>
        </>}
        {oc && <>
          <h3 style={{ marginTop: 14 }}>Scanned reports (OCR) <span className="sub">event recall · {oc.n_truth} true events in {oc.n_docs} completion reports</span></h3>
          <HBar max={1} fmtV={(v) => v.toFixed(2)} rows={[
            { label: "Text PDF (no OCR)", value: oc.text_pdf.recall, color: "#9e9e9e", note: `precision ${oc.text_pdf.precision}` },
            { label: "Standard scan (200 dpi)", value: oc.standard.recall, color: "#3987e5", bold: true,
              note: `precision ${oc.standard.precision} · ${Math.round(oc.standard.char_diff * 100)}% characters differ from the text layer` },
            { label: "Poor scan (150 dpi photocopy)", value: oc.poor.recall, color: "#3987e5",
              note: `precision ${oc.poor.precision} · ${Math.round(oc.poor.char_diff * 100)}% characters differ` },
          ]} />
          <div className="small muted" style={{ marginTop: 8 }}>The same reports scored as text PDFs and as scans, so the gap is what OCR loses. On-prem RapidOCR, lines recognised from padded upright crops, OCR digit confusions repaired. Missed on poor scans: {oc.poor.fn_examples.slice(0, 3).join("; ") || "none"}.</div>
        </>}
      </div>
    </div>
    <div className="grid2">
      <div className="card">
        <h3>NPT Pareto by hazard <span className="sub">{fmt.n0(totalNpt)} h across offsets</span></h3>
        <HBar fmtV={(v) => `${fmt.n0(v)} h`} rows={d.npt_by_hazard.map((r: any) => ({ label: r.label, value: r.npt_hours, color: HAZARD_COLOR[r.hazard], note: `${r.events} events` }))} />
        <div className="card" style={{ marginTop: 12, background: "var(--surface-2)" }}>
          <h3>What-if value <span className="sub">editable assumptions</span></h3>
          <div className="row wrap small">
            <label className="row" style={{ gap: 6 }}>Rig spread cost ₹ lakh/day <input type="number" value={rate} min={1} step={1} onChange={(e) => setRate(Number(e.target.value))} style={{ width: 70 }} /></label>
            <label className="row" style={{ gap: 6 }}>NPT avoided <input type="number" value={avoid} min={0} max={100} step={5} onChange={(e) => setAvoid(Number(e.target.value))} style={{ width: 60 }} />%</label>
          </div>
          <div style={{ marginTop: 6 }}>≈ <b className="num">₹ {saving.toFixed(0)} lakh</b> on the historical NPT of these {inv.wells} wells ({fmt.n0(totalNpt / 24)} rig-days × {avoid}% × ₹{rate} lakh/day). Assumptions are placeholders for OIL to set.</div>
        </div>
      </div>
      <div className="card">
        <h3>NPT hours by formation × hazard</h3>
        <table className="t"><thead><tr><th>Formation</th>{hazards.map((h) => <th key={h} className="num" title={HAZARD_SHORT[h]}><span className="swatch" style={{ background: HAZARD_COLOR[h] }} /></th>)}</tr></thead>
          <tbody>{d.npt_by_formation.map((r: any) => <tr key={r.formation}><td>{fmName(r.formation)}</td>
            {hazards.map((h) => <td key={h} className="num" style={{ background: r[h] ? riskColor((r[h] / maxCell) * 0.8) : undefined }}>{r[h] ? fmt.n0(r[h]) : ""}</td>)}</tr>)}</tbody></table>
        <div className="small muted" style={{ marginTop: 6 }}>Columns follow the hazard legend order: {hazards.map((h) => HAZARD_SHORT[h]).join(", ")}.</div>
        {rm?.calibration && <>
          <h3 style={{ marginTop: 14 }}>Calibration <span className="sub">predicted vs observed (pooled)</span></h3>
          <table className="t"><thead><tr><th>Predicted band</th><th className="num">Mean predicted</th><th className="num">Observed rate</th><th className="num">Bins</th></tr></thead>
            <tbody>{rm.calibration.map((c: any) => <tr key={c.bin}><td>{c.bin}</td><td className="num">{fmt.pct(c.predicted)}</td><td className="num">{fmt.pct(c.observed)}</td><td className="num">{fmt.n0(c.n)}</td></tr>)}</tbody></table>
        </>}
      </div>
    </div>
    {d.live_eval && <LiveEval ev={d.live_eval} />}
    <PublicEval ev={d.public_eval} />
    <div className="grid2">
      <div className="card">
        <h3>Decision log <span className="sub">append-only · SHA-256 hash chain</span></h3>
        <div className="row wrap" style={{ gap: 16 }}>
          <div className="kpi" style={{ flex: 1 }}><div className="k">Entries</div><div className="v num">{fmt.n0(d.audit.n)}</div><div className="d">{d.audit.sessions} live session(s)</div></div>
          <div className="kpi" style={{ flex: 1 }}><div className="k">Chain verification</div>
            <div className="v" style={{ color: d.audit.ok ? "var(--good-ink)" : "var(--bad-ink)", fontSize: 17 }}>{d.audit.ok ? "✔ intact" : `✖ broken at #${d.audit.first_bad_seq}`}</div>
            <div className="d mono" title={d.audit.head ?? ""}>{d.audit.head ? `head ${String(d.audit.head).slice(0, 16)}…` : "empty"}</div></div>
        </div>
        {Object.keys(d.audit.by_event).length > 0 && <div className="row wrap small" style={{ gap: 6, marginTop: 8 }}>
          {Object.entries(d.audit.by_event).map(([k, v]) => <span key={k} className="chip">{k.replace(/_/g, " ")} · {String(v)}</span>)}</div>}
        <div className="small muted" style={{ marginTop: 8 }}>Every alert shown, escalation, acknowledgement (with the person's name), clearance, engineer feedback, alarm-budget change, review decision and maintenance job is logged. Editing or deleting any past entry breaks the chain, so post-incident reviews and OISD audits can trust what the console showed and when.</div>
        <AuditBrowser admin={can("admin")} />
      </div>
    <div className="card">
      <h3>Alert feedback from engineers</h3>
      {d.feedback.length === 0 ? <div className="small muted">No feedback yet — use 👍 / 👎 on alerts in Live Ops. Feedback is stored per hazard to tune thresholds.</div> :
        <table className="t"><thead><tr><th>Hazard</th><th>Verdict</th><th className="num">Count</th></tr></thead>
          <tbody>{d.feedback.map((f: any, i: number) => <tr key={i}><td>{HAZARD_SHORT[f.hazard] ?? f.hazard}</td><td>{f.useful ? "useful" : "false alarm / not actionable"}</td><td className="num">{f.n}</td></tr>)}</tbody></table>}
    </div>
    </div>
    {can("admin") && authOn && <UsersCard />}
  </div>;
}

/** Replay evidence for the live alerting: alarm budget trade-off and automatic top picking. */
function LiveEval({ ev }: { ev: any }) {
  const { fmName } = useApp();
  const rows: any[] = ev.budget.rows;
  const stress = rows.filter((r) => r.nuisance > 0);
  const nEp = ev.budget.n_episodes;
  const dtw = ev.top_picks.dtw, ml = ev.top_picks.mudlogger;
  return <div className="grid2">
    <div className="card">
      <h3>Alarm budget <span className="sub">replay of the active well · {rows[0]?.hours} h of drilling</span></h3>
      <HBar fmtV={(v) => String(v)} rows={stress.map((r) => ({
        label: r.gated ? `budget ${r.budget_per_hour}/h` : "no budget (all alerts)", value: r.false_alarms,
        color: r.gated ? "#3987e5" : "#9e9e9e", bold: r.budget_per_hour === 1,
        note: `${r.detected}/${nEp} hazards caught · ${r.false_per_hour} false alarms per hour`,
      }))} />
      <div className="small muted" style={{ marginTop: 6 }}>False alarms under a nuisance stress test (pit transfers, flow surges, gas and stick-slip bursts injected into the replay).</div>
      <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Stream</th><th>Budget</th><th className="num">Hazards caught</th><th className="num">False alarms</th><th className="num">per hour</th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}><td>{r.nuisance > 0 ? "with nuisances" : "clean"}</td><td>{r.gated ? `${r.budget_per_hour}/h` : "off"}</td>
          <td className="num">{r.detected}/{nEp}</td><td className="num">{r.false_alarms}</td><td className="num">{r.false_per_hour}</td></tr>)}</tbody></table>
      <div className="small muted" style={{ marginTop: 6 }}>Critical alerts and alerts corroborated by an offset look-ahead zone always show; over budget, weaker signals are held in a visible digest. Remaining false alarms are mostly critical-level pit-transfer "losses": the budget never hides a critical signal.</div>
    </div>
    <div className="card">
      <h3>Automatic top picking <span className="sub">GR correlation (DTW) vs hidden truth</span></h3>
      <div className="row wrap" style={{ gap: 12 }}>
        <div className="kpi" style={{ flex: 1 }}><div className="k">DTW only: median |error|</div>
          <div className="v num">{fmt.n1(median(Object.values(dtw.per_formation).map((v: any) => Math.abs(v))))} m</div><div className="d">mean {dtw.mae_m} m · worst {dtw.max_m} m · {dtw.n} tops</div></div>
        <div className="kpi" style={{ flex: 1 }}><div className="k">Hazards still caught, DTW only</div><div className="v num">{dtw.episodes_detected}/{dtw.n_episodes}</div><div className="d">no mud-logger picks used</div></div>
      </div>
      <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Formation top</th><th className="num">DTW error</th><th className="num">Mud logger error</th></tr></thead>
        <tbody>{Object.entries(dtw.per_formation).map(([c, v]: any) => <tr key={c}><td>{fmName(c)}</td>
          <td className="num" style={{ color: Math.abs(v) > 15 ? "var(--bad-ink)" : undefined }}>{v > 0 ? "+" : ""}{v} m</td>
          <td className="num">{ml.per_formation[c] != null ? `${ml.per_formation[c]} m` : "–"}</td></tr>)}</tbody></table>
      <div className="small muted" style={{ marginTop: 6 }}>Default mode keeps the mud-logger pick in charge and runs DTW as an independent QC that flags disagreements. Large errors (red) come from sand streaks inside clays; the synthetic mud-logger picks are near-perfect by construction.</div>
    </div>
  </div>;
}

/** Real-data check on the public Equinor Volve reports: zero-shot transfer and the local-adaptation curve. */
function PublicEval({ ev }: { ev: any }) {
  if (!ev) return <div className="card">
    <h3>Real-data check: Equinor Volve (public) <span className="sub">not run yet</span></h3>
    <div className="small">Everything above uses synthetic Assam data. To score NWIS on real drilling text:</div>
    <ol className="small" style={{ margin: "6px 0 0 18px", padding: 0, lineHeight: 1.6 }}>
      <li>Download the Volve daily drilling report XML from Equinor's Volve data-sharing page (you accept the Equinor Open Data Licence there).</li>
      <li>Run <code>python -m nwis.cli validate-volve &lt;folder&gt;</code> in <code>backend/</code>.</li>
      <li>Reload this page: zero-shot and locally adapted F1 appear here.</li>
    </ol>
    <div className="small muted" style={{ marginTop: 6 }}>NWIS reads only the free text; the operator's own activity codes are the labels, so results are agreement with operator coding.</div>
  </div>;
  const z = ev.zero_shot;
  const curve: any[] = ev.adaptation ?? [];
  return <div className="grid2">
    <div className="card">
      <h3>Real-data check: Equinor Volve (public) <span className="sub">{ev.n_wells} wells · {fmt.n0(ev.n_reports)} report-days · free text only</span></h3>
      <HBar max={1} fmtV={(v) => v.toFixed(2)} rows={[
        { label: "Zero-shot F1 (Assam-trained)", value: z.f1, color: "#56626f", note: `P ${z.precision} · R ${z.recall} · ${z.n_truth} coded events` },
        ...curve.filter((c) => c.k_report_days > 0 && c.f1_mean != null).map((c) => ({
          label: `+ ${c.k_report_days} local report-days`, value: c.f1_mean, color: "#3987e5", bold: c === curve[curve.length - 1],
          note: `held-out wells, ${c.folds.length} folds` })),
      ]} />
      <div className="small muted" style={{ marginTop: 6 }}>Transfer from synthetic Assam reports to a different operator, basin and writing style, then with a few labelled local report-days from other wells. {ev.caveats?.[0]}</div>
    </div>
    <div className="card">
      <h3>Per hazard (zero-shot) <span className="sub">agreement with operator activity codes</span></h3>
      <table className="t"><thead><tr><th>Hazard</th><th className="num">Coded events</th><th className="num">Precision</th><th className="num">Recall</th><th className="num">F1</th></tr></thead>
        <tbody>{Object.entries(z.by_hazard as Record<string, any>).map(([h, v]) => <tr key={h}>
          <td><span className="swatch" style={{ background: HAZARD_COLOR[h] }} /> {HAZARD_SHORT[h] ?? h}</td>
          <td className="num">{v.support}</td><td className="num">{v.precision}</td><td className="num">{v.recall}</td><td className="num"><b>{v.f1}</b></td></tr>)}</tbody></table>
      <div className="small muted" style={{ marginTop: 6 }}>Depth MAE {z.depth_mae_m ?? "–"} m.
        {ev.unmapped_interruption_codes?.length > 0 && <> Unmapped interruption codes: {ev.unmapped_interruption_codes.slice(0, 4).join("; ")}.</>}</div>
    </div>
  </div>;
}

function median(v: number[]): number | null {
  if (!v.length) return null;
  const s = [...v].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

/** Browse the decision log (newest first); admins can re-verify the whole hash chain on demand. */
function AuditBrowser({ admin }: { admin: boolean }) {
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState("");
  const [event, setEvent] = useState("");
  const [rows, setRows] = useState<any[] | null>(null);
  const [check, setCheck] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    setErr(null);
    api(`/api/audit?limit=500${session.trim() ? `&session_id=${encodeURIComponent(session.trim())}` : ""}`)
      .then((r) => setRows(r.rows)).catch((e) => setErr(e.message));
  };
  useEffect(() => { if (open) load(); }, [open]);
  const verify = () => { setCheck(null); api("/api/audit/verify").then(setCheck).catch((e) => setErr(e.message)); };
  const shown = (rows ?? []).filter((r) => !event || r.event === event);
  const events = [...new Set((rows ?? []).map((r) => r.event))].sort();
  return <div style={{ marginTop: 10 }}>
    <div className="row wrap" style={{ gap: 6 }}>
      <button className="btn sm" onClick={() => setOpen(!open)}>{open ? "Hide log" : "Browse log"}</button>
      {admin && <button className="btn sm" onClick={verify}>Verify hash chain</button>}
      {check && <span className="small" style={{ color: check.ok ? "var(--good-ink)" : "var(--bad-ink)" }}>
        {check.ok ? `✔ all ${fmt.n0(check.n)} entries verified` : `✖ chain broken at entry #${check.first_bad_seq}`}</span>}
    </div>
    {err && <div className="banner small" style={{ marginTop: 6 }}>{err}</div>}
    {open && <div className="col" style={{ gap: 6, marginTop: 8 }}>
      <div className="row wrap small" style={{ gap: 6 }}>
        <input type="text" placeholder="session id (optional)" value={session} onChange={(e) => setSession(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()} style={{ width: 200 }} />
        <button className="btn sm" onClick={load}>Load</button>
        <select value={event} onChange={(e) => setEvent(e.target.value)}>
          <option value="">all events</option>{events.map((e) => <option key={e} value={e}>{e.replace(/_/g, " ")}</option>)}
        </select>
        <span className="muted">{rows ? `${shown.length} of the latest ${rows.length} entries` : "loading…"}</span>
      </div>
      <div className="scroll" style={{ maxHeight: 320 }}>
        <table className="t"><thead><tr><th className="num">#</th><th>Time</th><th>Event</th><th>Hazard</th><th>Level</th><th>Who</th><th>Well</th><th className="num">MD</th><th>Details</th></tr></thead>
          <tbody>{shown.map((r) => <tr key={r.seq}><td className="num">{r.seq}</td><td className="small">{String(r.ts_wall).replace("T", " ")}</td>
            <td>{String(r.event).replace(/_/g, " ")}</td><td>{r.hazard ? HAZARD_SHORT[r.hazard] ?? r.hazard : ""}</td><td>{r.level ?? ""}</td>
            <td>{r.actor}</td><td>{r.well_id ?? ""}</td><td className="num">{r.md != null ? fmt.n0(r.md) : ""}</td>
            <td className="small muted" title={JSON.stringify(r.payload)} style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {Object.entries(r.payload ?? {}).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")}</td></tr>)}</tbody></table>
      </div>
    </div>}
  </div>;
}

/** Admin only: who can sign in, and with which role. */
function UsersCard() {
  const [users, setUsers] = useState<User[]>([]);
  const [f, setF] = useState({ username: "", display_name: "", role: "field" as Role, password: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api<User[]>("/api/users").then(setUsers).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  const add = async () => {
    setMsg(null);
    try {
      const u = await api<User>("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
      setMsg(`Added ${u.username} (${roleLabel(u.role)})`);
      setF({ username: "", display_name: "", role: "field", password: "" });
      load();
    } catch (e: any) { setMsg(e.message); }
  };
  return <div className="card">
    <h3>Users &amp; roles <span className="sub">field: live, map, risk, knowledge, memos · office: + ingestion, review, what-if, analytics · admin: + users, log verification</span></h3>
    <table className="t"><thead><tr><th>Username</th><th>Name</th><th>Role</th></tr></thead>
      <tbody>{users.map((u) => <tr key={u.username}><td>{u.username}</td><td>{u.display_name}</td><td>{roleLabel(u.role)}</td></tr>)}</tbody></table>
    <div className="row wrap" style={{ marginTop: 8, gap: 6 }}>
      <input type="text" placeholder="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} style={{ width: 120 }} />
      <input type="text" placeholder="display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} style={{ width: 180 }} />
      <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
        {(["field", "office", "admin"] as Role[]).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
      </select>
      <input type="password" placeholder="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} style={{ width: 120 }} />
      <button className="btn sm primary" disabled={!f.username || f.password.length < 4} onClick={add}>Add user</button>
      {msg && <span className="small muted">{msg}</span>}
    </div>
  </div>;
}
