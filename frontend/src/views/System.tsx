import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { roleLabel, useAuth, type Role, type User } from "../auth";
import { useApp } from "../context";
import { JobView, fmtDuration, reloadAfterRestart, startJob, uploadJob, useJob, type Job } from "../jobs";
import { useLive } from "../live";
import { fmt } from "../theme";

interface Dataset { code: string; label: string; about: string; built: boolean; current: boolean; synthetic: boolean; path: string;
  staged: boolean; needs?: string | null; build_info: any; stream?: boolean;
  stream_source?: { wellbore: string; incidents?: number; tops?: string; casing?: string; wells?: string[];
    window: { start: string; hours: number; md_from: number; md_to: number } } | null; volve_wells?: string[] }
interface StreamInfo { spec: string; live: boolean; describe: string; in_gap: boolean; samples: number; bit_md: number | null; listen_port: number | null;
  stats: { connected: boolean; peer: string | null; packets: number; errors: number; reconnects: number; last_packet_age_s: number | null; last_error: string | null } | null }
interface SimInfo { available: boolean; running: boolean; frames: number; total: number; bit_md: number | null; speed: number; target: string | null; error: string | null }
interface AdminStatus {
  settings: Record<string, any>; locked: string[]; region: string; data_dir: string; datasets: Record<string, Dataset>;
  components: { ocr: { label: string; available: boolean; engine: string | null }; asr: { label: string; available: boolean; model: string | null; hint: string | null };
    llm: { backend: string; model: string | null } };
  stream: StreamInfo; simulator: SimInfo; episodes: { id: string; hazard: string; label: string; md: number; onset_md?: number }[];
  supervised: boolean; boot: string; jobs: Job[]; build: any; settings_file: string;
}

const ENV_NAME: Record<string, string> = { dataset: "STRATASENSE_REGION / STRATASENSE_DATA_DIR", stream: "STRATASENSE_STREAM", stream_gap_s: "STRATASENSE_STREAM_GAP_S",
  top_pick_mode: "STRATASENSE_TOP_PICK", llm_backend: "STRATASENSE_LLM", ollama_url: "OLLAMA_URL", ollama_model: "OLLAMA_MODEL", auth: "STRATASENSE_AUTH",
  tile_url: "STRATASENSE_TILE_URL", tile_attribution: "STRATASENSE_TILE_ATTRIBUTION", asr_model: "STRATASENSE_ASR_MODEL" };

const post = (path: string, body?: unknown) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export default function System() {
  const [st, setSt] = useState<AdminStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [restarting, setRestarting] = useState<{ why: string; s: number } | null>(null);
  const load = () => api<AdminStatus>("/api/admin/status").then((s) => { setSt(s); setErr(null); }).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    const t = window.setInterval(() => { if (!document.hidden) load(); }, 3000);
    return () => clearInterval(t);
  }, []);

  const restart = (why: string, boot: string) => { setRestarting({ why, s: 0 }); reloadAfterRestart(boot, (s) => setRestarting({ why, s })); };

  if (restarting) return <div className="card" style={{ maxWidth: 640 }}>
    <h3>Restarting StrataSense</h3>
    <div>{restarting.why} <span className="muted num">{fmtDuration(restarting.s)}</span></div>
    <div className="progress" style={{ marginTop: 10 }}><div className="indeterminate" style={{ width: "100%" }} /></div>
    <div className="small muted" style={{ marginTop: 8 }}>The page reloads by itself when the server is back.</div>
  </div>;
  if (!st) return err ? <div className="banner">{err}</div> : <div className="empty">Loading system status…</div>;

  const locked = new Set(st.locked);
  const busy = st.jobs.find((j) => j.status === "running" && ["build", "install", "evaluate", "validate"].includes(j.kind));
  return <div className="col">
    <div>
      <h2 className="view">System</h2>
      <p className="lede">Everything that used to need a terminal: build or switch the knowledge base, connect the live rig feed or the rig simulator,
        change alerting and AI settings, install optional engines, manage users and restart the server. Changes are saved in <code>{st.settings_file.split(/[\\/]/).pop()}</code> and survive restarts.</p>
    </div>
    <div className="kpis">
      <div className="kpi"><div className="k">Dataset in use</div><div className="v" style={{ fontSize: 15 }}>{st.datasets[st.region]?.label}</div>
        <div className="d">{st.build ? `${st.build.n_wells} wells · ${st.build.n_docs} documents` : ""}</div></div>
      <div className="kpi"><div className="k">Live rig feed</div><div className="v" style={{ fontSize: 15, color: st.stream.live ? (st.stream.stats?.connected ? "var(--good-ink)" : "var(--warn-ink)") : undefined }}>
        {st.stream.live ? (st.stream.stats?.connected ? "● connected" : "○ waiting for data") : "Replay only"}</div><div className="d">{st.stream.describe}</div></div>
      <div className="kpi"><div className="k">OCR · voice · LLM</div><div className="v" style={{ fontSize: 15 }}>
        {st.components.ocr.available ? "✔" : "✖"} · {st.components.asr.available ? "✔" : "✖"} · {st.components.llm.backend === "off" ? "off" : "✔"}</div>
        <div className="d">optional engines</div></div>
      <div className="kpi"><div className="k">Background jobs</div><div className="v" style={{ fontSize: 15 }}>{busy ? "1 running" : "idle"}</div><div className="d">{busy?.title ?? `${st.jobs.length} this session`}</div></div>
    </div>
    <div className="grid2">
      <div className="col">
        <DatasetCard st={st} locked={locked.has("dataset")} busy={busy} reload={load} restart={restart} />
        <SettingsCard st={st} locked={locked} reload={load} />
        <EnginesCard st={st} busy={busy} reload={load} />
      </div>
      <div className="col">
        <FeedCard st={st} locked={locked} reload={load} />
        <AccessCard st={st} locked={locked} reload={load} />
        <ServerCard st={st} busy={busy} restart={restart} />
      </div>
    </div>
  </div>;
}

function Lock({ k, locked }: { k: string; locked: boolean }) {
  return locked ? <span className="small muted" title={`Fixed by the ${ENV_NAME[k]} environment variable on the server`}>🔒 {ENV_NAME[k]}</span> : null;
}

function Msg({ m }: { m: { ok: boolean; text: string } | null }) {
  return m ? <span className="small" style={{ color: m.ok ? "var(--good-ink)" : "var(--bad-ink)" }}>{m.text}</span> : null;
}

/** Follows a job the user started here; `onEnd` runs once when it finishes. */
function Tracked({ id, onEnd }: { id: string | null; onEnd?: (j: Job) => void }) {
  const [job] = useJob(id, onEnd);
  return job ? <JobView job={job} /> : null;
}

// ------------------------------------------------------------------------------------------------ dataset
function DatasetCard({ st, locked, busy, reload, restart }: { st: AdminStatus; locked: boolean; busy?: Job; reload: () => void; restart: (why: string, boot: string) => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [quadrants, setQuadrants] = useState("15,16");
  const [download, setDownload] = useState(true);
  const [switchAfter, setSwitchAfter] = useState(true);
  const [csv, setCsv] = useState<{ have: string[]; missing: string[] } | null>(null);
  const boot = useRef(st.boot);
  boot.current = st.boot;
  const running = busy?.kind === "build" ? busy : undefined;
  // keep following a build started elsewhere (another tab, before a reload) until it has finished
  const [seen, setSeen] = useState<string | null>(null);
  useEffect(() => { if (running) setSeen(running.id); }, [running?.id]);
  const followId = jobId ?? seen;

  const build = async (dataset: string, extra: Record<string, unknown> = {}) => {
    const label = st.datasets[dataset].label;
    if (st.datasets[dataset].built && !confirm(`Rebuild "${label}" from scratch? The current knowledge base stays in use until the new one is ready; people's accounts and the decision log are kept.`)) return;
    setMsg(null);
    try { setJobId((await startJob("/api/admin/build", { dataset, ...extra })).id); reload(); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const onEnd = (j: Job) => {
    reload();
    if (j.status === "done" && j.result?.restart) restart("Loading the new knowledge base…", boot.current);
    else if (j.status === "done") setMsg({ ok: true, text: j.stage ?? "Built." });
  };
  const switchTo = async (code: string) => {
    if (!confirm(`Switch StrataSense to "${st.datasets[code].label}"? The server restarts (about 10–30 s); everyone's pages reload.`)) return;
    try { const r: any = await post("/api/admin/dataset", { dataset: code }); if (r.restart) restart(`Switching to ${st.datasets[code].label}…`, st.boot); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const uploadCsv = async (files: FileList | null) => {
    if (!files?.length) return;
    try { setCsv(await uploadJob("/api/admin/sodir-csv", [...files])); setDownload(false); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };

  return <div className="card col" style={{ gap: 10 }}>
    <h3>Dataset &amp; knowledge base <Lock k="dataset" locked={locked} /></h3>
    {Object.values(st.datasets).map((d) => <div key={d.code} className={`card choice ${d.current ? "rec" : ""}`} style={{ padding: "10px 12px" }}>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 650 }}>{d.label} {d.current && <span className="pill" style={{ color: "var(--good-ink)" }}>IN USE</span>}
            {!d.built && <span className="pill" style={{ color: "var(--ink-3)", marginLeft: 4 }}>NOT BUILT</span>}</div>
          <div className="small ink2" style={{ marginTop: 2 }}>{d.about}</div>
          {d.current && st.build && <div className="small muted" style={{ marginTop: 2 }}>{st.build.n_wells} wells · {st.build.n_docs} documents{st.build.seconds ? ` · built in ${fmtDuration(st.build.seconds)}` : ""}</div>}
          {d.needs && !d.built && <div className="small muted" style={{ marginTop: 4 }}>{d.needs}</div>}
        </div>
        <div className="col" style={{ gap: 6, alignItems: "flex-end" }}>
          {d.current && <button className="btn sm" disabled={!!busy} onClick={() => build(d.code)}>Rebuild</button>}
          {!d.current && d.built && <button className="btn sm primary" disabled={!!busy || locked} onClick={() => switchTo(d.code)}>Switch to this</button>}
          {!d.current && d.built && <button className="btn sm" disabled={!!busy || locked || !!d.needs} onClick={() => build(d.code, { quadrants, download: false, switch: false })}>Rebuild</button>}
        </div>
      </div>
      {d.code === "norway" && d.built && <VolveStream d={d} busy={busy} restart={restart} boot={st.boot} reload={reload} />}
      {d.code === "norway" && !d.built && !d.needs && <div className="col" style={{ gap: 6, marginTop: 8 }}>
        <div className="row wrap small" style={{ gap: 10 }}>
          <label className="row" style={{ gap: 6 }}>Quadrants <input type="text" value={quadrants} onChange={(e) => setQuadrants(e.target.value)} style={{ width: 90 }} title="Norwegian quadrants, e.g. 15,16 (Sleipner / Volve area); 'all' for the whole shelf" /></label>
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={download} onChange={(e) => setDownload(e.target.checked)} /> download from factpages.sodir.no</label>
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={switchAfter} onChange={(e) => setSwitchAfter(e.target.checked)} /> switch to it when built</label>
        </div>
        <label className="small">No internet on this server? Upload the FactPages CSV exports instead:
          <input type="file" multiple accept=".csv" style={{ marginLeft: 6 }} onChange={(e) => uploadCsv(e.target.files)} /></label>
        {csv && <div className="small muted">Have {csv.have.length} tables{csv.missing.length ? ` · still missing: ${csv.missing.join(", ")}` : " · complete"}</div>}
        <div><button className="btn sm primary" disabled={!!busy || locked} onClick={() => build("norway", { quadrants, download, switch: switchAfter })}>Build North Sea knowledge base</button></div>
      </div>}
    </div>)}
    {followId && <Tracked id={followId} onEnd={onEnd} />}
    <Msg m={msg} />
    <div className="small muted">A rebuild runs next to the knowledge base in use and is swapped in only when it has finished cleanly.</div>
  </div>;
}

interface VolveWell { wellbore: string; drilling_logs: number; files: number; log_days: number; trajectory: boolean; incidents: number }

/** Real rig data for the North Sea Live Ops: Equinor's Volve WITSML real-time logs (+ daily drilling reports). */
function VolveStream({ d, busy, restart, boot, reload }: { d: Dataset; busy?: Job; restart: (why: string, boot: string) => void; boot: string; reload: () => void }) {
  const [folder, setFolder] = useState("");
  const [ddr, setDdr] = useState("");
  const [picks, setPicks] = useState("");
  const [allWells, setAllWells] = useState(true);
  const [replay, setReplay] = useState("");
  const [hours, setHours] = useState(12);
  const [wells, setWells] = useState<VolveWell[] | null>(null);
  const [wellbore, setWellbore] = useState("");
  const [scanId, setScanId] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const src = d.stream_source;
  const body = () => ({ folder: folder.trim(), ddr_folder: ddr.trim() || undefined });

  const scan = async () => {
    setMsg(null); setWells(null);
    try { setScanId((await startJob("/api/admin/volve/scan", body())).id); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const onScanned = (j: Job) => {
    if (j.status !== "done") return;
    const rows: VolveWell[] = j.result?.wellbores ?? [];
    setWells(rows);
    setWellbore((rows.find((r) => r.incidents > 0) ?? rows[0])?.wellbore ?? "");
    if (!rows.length) setMsg({ ok: false, text: "No time-indexed drilling logs in that folder. Pick the 'WITSML Realtime drilling data' folder or a wellbore folder inside it." });
  };
  const run = async () => {
    setMsg(null);
    try { setImportId((await startJob("/api/admin/volve/import", { ...body(), picks: picks.trim() || undefined, all: allWells,
      wellbore: wellbore || undefined, hours })).id); reload(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const onImported = (j: Job) => {
    reload();
    if (j.status === "done" && j.result?.restart) restart("Loading the Volve rig stream…", boot);
    else if (j.status === "done") setMsg({ ok: true, text: "Imported. It is used when the North Sea dataset is switched on." });
  };
  const activate = async () => {
    const wid = replay || d.volve_wells?.[0];
    if (!wid || !confirm(`Replay ${wid} in Live Ops? ${d.current ? "The server restarts (about 10–30 s)." : ""}`)) return;
    setMsg(null);
    try {
      const r = await api<{ restart: boolean }>("/api/admin/volve/activate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ wellbore: wid }) });
      if (r.restart) restart(`Loading ${wid}…`, boot); else { setMsg({ ok: true, text: `${wid} will be replayed when the North Sea dataset is in use.` }); reload(); }
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const upload = async (f: File | undefined, set: (p: string) => void) => {
    if (!f) return;
    setUploading(true); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", f, f.name);
      set((await api<{ folder: string }>("/api/admin/volve/upload", { method: "POST", body: fd })).folder);
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setUploading(false); }
  };

  return <div className="col" style={{ gap: 6, marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--line)" }}>
    <div style={{ fontWeight: 650 }}>Real rig stream for Live Ops: Equinor Volve</div>
    <div className="small ink2">{src ? <>In use: <b>{src.wellbore}</b>, {src.window.start.slice(0, 16).replace("T", " ")} UTC, {src.window.hours} h, bit {Math.round(src.window.md_from)}–{Math.round(src.window.md_to)} m MD
      {src.incidents ? ` · ${src.incidents} real incident${src.incidents > 1 ? "s" : ""} from the drilling reports` : " · no coded incidents"}
      {src.tops ? ` · tops: ${src.tops}` : ""}{src.wells && src.wells.length > 1 ? ` · ${src.wells.length} Volve wellbores imported (the others are offsets)` : ""}</>
      : "None yet: Live Ops has no stream in this dataset."}</div>
    <div className="small muted">Download from <a href="https://www.equinor.com/energy/volve-data-sharing" target="_blank" rel="noreferrer">Equinor's Volve data sharing</a> (Databricks Marketplace, free account):
      the <b>WITSML Realtime drilling data</b> folder, and for real incidents the daily drilling reports (XML) from <b>Well_technical_data</b>. Equinor Open Data Licence: attribution required, no sale of the data.</div>
    <label className="small col" style={{ gap: 2 }}>WITSML real-time folder on this server
      <input type="text" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="D:\Volve\WITSML Realtime drilling data" /></label>
    <label className="small">or upload a .zip of it <input type="file" accept=".zip" disabled={uploading} onChange={(e) => upload(e.target.files?.[0], setFolder)} /></label>
    <label className="small col" style={{ gap: 2 }}>Daily drilling reports folder (optional, for real incidents)
      <input type="text" value={ddr} onChange={(e) => setDdr(e.target.value)} placeholder="D:\Volve\Well_technical_data" /></label>
    <label className="small">or upload a .zip of the reports <input type="file" accept=".zip" disabled={uploading} onChange={(e) => upload(e.target.files?.[0], setDdr)} /></label>
    <label className="small col" style={{ gap: 2 }}>Formation picks file (optional: Geophysical_Interpretations/Wells/Well_picks_Volve_v1.dat)
      <input type="text" value={picks} onChange={(e) => setPicks(e.target.value)} placeholder="D:\Volve\picks\Well_picks_Volve_v1.dat" /></label>
    <label className="small row" style={{ gap: 4 }}><input type="checkbox" checked={allWells} onChange={(e) => setAllWells(e.target.checked)} />
      import every usable wellbore (the others become offset wells with real logs, casing and picked tops)</label>
    {uploading && <div className="small muted">Uploading and unpacking…</div>}
    <div className="row wrap small" style={{ gap: 8 }}>
      <button className="btn sm" disabled={!folder.trim() || !!busy} onClick={scan}>Scan folder</button>
      {wells && wells.length > 0 && <label className="row" style={{ gap: 4 }}>Wellbore
        <select value={wellbore} onChange={(e) => setWellbore(e.target.value)}>
          {wells.map((w) => <option key={w.wellbore} value={w.wellbore}>{w.wellbore} · {w.files} log files · {w.log_days} log-days{w.incidents ? ` · ${w.incidents} incidents` : ""}{w.trajectory ? "" : " · no survey"}</option>)}
        </select></label>}
      <label className="row" style={{ gap: 4 }}>Replay <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
        {[6, 12, 24].map((h) => <option key={h} value={h}>{h} h</option>)}</select></label>
      <button className="btn sm primary" disabled={!folder.trim() || !!busy} onClick={run}>Import Volve stream</button>
    </div>
    {(d.volve_wells?.length ?? 0) > 1 && <div className="row wrap small" style={{ gap: 8 }}>
      <label className="row" style={{ gap: 4 }}>Replay this well
        <select value={replay || src?.wellbore || ""} onChange={(e) => setReplay(e.target.value)}>
          {d.volve_wells!.map((w) => <option key={w} value={w}>{w}{w === src?.wellbore ? " (in use)" : ""}</option>)}
        </select></label>
      <button className="btn sm" disabled={!!busy || !replay || replay === src?.wellbore} onClick={activate}>Replay it</button>
    </div>}
    {scanId && <Tracked id={scanId} onEnd={onScanned} />}
    {importId && <Tracked id={importId} onEnd={onImported} />}
    <Msg m={msg} />
  </div>;
}

// ------------------------------------------------------------------------------------------------ live feed
type FeedKind = "replay" | "wits0-listen" | "wits0-connect" | "witsml";

function parseSpec(spec: string) {
  const [kind, ...rest] = spec.split(":");
  const r = rest.join(":");
  const f = { kind: (["replay", "wits0-listen", "wits0-connect", "witsml"].includes(kind) ? kind : "replay") as FeedKind,
    port: "5501", host: "", cport: "5501", url: "", well: "", wellbore: "", log: "", user: "", password: "", interval: "10" };
  if (f.kind === "wits0-listen") f.port = r || "5501";
  if (f.kind === "wits0-connect") { const i = r.lastIndexOf(":"); f.host = r.slice(0, i); f.cport = r.slice(i + 1); }
  if (f.kind === "witsml") {
    try {
      const u = new URL(r);
      f.url = `${u.origin}${u.pathname}`;
      for (const k of ["well", "wellbore", "log", "user", "password", "interval"] as const) f[k] = u.searchParams.get(k) ?? f[k];
    } catch { f.url = r; }
  }
  return f;
}

function buildSpec(f: ReturnType<typeof parseSpec>): string {
  if (f.kind === "wits0-listen") return `wits0-listen:${f.port}`;
  if (f.kind === "wits0-connect") return `wits0-connect:${f.host}:${f.cport}`;
  if (f.kind === "witsml") {
    const q = new URLSearchParams({ well: f.well, wellbore: f.wellbore, log: f.log, interval: f.interval });
    if (f.user) q.set("user", f.user);
    if (f.password) q.set("password", f.password);
    return `witsml:${f.url}?${q.toString()}`;
  }
  return "replay";
}

function FeedCard({ st, locked, reload }: { st: AdminStatus; locked: Set<string>; reload: () => void }) {
  const { refreshMeta, go } = useApp();
  const [, , setMode] = useLive();
  const [f, setF] = useState(() => parseSpec(st.stream.spec));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [scenario, setScenario] = useState("");
  const [speed, setSpeed] = useState(60);
  const [busy, setBusy] = useState(false);
  const s = st.stream, stats = s.stats, sim = st.simulator;
  const lockedFeed = locked.has("stream");

  const apply = async () => {
    setBusy(true); setMsg(null);
    try {
      await post("/api/admin/stream", { spec: buildSpec(f) });
      await refreshMeta();
      if (f.kind === "replay") setMode("replay");
      setMsg({ ok: true, text: f.kind === "replay" ? "Live feed disconnected; consoles use the stored replay." : "Feed applied; consoles on the old feed reconnect." });
      reload();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  const startSim = async () => {
    setBusy(true); setMsg(null);
    try {
      await post("/api/admin/simulator/start", { episode: scenario || undefined, speed, port: f.kind === "wits0-listen" ? Number(f.port) : 5501 });
      await refreshMeta();
      setMode("live");
      reload();
      setF(parseSpec((await api<AdminStatus>("/api/admin/status")).stream.spec));
      setMsg({ ok: true, text: "Simulated rig is transmitting. Open Live Ops → ● Live rig feed to watch StrataSense read it." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  const stopSim = async () => { await post("/api/admin/simulator/stop"); reload(); };
  const set = (k: string, v: string) => setF({ ...f, [k]: v });

  return <div className="card col" style={{ gap: 10 }}>
    <h3>Live rig feed <span className="sub">how eRTMAC data reaches StrataSense</span> <Lock k="stream" locked={lockedFeed} /></h3>
    <div className="row wrap small" style={{ gap: 12 }}>
      <span><b>Now:</b> {s.describe}</span>
      {s.live && stats && <>
        <span style={{ color: stats.connected ? "var(--good-ink)" : "var(--warn-ink)" }}>{stats.connected ? `● connected${stats.peer ? ` (${stats.peer})` : ""}` : "○ not connected"}</span>
        <span className="num">{fmt.n0(stats.packets)} packets · {fmt.n0(s.samples)} samples</span>
        {s.bit_md != null && <span className="num">bit {fmt.n0(s.bit_md)} m</span>}
        {stats.last_packet_age_s != null && <span className="muted">last packet {Math.round(stats.last_packet_age_s)} s ago</span>}
        {s.in_gap && <span style={{ color: "var(--bad-ink)" }}>stream gap</span>}
      </>}
    </div>
    {stats?.last_error && <div className="small" style={{ color: "var(--bad-ink)" }}>{stats.last_error}</div>}
    <div className="seg" role="group" aria-label="Feed type">
      {([["replay", "Replay only"], ["wits0-listen", "WITS-0 listen"], ["wits0-connect", "WITS-0 connect"], ["witsml", "WITSML"]] as [FeedKind, string][])
        .map(([k, l]) => <button key={k} className={f.kind === k ? "on" : ""} disabled={lockedFeed} onClick={() => set("kind", k)}>{l}</button>)}
    </div>
    <div className="row wrap small" style={{ gap: 8 }}>
      {f.kind === "replay" && <span className="muted">Each browser replays the stored eRTMAC stream of the active well privately (demo mode).</span>}
      {f.kind === "wits0-listen" && <label className="row" style={{ gap: 6 }}>Listen on TCP port <input type="number" value={f.port} onChange={(e) => set("port", e.target.value)} style={{ width: 90 }} />
        <span className="muted">point the rig's WITS-0 box / eRTMAC relay at this server</span></label>}
      {f.kind === "wits0-connect" && <><span className="muted">StrataSense connects to a WITS-0 TCP server (serial-over-IP box).</span><label className="row" style={{ gap: 6 }}>Host <input type="text" value={f.host} placeholder="10.0.0.5" onChange={(e) => set("host", e.target.value)} style={{ width: 150 }} /></label>
        <label className="row" style={{ gap: 6 }}>Port <input type="number" value={f.cport} onChange={(e) => set("cport", e.target.value)} style={{ width: 90 }} /></label></>}
      {f.kind === "witsml" && <div className="col" style={{ gap: 6, width: "100%" }}>
        <span className="muted">StrataSense polls a WITSML 1.4.1 store for new log rows.</span>
        <input type="text" value={f.url} placeholder="https://witsml-store/Store/WMLS.asmx" onChange={(e) => set("url", e.target.value)} />
        <div className="row wrap" style={{ gap: 6 }}>
          {(["well", "wellbore", "log"] as const).map((k) => <input key={k} type="text" value={f[k]} placeholder={`${k} uid`} onChange={(e) => set(k, e.target.value)} style={{ width: 130 }} />)}
          <input type="text" value={f.user} placeholder="user (optional)" onChange={(e) => set("user", e.target.value)} style={{ width: 130 }} />
          <input type="password" value={f.password} placeholder="password" onChange={(e) => set("password", e.target.value)} style={{ width: 120 }} />
          <label className="row" style={{ gap: 4 }}>poll every <input type="number" value={f.interval} onChange={(e) => set("interval", e.target.value)} style={{ width: 60 }} /> s</label>
        </div>
      </div>}
    </div>
    <div className="row" style={{ gap: 8 }}>
      <button className="btn sm primary" disabled={busy || lockedFeed || buildSpec(f) === s.spec} onClick={apply}>Apply feed</button>
      <Msg m={msg} />
    </div>

    <div className="card" style={{ background: "var(--surface-2)" }}>
      <h3>Rig simulator <span className="sub">the recorded well sent as real WITS-0 frames, for demos and testing</span></h3>
      {!sim.available ? <div className="small muted">This dataset has no recorded rig stream (use the Assam demo).</div> : <>
        <div className="row wrap small" style={{ gap: 8 }}>
          <label className="row" style={{ gap: 6 }}>Start at <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={sim.running}>
            <option value="">Spud (from the top)</option>
            {st.episodes.map((e) => <option key={e.id} value={e.id}>{e.id}: {e.label} (~{fmt.n0(e.onset_md ?? e.md)} m)</option>)}
          </select></label>
          <label className="row" style={{ gap: 6 }}>Speed <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} disabled={sim.running}>
            {[1, 10, 60, 300, 600].map((v) => <option key={v} value={v}>{v === 1 ? "real time" : `${v}×`}</option>)}</select></label>
          {sim.running ? <button className="btn sm" onClick={stopSim}>■ Stop</button>
            : <button className="btn sm primary" disabled={busy || (lockedFeed && !s.listen_port)} onClick={startSim}>▶ Start simulated rig</button>}
          {sim.running && <button className="btn sm" onClick={() => go("live")}>Open Live Ops</button>}
        </div>
        {(sim.running || sim.frames > 0) && <div className="small" style={{ marginTop: 6 }}>
          {sim.running ? "Transmitting" : "Stopped"} · {fmt.n0(sim.frames)} / {fmt.n0(sim.total)} frames{sim.bit_md != null ? ` · bit ${fmt.n0(sim.bit_md)} m` : ""} · {sim.speed}× → {sim.target}
          <div className="progress" style={{ marginTop: 4 }}><div style={{ width: `${sim.total ? (sim.frames / sim.total) * 100 : 0}%` }} /></div></div>}
        {sim.error && <div className="small" style={{ color: "var(--bad-ink)" }}>{sim.error}</div>}
        <div className="small muted" style={{ marginTop: 6 }}>Only what a rig WITS box sends goes over the wire; StrataSense infers rig state, computes the d-exponent and picks tops from gamma ray itself.
          {s.spec.startsWith("wits0-listen") ? "" : " Starting it switches the feed to a WITS-0 listener on port 5501."}</div>
      </>}
    </div>
  </div>;
}

// ------------------------------------------------------------------------------------------------ settings
function Field({ label, k, locked, children }: { label: string; k: string; locked: boolean; children: ReactNode }) {
  return <label className="row wrap small" style={{ gap: 8 }}><span style={{ width: 150 }}>{label}</span>{children}<Lock k={k} locked={locked} /></label>;
}

function SettingsCard({ st, locked, reload }: { st: AdminStatus; locked: Set<string>; reload: () => void }) {
  const { refreshMeta } = useApp();
  const [d, setD] = useState<Record<string, any>>(st.settings);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const keys = ["top_pick_mode", "stream_gap_s", "llm_backend", "ollama_url", "ollama_model", "asr_model", "tile_url", "tile_attribution"];
  const changed = Object.fromEntries(keys.filter((k) => !locked.has(k) && String(d[k]) !== String(st.settings[k])).map((k) => [k, d[k]]));
  const save = async () => {
    setMsg(null);
    try { await post("/api/admin/settings", changed); await refreshMeta(); reload(); setMsg({ ok: true, text: "Saved." }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const test = async () => {
    const r: any = await post("/api/admin/llm/test", { url: d.ollama_url });
    if (r.ok) { setModels(r.models); setMsg({ ok: true, text: `Ollama reachable · ${r.models.length} model(s)` }); } else setMsg({ ok: false, text: `Ollama not reachable: ${r.error}` });
  };
  const set = (k: string, v: any) => setD({ ...d, [k]: v });
  const dis = (k: string) => locked.has(k);
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Alerting, AI &amp; map settings</h3>
    <Field label="Formation-top picking" k="top_pick_mode" locked={dis("top_pick_mode")}>
      <select value={d.top_pick_mode} disabled={dis("top_pick_mode")} onChange={(e) => set("top_pick_mode", e.target.value)}>
        <option value="auto">mud logger + DTW QC (recommended)</option><option value="mudlogger">mud logger only</option><option value="dtw">gamma-ray correlation (DTW) only</option>
      </select></Field>
    <Field label="Stream-gap alarm after" k="stream_gap_s" locked={dis("stream_gap_s")}>
      <input type="number" min={10} value={d.stream_gap_s} disabled={dis("stream_gap_s")} onChange={(e) => set("stream_gap_s", e.target.value)} style={{ width: 90 }} /> s without data</Field>
    <Field label="Answer phrasing (LLM)" k="llm_backend" locked={dis("llm_backend")}>
      <select value={d.llm_backend} disabled={dis("llm_backend")} onChange={(e) => set("llm_backend", e.target.value)}>
        <option value="off">off: grounded extractive answers</option><option value="ollama">on-prem Ollama (citation-guarded)</option></select></Field>
    {d.llm_backend === "ollama" && <>
      <Field label="Ollama URL" k="ollama_url" locked={dis("ollama_url")}>
        <input type="text" value={d.ollama_url} disabled={dis("ollama_url")} onChange={(e) => set("ollama_url", e.target.value)} style={{ width: 220 }} />
        <button className="btn sm" onClick={test}>Test connection</button></Field>
      <Field label="Ollama model" k="ollama_model" locked={dis("ollama_model")}>
        <input type="text" list="ollama-models" value={d.ollama_model} disabled={dis("ollama_model")} onChange={(e) => set("ollama_model", e.target.value)} style={{ width: 220 }} />
        <datalist id="ollama-models">{models.map((m) => <option key={m} value={m} />)}</datalist></Field>
    </>}
    <Field label="Voice-memo model" k="asr_model" locked={dis("asr_model")}>
      <input type="text" value={d.asr_model} disabled={dis("asr_model")} onChange={(e) => set("asr_model", e.target.value)} style={{ width: 120 }} /> <span className="muted">Whisper size: tiny, base, small, medium</span></Field>
    <Field label="Map tiles URL" k="tile_url" locked={dis("tile_url")}>
      <input type="text" value={d.tile_url} disabled={dis("tile_url")} onChange={(e) => set("tile_url", e.target.value)} style={{ flex: 1, minWidth: 220 }} /></Field>
    <Field label="Map attribution" k="tile_attribution" locked={dis("tile_attribution")}>
      <input type="text" value={d.tile_attribution} disabled={dis("tile_attribution")} onChange={(e) => set("tile_attribution", e.target.value)} style={{ flex: 1, minWidth: 220 }} /></Field>
    <div className="row" style={{ gap: 8 }}>
      <button className="btn sm primary" disabled={!Object.keys(changed).length} onClick={save}>Save settings</button>
      {Object.keys(changed).length > 0 && <button className="btn sm ghost" onClick={() => setD(st.settings)}>Discard</button>}
      <Msg m={msg} />
    </div>
    <div className="small muted">Settings apply immediately. Top picking applies to replays started after the change and to the live feed after it reconnects. For an air-gapped site, point the map at an on-prem tile server.</div>
  </div>;
}

// ------------------------------------------------------------------------------------------------ engines
function EnginesCard({ st, busy, reload }: { st: AdminStatus; busy?: Job; reload: () => void }) {
  const { refreshMeta } = useApp();
  const [jobId, setJobId] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const install = async (component: string) => {
    setMsg(null);
    try { setJobId((await startJob("/api/admin/install", { component })).id); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const c = st.components;
  const rows: [string, string, boolean, string][] = [
    ["ocr", c.ocr.label, c.ocr.available, c.ocr.available ? `ready · ${c.ocr.engine}` : "not installed: scanned pages are flagged 'needs OCR'"],
    ["asr", c.asr.label, c.asr.available, c.asr.available ? `ready · model ${c.asr.model}` : "not installed: typed memos still work"],
  ];
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Optional engines <span className="sub">installed on this server, used offline</span></h3>
    <table className="t"><tbody>{rows.map(([k, label, ok, note]) => <tr key={k}>
      <td>{label}<div className="small muted">{note}</div></td>
      <td style={{ textAlign: "right", verticalAlign: "middle" }}>{ok ? <span style={{ color: "var(--good-ink)" }}>✔</span>
        : <button className="btn sm" disabled={!!busy} onClick={() => install(k)}>Install</button>}</td></tr>)}
      <tr><td>On-prem LLM for answer phrasing<div className="small muted">{c.llm.backend === "off" ? "off (answers stay grounded and cited without it)" : `Ollama · ${c.llm.model}`}</div></td>
        <td style={{ textAlign: "right" }} className="small muted">see settings</td></tr>
    </tbody></table>
    {jobId && <Tracked id={jobId} onEnd={() => { reload(); refreshMeta(); }} />}
    <Msg m={msg} />
    <div className="small muted">Installing needs internet access (or a local package mirror) on the server. Speech-to-text also downloads its Whisper model the first time a voice memo is transcribed.</div>
  </div>;
}

// ------------------------------------------------------------------------------------------------ access
function AccessCard({ st, locked, reload }: { st: AdminStatus; locked: Set<string>; reload: () => void }) {
  const { authOn } = useAuth();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const on = !!st.settings.auth;
  const toggle = async () => {
    const next = !on;
    if (!next && !confirm("Switch sign-in off? Anyone who can reach this server then gets full administrator access and the decision log records names typed by the browser. Only do this on an isolated test machine.")) return;
    try { await post("/api/admin/settings", { auth: next }); reload(); setMsg({ ok: true, text: next ? "Sign-in is on; the page reloads so you can sign in." : "Sign-in is off." }); if (next) setTimeout(() => location.reload(), 1200); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Access &amp; users <Lock k="auth" locked={locked.has("auth")} /></h3>
    <div className="row wrap small" style={{ gap: 10 }}>
      <span>Sign-in and roles: <b style={{ color: on ? "var(--good-ink)" : "var(--bad-ink)" }}>{on ? "on" : "off"}</b></span>
      <button className="btn sm" disabled={locked.has("auth")} onClick={toggle}>{on ? "Switch off" : "Switch on"}</button>
      <Msg m={msg} />
    </div>
    {authOn ? <UsersCard /> : <div className="small muted">User accounts are managed here once sign-in is on (seeded accounts: field / office / admin, password demo).</div>}
  </div>;
}

/** Who can sign in, and with which role. */
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
  return <div>
    <div className="small muted" style={{ marginBottom: 6 }}>field: live, map, risk, knowledge, memos · office: + ingestion, review, what-if, analytics · admin: + users, log verification, System</div>
    <table className="t"><thead><tr><th>Username</th><th>Name</th><th>Role</th></tr></thead>
      <tbody>{users.map((u) => <tr key={u.username}><td>{u.username}</td><td>{u.display_name}</td><td>{roleLabel(u.role)}</td></tr>)}</tbody></table>
    <div className="row wrap" style={{ marginTop: 8, gap: 6 }}>
      <input type="text" placeholder="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} style={{ width: 120 }} />
      <input type="text" placeholder="display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} style={{ width: 170 }} />
      <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
        {(["field", "office", "admin"] as Role[]).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
      </select>
      <input type="password" placeholder="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} style={{ width: 120 }} />
      <button className="btn sm primary" disabled={!f.username || f.password.length < 4} onClick={add}>Add user</button>
      {msg && <span className="small muted">{msg}</span>}
    </div>
  </div>;
}

// ------------------------------------------------------------------------------------------------ server
function ServerCard({ st, busy, restart }: { st: AdminStatus; busy?: Job; restart: (why: string, boot: string) => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const doRestart = async () => {
    if (!confirm("Restart the StrataSense server now? Live consoles reconnect by themselves.")) return;
    try { await post("/api/admin/restart"); restart("Restarting the server…", st.boot); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return <div className="card col" style={{ gap: 8 }}>
    <h3>Server &amp; background jobs</h3>
    <div className="small ink2">Knowledge base folder: <code>{st.data_dir}</code><br />
      {st.supervised ? "Running under the StrataSense launcher: restarts are handled automatically." : "Started without the launcher: a restart re-launches the same process."}</div>
    <div className="row" style={{ gap: 8 }}><button className="btn sm" disabled={!!busy} onClick={doRestart}>Restart server</button><Msg m={msg} /></div>
    {st.jobs.length === 0 ? <div className="small muted">No background jobs since the server started.</div> :
      <table className="t"><thead><tr><th>Job</th><th>Status</th><th className="num">Took</th></tr></thead>
        <tbody>{st.jobs.map((j) => <tr key={j.id} className="click" onClick={() => setSel(sel === j.id ? null : j.id)}>
          <td>{j.title}</td><td style={{ color: j.status === "failed" ? "var(--bad-ink)" : j.status === "done" ? "var(--good-ink)" : undefined }}>{j.status}</td>
          <td className="num">{fmtDuration(j.elapsed_s)}</td></tr>)}</tbody></table>}
    {sel && <Tracked id={sel} />}
  </div>;
}
