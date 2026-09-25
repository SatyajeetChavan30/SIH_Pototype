import { api } from "../api";

export const REPORT_TYPES = [".pdf", ".xml"];
export const isReport = (f: File) => REPORT_TYPES.some((x) => f.name.toLowerCase().endsWith(x));

/** Sends one DDR/WCR PDF or WITSML XML to the ingestion pipeline; resolves to the pipeline trace. */
export function uploadReport(f: File) {
  const fd = new FormData(); fd.append("file", f);
  return api("/api/ingest", { method: "POST", body: fd });
}

/** Drag-and-drop / click-to-browse box for report files; with `multiple`, also accepts many files or a folder. */
export default function UploadDrop({ onFiles, disabled, multiple }: { onFiles: (files: File[]) => void; disabled?: boolean; multiple?: boolean }) {
  const take = (list: FileList | null | undefined) => { const fs = [...(list ?? [])]; if (fs.length && !disabled) onFiles(multiple ? fs : fs.slice(0, 1)); };
  return <div className="col" style={{ gap: 4 }}>
    <label className="card gridbg" style={{ textAlign: "center", padding: 26, cursor: disabled ? "wait" : "pointer", borderStyle: "dashed", opacity: disabled ? 0.6 : 1 }}
      onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); take(e.dataTransfer.files); }}>
      <input type="file" accept={REPORT_TYPES.join(",")} multiple={multiple} style={{ display: "none" }} disabled={disabled}
        onChange={(e) => { take(e.target.files); e.target.value = ""; }} />
      <div style={{ fontSize: 22 }}>⇪</div>Drop {multiple ? "PDF / XML files" : "PDF / XML"} here or click to browse
    </label>
    {multiple && <label className="small" style={{ alignSelf: "center", cursor: disabled ? "wait" : "pointer", color: "var(--info-ink)" }}>
      <input type="file" style={{ display: "none" }} disabled={disabled} multiple
        // a whole folder of reports, e.g. the Volve Daily_Drilling_Report_XML folder
        {...({ webkitdirectory: "", directory: "" } as any)} onChange={(e) => { take(e.target.files); e.target.value = ""; }} />
      or choose a whole folder
    </label>}
  </div>;
}
