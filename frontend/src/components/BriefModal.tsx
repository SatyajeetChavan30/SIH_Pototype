import { useEffect, type ReactNode } from "react";
import { useApp } from "../context";
import type { Brief } from "../types";

/** Renders "[n]" markers inside a fact as clickable citations. */
export function CitedText({ text, brief }: { text: string; brief: Brief }) {
  const { openCitation } = useApp();
  const parts = text.split(/(\[\d+\])/g);
  return <>{parts.map((p, i) => {
    const m = p.match(/^\[(\d+)\]$/);
    const c = m ? brief.citations.find((x) => x.n === Number(m[1])) : undefined;
    return c ? <span key={i} className="cite" onClick={() => openCitation(c)} title={c.text}>[{c.n}]</span> : <span key={i}>{p}</span>;
  })}</>;
}

/** Modal for cited, extractive documents: shift handover and after-action review. */
export default function BriefModal({ brief, onClose, footer, subtitle }: { brief: Brief; onClose: () => void; footer?: ReactNode; subtitle?: string }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return <div className="modal-bg" onClick={onClose}>
    <div className="modal brief" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={brief.title}>
      <div className="hd">
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700 }}>{brief.title}</div>
          <div className="small muted">{subtitle ?? "Assembled from recorded facts"} · {brief.mode === "llm" ? "rephrased by the on-prem LLM, citation-guarded" : "extractive, no LLM"} · {brief.citations.length} citation(s)</div>
        </div>
        <button className="btn sm" onClick={() => window.print()}>Print</button>
        <button className="btn sm" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="bd col" style={{ gap: 12 }}>
        {brief.summary && <div className="quote">{<CitedText text={brief.summary} brief={brief} />}</div>}
        {brief.sections.map((s) => <section key={s.heading}>
          <h4 className="brief-h">{s.heading}</h4>
          <ul className="brief-list">{s.items.map((it, i) => <li key={i}><CitedText text={it} brief={brief} /></li>)}</ul>
        </section>)}
        {brief.citations.length > 0 && <section>
          <h4 className="brief-h">Sources</h4>
          {brief.citations.map((c) => <div key={c.n} className="small">[{c.n}] {c.title ?? c.doc_id}, p.{c.page_no} — <span className="muted">“{(c.text ?? "").slice(0, 140)}”</span></div>)}
        </section>}
        {footer}
      </div>
    </div>
  </div>;
}
