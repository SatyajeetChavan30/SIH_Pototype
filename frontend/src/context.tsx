import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Citation, Formation, HazardDef, Meta } from "./types";

export type View = "live" | "map" | "correlation" | "knowledge" | "planning" | "ingest" | "analytics" | "system" | "rig";

interface AppCtx {
  meta: Meta;
  fm: (code?: string | null) => Formation | undefined;
  fmName: (code?: string | null) => string;
  hz: (code?: string | null) => HazardDef | undefined;
  view: View;
  params: Record<string, string>;
  go: (v: View, params?: Record<string, string>) => void;
  openCitation: (c: Citation) => void;
  /** Result of the last upload made outside the Ingestion view, shown there as the pipeline trace. */
  lastIngest: any | null;
  setLastIngest: (r: any | null) => void;
  /** Re-read /api/meta after a setting changed on the server (live feed, map tiles, LLM). */
  refreshMeta: () => Promise<void>;
}

const Ctx = createContext<AppCtx | null>(null);
export const useApp = () => useContext(Ctx)!;

function parseHash(): [View, Record<string, string>] {
  const h = location.hash.replace(/^#\/?/, "");
  const [v, q] = h.split("?");
  const params: Record<string, string> = {};
  new URLSearchParams(q || "").forEach((val, k) => { params[k] = val; });
  const views: View[] = ["live", "map", "correlation", "knowledge", "planning", "ingest", "analytics", "system", "rig"];
  return [(views.includes(v as View) ? v : "live") as View, params];
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [[view, params], setRoute] = useState(parseHash());
  const [cite, setCite] = useState<Citation | null>(null);
  const [lastIngest, setLastIngest] = useState<any | null>(null);

  const loadMeta = () => api<Meta>("/api/meta").then((m) => { setMeta(m); try { localStorage.setItem("stratasense.meta", JSON.stringify(m)); } catch { /* best-effort */ } });
  useEffect(() => {
    // cache /api/meta so the rig-site view still opens when the link to the server is down
    loadMeta()
      .catch((e) => {
        let cached: Meta | null = null;
        try { cached = JSON.parse(localStorage.getItem("stratasense.meta") || "null"); } catch { cached = null; }
        if (cached) setMeta(cached); else setErr(String(e.message || e));
      });
    const f = () => setRoute(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);

  if (err) return <div className="empty" style={{ padding: 40 }}>
    <h2>StrataSense server not reachable</h2><p>{err}</p>
    <p className="muted">If the knowledge base is still being built, this page continues once it is ready.</p>
    <button className="btn" onClick={() => location.reload()}>Try again</button>
  </div>;
  if (!meta) return <div className="empty" style={{ padding: 40 }}>Loading StrataSense…</div>;

  const fmMap = Object.fromEntries(meta.ontology.formations.map((f) => [f.code, f]));
  const hzMap = Object.fromEntries(meta.ontology.hazards.map((h) => [h.code, h]));
  const value: AppCtx = {
    meta, view, params,
    fm: (c) => (c ? fmMap[c] : undefined),
    fmName: (c) => (c ? fmMap[c]?.name ?? c : "–"),
    hz: (c) => (c ? hzMap[c] : undefined),
    go: (v, p) => { location.hash = `/${v}${p ? "?" + new URLSearchParams(p).toString() : ""}`; },
    openCitation: (c) => setCite(c),
    lastIngest, setLastIngest,
    refreshMeta: () => loadMeta().catch(() => undefined),
  };
  return <Ctx.Provider value={value}>
    {children}
    {cite && <DocViewer c={cite} onClose={() => setCite(null)} />}
  </Ctx.Provider>;
}

function DocViewer({ c, onClose }: { c: Citation; onClose: () => void }) {
  const [page, setPage] = useState<{ document: any; page: { text: string; ocr: number; ocr_conf: number | null } } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api(`/api/documents/${c.doc_id}/page/${c.page_no}`).then(setPage).catch((e) => setErr(e.message));
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [c]);
  useEffect(() => { document.getElementById("cite-mark")?.scrollIntoView({ block: "center" }); }, [page]);
  const text = page?.page.text ?? "";
  let start = c.start, end = c.end;
  if (text && (start < 0 || end > text.length || text.slice(start, end).replace(/\s+/g, " ").trim() !== c.text?.replace(/\s+/g, " ").trim())) {
    const i = c.text ? text.indexOf(c.text.slice(0, 40)) : -1;
    if (i >= 0) { start = i; end = i + c.text.length; }
  }
  return <div className="modal-bg" onClick={onClose}>
    <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Source document">
      <div className="hd">
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 650 }}>{page?.document.title ?? c.title ?? c.doc_id}</div>
          <div className="small muted">Page {c.page_no}{page?.page.ocr ? ` · OCR (confidence ${Math.round((page.page.ocr_conf ?? 0) * 100)}%)` : " · text layer"} · evidence span highlighted</div>
        </div>
        <a className="btn sm" href={`/api/documents/${c.doc_id}/file#page=${c.page_no}`} target="_blank" rel="noreferrer">Open original</a>
        <button className="btn sm" onClick={onClose}>Close</button>
      </div>
      <div className="bd">
        {err && <div className="empty">{err}</div>}
        {page && <pre className="page">{text.slice(0, start)}<mark id="cite-mark">{text.slice(start, end)}</mark>{text.slice(end)}</pre>}
      </div>
    </div>
  </div>;
}
