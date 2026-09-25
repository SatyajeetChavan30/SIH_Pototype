import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import MemoCard from "../components/MemoCard";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";

const ROLE_COLOR: Record<string, string> = {
  event: "#5fa8ff", action: "#57d36a", outcome: "#b9c4cf", negated: "#ff8080", hypothetical: "#fab219", lesson: "#9085e9",
  continuation: "#8fa3b8", top: "#8fa3b8",
};
const ROLE_HELP: Record<string, string> = {
  event: "hazard occurred → structured event", action: "mitigation linked to the open event", outcome: "success/failure of the last action",
  negated: "hazard term negated (e.g. 'no losses') → ignored", hypothetical: "precaution/plan, not an occurrence → ignored",
  lesson: "lesson learned → knowledge base", continuation: "same event continued",
};

export default function Ingestion() {
  const { openCitation, fmName, meta } = useApp();
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [review, setReview] = useState<any[]>([]);
  const [docs, setDocs] = useState<any[]>([]);

  const loadSide = () => {
    api("/api/review").then(setReview);
    api("/api/documents").then(setDocs);
  };
  useEffect(loadSide, []);

  const run = async (p: Promise<any>, label: string) => {
    setBusy(label); setErr(null);
    try { setRes(await p); loadSide(); } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const sample = (kind: string, label: string) => run(api(`/api/ingest/sample?kind=${kind}`, { method: "POST" }), label);
  const upload = (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    run(api("/api/ingest", { method: "POST", body: fd }), f.name);
  };
  const act = async (id: string, action: string) => {
    await api(`/api/review/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    loadSide();
  };

  return <div className="col">
    <div>
      <h2 className="view">Document Ingestion — NLP + OCR pipeline</h2>
      <p className="lede">Drop a DDR/WCR PDF (text or scanned) or WITSML drillReport XML. NWIS extracts events with depth, formation, severity, actions and outcomes, handles negation ("no losses observed") and precautions ("LCM kept ready"), normalises units (m/ft, ppg/SG, bbl/hr, m³/hr), merges with existing knowledge, and queues low-confidence items for human review. Everything stays on-prem.</p>
    </div>
    <div className="grid2">
      <div className="col">
      <div className="card col">
        <h3>Ingest a document</h3>
        <label className="card gridbg" style={{ textAlign: "center", padding: 26, cursor: "pointer", borderStyle: "dashed" }}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
          <input type="file" accept=".pdf,.xml" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <div style={{ fontSize: 22 }}>⇪</div>Drop PDF / XML here or click to browse
        </label>
        <div className="row wrap">
          <span className="small muted">Or try a sample:</span>
          <button className="btn sm" disabled={!!busy} onClick={() => sample("ddr", "today's DDR")}>Today's DDR · {meta.active_well}</button>
          <button className="btn sm" disabled={!!busy} onClick={() => sample("scanned", "scanned WCR")}>Scanned legacy WCR (OCR)</button>
          <button className="btn sm" disabled={!!busy} onClick={() => sample("witsml", "WITSML XML")}>WITSML drillReport XML</button>
        </div>
        {busy && <div className="small">Processing {busy}… {busy.includes("scan") ? "(OCR takes ~10 s per page)" : ""}</div>}
        {err && <div className="banner">{err}</div>}
      </div>
      <MemoCard onResult={(r) => { setRes(r); loadSide(); }} />
      </div>
      <div className="card">
        <h3>Human review queue <span className="sub">{review.length} open · approvals become training data (active learning)</span></h3>
        <div className="scroll" style={{ maxHeight: 560 }}>
          {review.length === 0 && <div className="empty">Nothing waiting for review.</div>}
          {review.map((r) => <div key={r.id} className="row" style={{ borderBottom: "1px solid var(--line)", padding: "6px 0", alignItems: "flex-start" }}>
            {r.kind === "lesson" ? <div style={{ flex: 1 }}>
              <div className="small"><span className="pill" style={{ color: "#9085e9" }}>EXPERT LESSON</span> <b>{r.payload.author ?? "unknown author"}</b>
                {r.payload.well_id ? ` · ${r.payload.well_id}` : ""}{r.payload.formation ? ` · ${fmName(r.payload.formation)}` : ""}</div>
              <div className="small quote" style={{ marginTop: 4 }}>{r.payload.text}</div>
              <div className="small muted">{r.reason}</div>
            </div> : <div style={{ flex: 1 }}>
              <div className="small"><span className="swatch" style={{ background: HAZARD_COLOR[r.payload.hazard] }} /> <b>{r.payload.id}</b> · {r.payload.summary}</div>
              <div className="small muted">confidence {fmt.pct(r.confidence)} · {r.reason}</div>
              {r.payload.citations?.[0] && <div className="small cite" onClick={() => openCitation(r.payload.citations[0])}>“{r.payload.citations[0].text.slice(0, 110)}”</div>}
            </div>}
            <button className="btn sm" onClick={() => act(r.id, "approve")}>✔ Approve</button>
            <button className="btn sm" onClick={() => act(r.id, "reject")}>✖ Reject</button>
          </div>)}
        </div>
      </div>
    </div>

    {res && <div className="card col">
      <h3>Pipeline trace <span className="sub">{res.kind}{res.author ? ` by ${res.author}` : ""} · well {res.well_id ?? "unknown"} · {res.pages?.length ?? 0} page(s)</span></h3>
      {res.transcript && <div className="small"><b>Transcript</b> ({res.transcript.language}): <span className="ink2">{res.transcript.original}</span>
        {res.transcript.language !== "en" && <div className="muted">English (used for extraction): {res.transcript.english}</div>}</div>}
      {res.kind === "MEMO" && <div className="banner small">Expert memo: every event and lesson below is held for peer review (see the queue) before it can influence alerts, rankings or briefs.</div>}
      {res.warnings?.map((w: string, i: number) => <div key={i} className="banner small">{w}</div>)}
      <div className="row wrap">
        {(res.pages ?? []).map((p: any) => <span key={p.page} className="chip">p.{p.page}: {p.ocr ? `OCR ${fmt.pct(p.ocr_conf)}` : p.needs_ocr ? "needs OCR" : "text layer"} · {p.chars} chars</span>)}
      </div>
      <div className="grid2">
        <div>
          <h4 className="small muted">EXTRACTED EVENTS ({res.events.length})</h4>
          {res.events.length === 0 && <div className="small muted">No hazard events in this document.</div>}
          {res.events.map((e: any) => <div key={e.id} className="card" style={{ marginBottom: 8, padding: "8px 10px" }}>
            <div className="row small" style={{ gap: 6 }}><span className="swatch" style={{ background: HAZARD_COLOR[e.hazard] }} /><b>{HAZARD_SHORT[e.hazard]}</b> · {e.id}
              <span className="pill" style={{ marginLeft: "auto", color: e.status === "auto" ? "#57d36a" : "#fab219" }}>{e.status === "auto" ? "AUTO-ACCEPTED" : "SENT TO REVIEW"}</span></div>
            <div className="small">{e.summary}</div>
            <div className="small muted">depth {fmt.m(e.md)} ({e.depth_source}) · formation {fmName(e.formation)} ({e.formation_source}) · confidence {fmt.pct(e.confidence)}</div>
            {e.actions.length > 0 && <div className="small">Actions: {e.actions.map((a: any, i: number) => <span key={i} style={{ color: a.success ? "#57d36a" : "#ff8080" }}>{a.code}{a.success ? " ✔" : " ✖"}{i < e.actions.length - 1 ? " → " : ""}</span>)}</div>}
            {e.citations?.[0] && <span className="small cite" onClick={() => openCitation(e.citations[0])}>view evidence (p.{e.citations[0].page_no})</span>}
          </div>)}
          {res.lessons.length > 0 && <><h4 className="small muted">LESSONS ({res.lessons.length})</h4>{res.lessons.map((l: any) => <div key={l.id} className="quote small" style={{ marginBottom: 4 }}>{l.text}</div>)}</>}
          {res.tops?.length > 0 && <><h4 className="small muted">FORMATION TOPS PARSED ({res.tops.length})</h4><div className="small">{res.tops.map((t: any) => `${fmName(t.formation)} ${fmt.n0(t.md)} m`).join(" · ")}</div></>}
        </div>
        <div>
          <h4 className="small muted">SENTENCE-LEVEL NLP ({res.sentences.length} informative sentences)</h4>
          <div className="row wrap small" style={{ gap: 6, marginBottom: 6 }}>{Object.entries(ROLE_HELP).map(([k, v]) => <span key={k} title={v} className="chip" style={{ color: ROLE_COLOR[k] }}>{k}</span>)}</div>
          <div className="scroll" style={{ maxHeight: 480 }}>
            {res.sentences.map((s: any, i: number) => <div key={i} style={{ borderBottom: "1px solid var(--line)", padding: "5px 0" }}>
              <div className="row small" style={{ gap: 6 }}>
                <span className="pill" style={{ color: ROLE_COLOR[s.role] ?? "var(--ink-3)" }}>{s.role}</span>
                {s.hazard && <span><span className="swatch" style={{ background: HAZARD_COLOR[s.hazard] }} /> {HAZARD_SHORT[s.hazard]}</span>}
                {s.negated?.length > 0 && <span style={{ color: "#ff8080" }}>negated: {s.negated.join(",")}</span>}
                {s.hypothetical && <span style={{ color: "#fab219" }}>hypothetical</span>}
                {s.mitigations?.length > 0 && <span className="muted">{s.mitigations.join(", ")}</span>}
                {s.outcome && <span style={{ color: s.outcome === "success" ? "#57d36a" : "#ff8080" }}>{s.outcome}</span>}
                {s.depths?.length > 0 && <span className="muted num">{s.depths.map((d: number) => `${Math.round(d)} m`).join(", ")}</span>}
                {s.clf_top && <span className="muted" style={{ marginLeft: "auto" }}>ML: {s.clf_top} {fmt.pct(s.clf_p)}</span>}
              </div>
              <div className="small ink2">{s.text}</div>
            </div>)}
          </div>
        </div>
      </div>
    </div>}

    <div className="card">
      <h3>Document library <span className="sub">{docs.length} most recent</span></h3>
      <div className="scroll" style={{ maxHeight: 260 }}>
        <table className="t"><thead><tr><th>Title</th><th>Well</th><th>Kind</th><th className="num">Pages</th><th className="num">OCR pages</th><th></th></tr></thead>
          <tbody>{docs.slice(0, 120).map((d) => <tr key={d.id}><td>{d.title}</td><td>{d.well_id}</td><td>{d.kind}</td><td className="num">{d.pages}</td><td className="num">{d.ocr_pages}</td>
            <td>{d.kind === "MEMO" ? <span className="small cite" onClick={() => openCitation({ doc_id: d.id, page_no: 1, start: 0, end: 0, text: "", title: d.title })}>read</span>
              : <a className="small" href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer">open</a>}</td></tr>)}</tbody></table>
      </div>
    </div>
  </div>;
}
