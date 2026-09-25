import { useEffect, useRef, useState } from "react";
import { useApp } from "../context";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";
import UploadDrop, { isReport, uploadReport } from "./UploadDrop";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

interface Item { file: File; status: "queued" | "processing" | "done" | "failed"; res?: any; err?: string }

/** Upload one or many reports from any screen; shows what was extracted and hands full traces to the Ingestion view. */
export default function UploadModal({ onClose }: { onClose: () => void }) {
  const { meta, go, setLastIngest } = useApp();
  const [items, setItems] = useState<Item[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [running, setRunning] = useState(false);
  const stop = useRef(false);

  const close = () => {
    if (running && !confirm("Uploads are still in progress. Stop after the current file and close?")) return;
    stop.current = true;
    onClose();
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const run = async (files: File[]) => {
    const ok = files.filter(isReport);
    // a single unsupported file still goes to the server, so its own error message is shown
    const queue = ok.length ? ok : files.slice(0, 1);
    setSkipped(files.length - queue.length);
    const list: Item[] = queue.map((file) => ({ file, status: "queued" }));
    setItems(list);
    setRunning(true); stop.current = false;
    for (let i = 0; i < list.length && !stop.current; i++) {
      list[i] = { ...list[i], status: "processing" }; setItems([...list]);
      try { list[i] = { ...list[i], status: "done", res: await uploadReport(list[i].file) }; }
      catch (e: any) { list[i] = { ...list[i], status: "failed", err: e.message }; }
      setItems([...list]);
    }
    setRunning(false);
  };
  const viewTrace = (res: any) => { setLastIngest(res); go("ingest"); onClose(); };
  const reset = () => { setItems([]); setSkipped(0); };

  const done = items.filter((i) => i.status === "done");
  const single = items.length === 1 ? items[0] : null;
  return <div className="modal-bg" onClick={close}>
    <div className="modal" style={{ width: "min(680px, 100%)" }} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Upload reports">
      <div className="hd">
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700 }}>Upload reports</div>
          <div className="small muted">DDR/WCR PDFs (text or scanned) or WITSML drillReport XML · one file, many files or a folder · processed on this server</div>
        </div>
        <button className="btn sm" onClick={close} aria-label="Close">✕</button>
      </div>
      <div className="bd col" style={{ gap: 10 }}>
        {!items.length && <>
          <UploadDrop onFiles={run} multiple />
          {!meta.ocr.available && <div className="small muted">No OCR engine is installed: scanned pages will be stored unread until OCR is added.</div>}
        </>}
        {skipped > 0 && <div className="small muted">{plural(skipped, "file")} skipped (only .pdf and .xml are read).</div>}
        {single?.status === "processing" && <div className="small">Processing {single.file.name}… (scanned pages take a few seconds each to OCR)</div>}
        {single?.status === "failed" && <div className="banner">{single.err}</div>}
        {single?.status === "done" && <Summary res={single.res} />}
        {items.length > 1 && <BatchTable items={items} onTrace={viewTrace} />}
        {items.length > 0 && <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
          {items.length > 1 && <span className="small muted" style={{ flex: 1 }}>
            {done.length} of {items.length} read · {done.reduce((s, i) => s + (i.res?.events?.length ?? 0), 0)} events ·{" "}
            {done.reduce((s, i) => s + (i.res?.review?.length ?? 0), 0)} sent to review
            {items.some((i) => i.status === "failed") ? ` · ${items.filter((i) => i.status === "failed").length} failed` : ""}</span>}
          {running
            ? <button className="btn sm" onClick={() => { stop.current = true; }}>Stop after this file</button>
            : <button className="btn sm" onClick={reset}>Upload more</button>}
          {single?.status === "done" && <button className="btn primary" onClick={() => viewTrace(single.res)}>View full trace</button>}
        </div>}
      </div>
    </div>
  </div>;
}

function BatchTable({ items, onTrace }: { items: Item[]; onTrace: (res: any) => void }) {
  return <div className="scroll" style={{ maxHeight: 360 }}>
    <table className="t"><thead><tr><th>File</th><th>Well</th><th className="num">Events</th><th className="num">Review</th><th>Status</th><th /></tr></thead>
      <tbody>{items.map((i, n) => <tr key={n}>
        <td className="small" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={i.file.name}>{i.file.name}</td>
        <td>{i.res?.well_id ?? ""}</td>
        <td className="num">{i.res ? i.res.events?.length ?? 0 : ""}</td>
        <td className="num">{i.res ? i.res.review?.length ?? 0 : ""}</td>
        <td className="small" style={{ color: i.status === "failed" ? "var(--bad-ink)" : i.status === "done" ? "var(--good-ink)" : undefined }}>
          {i.status === "failed" ? i.err : i.status}</td>
        <td>{i.status === "done" && <button className="btn sm ghost" onClick={() => onTrace(i.res)}>trace</button>}</td>
      </tr>)}</tbody></table>
  </div>;
}

/** What one uploaded report produced. */
function Summary({ res }: { res: any }) {
  const events: any[] = res?.events ?? [];
  const pending = events.filter((e) => e.status === "pending").length;
  const ocrPages = (res?.pages ?? []).filter((p: any) => p.ocr).length;
  return <>
    <div className="small">
      <b>{res.kind ?? "Document"}</b> · well <b>{res.well_id ?? "unknown"}</b> · {plural(res.pages?.length ?? 0, "page")}
      {ocrPages > 0 ? ` (${ocrPages} read by OCR)` : ""}
    </div>
    <div className="row wrap">
      <span className="chip">{plural(events.length, "event")}</span>
      <span className="chip">{plural(res.lessons?.length ?? 0, "lesson")}</span>
      <span className="chip">{plural(res.tops?.length ?? 0, "formation top")}</span>
      <span className="chip">{res.review?.length ?? 0} sent to review</span>
    </div>
    {res.warnings?.map((w: string, i: number) => <div key={i} className="banner small">{w}</div>)}
    {events.length > 0 && <div className="scroll" style={{ maxHeight: 220 }}>
      {events.map((e) => <div key={e.id} className="small" style={{ padding: "3px 0", borderBottom: "1px solid var(--line)" }}>
        <span className="swatch" style={{ background: HAZARD_COLOR[e.hazard] }} /> <b>{HAZARD_SHORT[e.hazard] ?? e.hazard}</b> · {e.summary}
        <span className="muted"> · confidence {fmt.pct(e.confidence)}{e.status === "pending" ? " · awaiting review" : ""}</span>
      </div>)}
    </div>}
    {events.length === 0 && <div className="small muted">No drilling events were found in this document.</div>}
    {pending > 0 && <div className="small muted">{plural(pending, "low-confidence event")} will not affect alerts until approved in the review queue.</div>}
  </>;
}
