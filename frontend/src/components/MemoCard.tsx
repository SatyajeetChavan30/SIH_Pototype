import { useRef, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../context";
import { getActor } from "../live";

/** Capture a senior engineer's know-how as a typed or spoken memo. Everything extracted goes to peer review. */
export default function MemoCard({ onResult }: { onResult: (r: any) => void }) {
  const { meta } = useApp();
  const { user } = useAuth();
  const [author, setAuthor] = useState(user?.display_name ?? (getActor() === "RTOC" ? "" : getActor()));
  const [well, setWell] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const asr = meta.asr;

  const submit = async () => {
    setBusy("Extracting…"); setErr(null);
    try {
      onResult(await api("/api/memo", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author, text, well_id: well || null }) }));
      setText("");
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const startRec = async () => {
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      chunks.current = [];
      r.ondataavailable = (e) => chunks.current.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const fd = new FormData();
        fd.append("file", new Blob(chunks.current, { type: r.mimeType }), "memo.webm");
        setBusy("Transcribing on-prem…");
        try {
          const res = await api(`/api/memo/audio?author=${encodeURIComponent(author)}&well_id=${encodeURIComponent(well)}`, { method: "POST", body: fd });
          onResult(res);
        } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
      };
      r.start();
      setRec(r);
    } catch (e: any) { setErr(`Microphone unavailable: ${e.message}`); }
  };
  const stopRec = () => { rec?.stop(); setRec(null); };

  return <div className="card col">
    <h3>Expert memo <span className="sub">institutional memory · typed or spoken (Assamese, Hindi, English)</span></h3>
    <div className="small muted">What a senior engineer knows that is not in any report. It is extracted like a DDR, credited to its author,
      and held for peer review before it can influence alerts or rankings.</div>
    <div className="row wrap">
      <input type="text" placeholder="Author (required)" value={author} onChange={(e) => setAuthor(e.target.value)} style={{ width: 180 }}
        readOnly={!!user} title={user ? "Memos are credited to the signed-in user" : undefined} />
      <input type="text" placeholder="Well id (optional)" value={well} onChange={(e) => setWell(e.target.value.toUpperCase())} style={{ width: 140 }} />
    </div>
    <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder="e.g. In Hapjan we always lost returns in the Sylhet limestone around 3,700 m. Fine LCM never worked; only a cement plug cured it."
      style={{ background: "var(--surface-2)", border: "1px solid var(--line-strong)", borderRadius: 8, padding: 8, resize: "vertical" }} />
    <div className="row wrap">
      <button className="btn sm primary" disabled={!!busy || !author.trim() || text.trim().length < 20} onClick={submit}>Submit memo for review</button>
      {rec ? <button className="btn sm" onClick={stopRec}>■ Stop and transcribe</button>
        : <button className="btn sm" disabled={!!busy || !author.trim() || !asr?.available} onClick={startRec}
          title={asr?.available ? `On-prem speech-to-text (${asr.model})` : asr?.hint ?? "Speech-to-text not installed"}>🎙 Record voice memo</button>}
      {!asr?.available && <span className="small muted">voice needs the optional on-prem speech model</span>}
      {busy && <span className="small">{busy}</span>}
    </div>
    {err && <div className="banner small">{err}</div>}
  </div>;
}
