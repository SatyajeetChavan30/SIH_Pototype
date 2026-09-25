import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from "d3-force";
import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import { useApp } from "../context";
import { useTip } from "../components/Tip";
import BriefModal from "../components/BriefModal";
import { getActor } from "../live";
import type { Brief } from "../types";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";

const EXAMPLES = [
  "losses in Tipam within 5 km after 2012",
  "what worked for losses in fractured Sylhet limestone",
  "kick in lower Barail below 3000 m",
  "bit balling Girujan clay",
  "stuck pipe differential sticking",
  "poor cement bond 9-5/8 casing",
];

function Highlighted({ text, hl }: { text: string; hl: number[][] }) {
  if (!hl?.length) return <>{text}</>;
  const merged: number[][] = [];
  for (const [a, b] of [...hl].sort((x, y) => x[0] - y[0])) {
    const l = merged[merged.length - 1];
    if (l && a <= l[1]) l[1] = Math.max(l[1], b); else merged.push([a, b]);
  }
  const out: React.ReactNode[] = [];
  let p = 0;
  merged.forEach(([a, b], i) => { out.push(text.slice(p, a)); out.push(<mark key={i}>{text.slice(a, b)}</mark>); p = b; });
  out.push(text.slice(p));
  return <>{out}</>;
}

export default function Knowledge() {
  const { params } = useApp();
  const [tab, setTab] = useState<"search" | "ask" | "graph">(params.tab as any || "search");
  return <div className="col">
    <div>
      <h2 className="view">Knowledge — institutional memory</h2>
      <p className="lede">Every DDR, WCR and legacy scan, structured into events, lessons and citations. Plain-English questions are parsed into filters (formation, hazard, radius, depth, year) with no LLM required; answers are extractive and every statement links to its source page.</p>
    </div>
    <div className="seg" style={{ alignSelf: "flex-start" }}>
      <button className={tab === "search" ? "on" : ""} onClick={() => setTab("search")}>Search</button>
      <button className={tab === "ask" ? "on" : ""} onClick={() => setTab("ask")}>Ask NWIS</button>
      <button className={tab === "graph" ? "on" : ""} onClick={() => setTab("graph")}>Knowledge graph</button>
    </div>
    {tab === "search" && <Search initial={params.q} />}
    {tab === "ask" && <Ask />}
    {tab === "graph" && <Graph />}
  </div>;
}

function Search({ initial }: { initial?: string }) {
  const { openCitation, fmName } = useApp();
  const [q, setQ] = useState(initial ?? EXAMPLES[0]);
  const [types, setTypes] = useState(["event", "lesson", "passage"]);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [aar, setAar] = useState<string | null>(null);
  const run = async (qq = q) => {
    setBusy(true);
    try { setRes(await api(`/api/search?${qs({ q: qq, types: types.join(","), limit: 30 })}`)); } finally { setBusy(false); }
  };
  useEffect(() => { run(); }, [types]);
  return <div className="col">
    <form className="row" onSubmit={(e) => { e.preventDefault(); run(); }}>
      <input type="text" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} placeholder="e.g. losses in Tipam within 5 km after 2012" aria-label="Search query" />
      <button className="btn primary" disabled={busy}>{busy ? "…" : "Search"}</button>
    </form>
    <div className="row wrap small" style={{ gap: 6 }}>
      <span className="muted">Try:</span>
      {EXAMPLES.map((e) => <span key={e} className="chip click" onClick={() => { setQ(e); run(e); }}>{e}</span>)}
    </div>
    {res && <div className="row wrap" style={{ gap: 6 }}>
      <span className="small muted">Understood as:</span>
      {res.query.chips.length === 0 && <span className="small muted">free text</span>}
      {res.query.chips.map((c: any, i: number) => <span key={i} className="chip">{c.kind === "hazard" && <span className="swatch" style={{ background: HAZARD_COLOR[c.value] }} />}{c.label}</span>)}
      <span className="small muted">· {res.total} matching records</span>
      <span style={{ marginLeft: "auto" }} className="row small">{["event", "lesson", "passage"].map((t) =>
        <label key={t} className="row" style={{ gap: 4 }}><input type="checkbox" checked={types.includes(t)} onChange={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])} />{t}s</label>)}</span>
    </div>}
    {res && <div className="col" style={{ gap: 8 }}>
      {res.results.map((r: any) => <div key={r.id} className="card" style={{ padding: "9px 12px" }}>
        <div className="row small" style={{ gap: 8 }}>
          <span className="pill" style={{ color: r.type === "event" ? "var(--info-ink)" : r.type === "lesson" ? "var(--good-ink)" : "var(--ink-3)" }}>{r.type.toUpperCase()}</span>
          <b>{r.well_id}</b>
          {r.formation && <span className="muted">{fmName(r.formation)}</span>}
          {r.md != null && <span className="muted num">{fmt.m(r.md)}</span>}
          {r.hazards.map((h: string) => <span key={h} className="chip" style={{ padding: "0 6px" }}><span className="swatch" style={{ background: HAZARD_COLOR[h] }} />{HAZARD_SHORT[h]}</span>)}
          <span className="muted" style={{ marginLeft: "auto" }} title="lexical BM25 / semantic LSA similarity">bm25 {r.lexical} · sem {r.semantic}</span>
        </div>
        <div style={{ marginTop: 4 }}><Highlighted text={r.text} hl={r.highlights} /></div>
        <div className="row small" style={{ marginTop: 3 }}>
          {r.citation && <span className="cite" onClick={() => openCitation(r.citation)}>Source: {r.citation.title ?? r.citation.doc_id}, page {r.citation.page_no}</span>}
          {r.type === "event" && <button className="btn sm ghost" style={{ marginLeft: "auto" }} onClick={() => setAar(r.id)}
            title="Draft a cited after-action review and turn it into a lesson">📋 After-action review</button>}
        </div>
      </div>)}
      {res.results.length === 0 && <div className="empty">No matches. Remove a filter chip term from the query or widen the radius.</div>}
    </div>}
    {aar && <AarModal eventId={aar} onClose={() => setAar(null)} />}
  </div>;
}

/** After-action review: drafted from the event's reports and offset outcomes, approved into a lesson by a person. */
function AarModal({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    api<Brief>(`/api/aar/${encodeURIComponent(eventId)}`).then((b) => { setBrief(b); setText(b.draft_lesson ?? ""); setDone(b.status === "approved"); })
      .catch((e) => setErr(e.message));
  }, [eventId]);
  const approve = async () => {
    setErr(null);
    try {
      await api(`/api/aar/${encodeURIComponent(eventId)}/approve`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, reviewer: getActor() }) });
      setDone(true);
    } catch (e: any) { setErr(e.message); }
  };
  if (err && !brief) return <div className="modal-bg" onClick={onClose}><div className="modal"><div className="bd empty">{err}</div></div></div>;
  if (!brief) return <div className="modal-bg"><div className="modal"><div className="bd empty">Drafting after-action review…</div></div></div>;
  return <BriefModal brief={brief} onClose={onClose} subtitle="After-action review draft" footer={<section>
    <h4 className="brief-h">Lesson to publish</h4>
    <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} disabled={done}
      style={{ width: "100%", background: "var(--surface-2)", border: "1px solid var(--line-strong)", borderRadius: 8, padding: 8 }} />
    <div className="row" style={{ marginTop: 6 }}>
      {done ? <span className="small" style={{ color: "var(--good-ink)" }}>✔ Approved: this lesson now appears in search, Ask NWIS, hazard briefs and live recommendations.</span>
        : <button className="btn sm primary" disabled={text.trim().length < 20} onClick={approve}>Approve as {getActor()} and publish lesson</button>}
      {err && <span className="small" style={{ color: "var(--bad-ink)" }}>{err}</span>}
    </div>
  </section>} />;
}

function Ask() {
  const { openCitation } = useApp();
  const [q, setQ] = useState("What problems did offsets within 5 km have in Tipam, and what worked?");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); try { setRes(await api(`/api/ask?${qs({ q })}`)); } finally { setBusy(false); } };
  useEffect(() => { run(); }, []);
  const renderAnswer = (text: string) => text.split(/(\[\d+\])/).map((part, i) => {
    const m = part.match(/\[(\d+)\]/);
    if (!m) return <span key={i}>{part}</span>;
    const c = res.citations.find((x: any) => x.n === Number(m[1]));
    return <span key={i} className="cite" onClick={() => c && openCitation(c)}>{part}</span>;
  });
  return <div className="col">
    <form className="row" onSubmit={(e) => { e.preventDefault(); run(); }}>
      <input type="text" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} aria-label="Question" />
      <button className="btn primary" disabled={busy}>{busy ? "…" : "Ask"}</button>
    </form>
    <div className="row wrap small" style={{ gap: 6 }}>
      {["Kicks in Barail within 8 km — what MW did offsets need?", "What worked for losses in Sylhet?", "Bit balling in Girujan clay after 2010", "Stuck pipe in Tipam near NDH-09"].map((e) =>
        <span key={e} className="chip click" onClick={() => setQ(e)}>{e}</span>)}
    </div>
    {res && <div className="card">
      <h3>Answer <span className="sub">{res.mode === "llm" ? "LLM-phrased, citation-guarded" : "grounded extractive"} · {res.stats.n_events} events · {res.stats.n_wells_exposed} wells in scope</span></h3>
      <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>{res.parsed.chips.map((c: any, i: number) => <span key={i} className="chip">{c.label}</span>)}</div>
      {res.mode === "llm" ? <div style={{ lineHeight: 1.65 }}>{renderAnswer(res.answer)}</div> :
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>{res.facts.map((f: string, i: number) => <li key={i}>{renderAnswer(f)}</li>)}</ul>}
      {res.citations.length > 0 && <div style={{ marginTop: 10 }}>
        <h4 className="small muted" style={{ margin: "6px 0" }}>SOURCES</h4>
        {res.citations.map((c: any) => <div key={c.n} className="small"><span className="cite" onClick={() => openCitation(c)}>[{c.n}] {c.title ?? c.doc_id}, p.{c.page_no}</span> — <i className="muted">“{c.text?.slice(0, 140)}”</i></div>)}
      </div>}
    </div>}
  </div>;
}

function Graph() {
  const { meta, fmName, fm } = useApp();
  const [formation, setFormation] = useState("TIPAM");
  const [hazard, setHazard] = useState("");
  const [g, setG] = useState<any>(null);
  const [tip, show, hide] = useTip();
  useEffect(() => { api(`/api/kg?${qs({ formation: formation || undefined, hazard: hazard || undefined })}`).then(setG); }, [formation, hazard]);
  const W = 900, H = 560;
  const layout = useMemo(() => {
    if (!g) return null;
    const nodes = g.nodes.map((n: any) => ({ ...n }));
    const links = g.links.map((l: any) => ({ ...l }));
    const xTarget: Record<string, number> = { well: 70, formation: 230, hazard: 390, cause: 550, mitigation: 700 };
    const sim = forceSimulation(nodes).force("link", forceLink(links).id((d: any) => d.id).distance(90).strength(0.4))
      .force("charge", forceManyBody().strength(-160)).force("center", forceCenter(W / 2, H / 2)).force("collide", forceCollide(22)).stop();
    for (let i = 0; i < 260; i++) {
      sim.tick();
      nodes.forEach((n: any) => { n.x += ((xTarget[n.type] ?? W / 2) - n.x) * 0.12; n.y = Math.max(20, Math.min(H - 20, n.y)); });
    }
    return { nodes, links };
  }, [g]);
  const color = (n: any) => n.type === "hazard" ? HAZARD_COLOR[n.id.split(":")[1]] : n.type === "formation" ? fm(n.id.split(":")[1])?.color : n.type === "well" ? "var(--ink)" : n.type === "cause" ? "var(--ink-3)" : "var(--ink-2)";
  return <div className="card">
    <div className="row wrap" style={{ marginBottom: 8 }}>
      <label className="row small" style={{ gap: 6 }}>Formation <select value={formation} onChange={(e) => setFormation(e.target.value)}>
        <option value="">All</option>{meta.formation_order.slice(1, 9).map((f) => <option key={f} value={f}>{fmName(f)}</option>)}</select></label>
      <label className="row small" style={{ gap: 6 }}>Hazard <select value={hazard} onChange={(e) => setHazard(e.target.value)}>
        <option value="">All</option>{[...meta.ribbon_hazards, "FISH"].map((h) => <option key={h} value={h}>{HAZARD_SHORT[h]}</option>)}</select></label>
      <span className="small muted">Wells → Formation → Hazard → Cause → Mitigation (edge label = cured/attempts). {g?.n_events ?? 0} events.</span>
    </div>
    {layout && <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Knowledge graph">
      {["Wells", "Formation", "Hazard", "Cause", "Mitigation (cured/tried)"].map((t, i) => <text key={t} x={[70, 230, 390, 550, 740][i]} y={12} fontSize={11} fill="var(--ink-3)" textAnchor="middle">{t}</text>)}
      {layout.links.map((l: any, i: number) => {
        const ok = l.rel === "treated_with";
        const ratio = ok ? l.success / l.count : 1;
        return <g key={i}>
          <line x1={l.source.x} y1={l.source.y} x2={l.target.x} y2={l.target.y} stroke={ok ? (ratio >= 0.55 ? "var(--good-ink)" : ratio <= 0.3 ? "var(--bad-ink)" : "var(--ink-3)") : "var(--line-strong)"}
            strokeWidth={Math.min(1 + l.count * 0.6, 5)} opacity={0.7} />
          {ok && <text x={(l.source.x + l.target.x) / 2} y={(l.source.y + l.target.y) / 2 - 3} fontSize={9.5} fill="var(--ink-2)" textAnchor="middle">{l.success}/{l.count}</text>}
        </g>;
      })}
      {layout.nodes.map((n: any) => <g key={n.id} transform={`translate(${n.x},${n.y})`} onMouseMove={(e) => show(e, <div><b>{n.label}</b><br /><span className="muted">{n.type} · {n.count} events</span></div>)} onMouseLeave={hide}>
        {n.type === "cause" ? <rect x={-7} y={-7} width={14} height={14} transform="rotate(45)" fill={color(n)} /> :
          n.type === "mitigation" ? <rect x={-8} y={-8} width={16} height={16} rx={3} fill="var(--surface-1)" stroke={color(n)} strokeWidth={2} /> :
            <circle r={n.type === "well" ? 5 : 10 + Math.min(n.count, 10)} fill={color(n)} stroke="var(--page)" strokeWidth={2} />}
        <text x={n.type === "mitigation" ? 12 : n.type === "well" ? -8 : 0} y={n.type === "mitigation" || n.type === "well" ? 4 : -14} fontSize={10.5}
          textAnchor={n.type === "mitigation" ? "start" : n.type === "well" ? "end" : "middle"} fill="var(--ink)">{n.label.length > 30 ? n.label.slice(0, 29) + "…" : n.label}</text>
      </g>)}
    </svg>}
    <div className="row wrap small" style={{ gap: 12 }}><span style={{ color: "var(--good-ink)" }}>━ usually cured (≥55%)</span><span style={{ color: "var(--bad-ink)" }}>━ rarely cured (≤30%)</span><span className="muted">━ mixed</span></div>
    {tip}
  </div>;
}
