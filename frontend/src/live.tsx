import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { SIGNED_OUT_EVENT, wsUrl } from "./api";
import type { Alert, Brief, Episode, LiveEvent, LiveStatus, RibbonBin, Sample, TopPred, Zone } from "./types";

export type StreamMode = "replay" | "live";

export interface LiveData {
  connected: boolean;
  playing: boolean;
  speed: number;
  status: LiveStatus | null;
  samples: Sample[];
  alerts: Map<string, Alert>;
  zones: Zone[];
  tops: Record<string, TopPred>;
  window: Record<string, any>;
  grid: number[];
  episodes: Episode[];
  ribbon: RibbonBin[];
  events: LiveEvent[];
  sections: any[];
  version: number;
  autoPause: boolean;
  pausedOn: string | null;   // alert key that triggered an auto-pause
  sessionId: string | null;  // decision-log session id
  handover: Brief | null;    // last shift-handover brief received
  mode: StreamMode;          // stored replay (private) or the shared live rig feed
  offlineSince: number | null;  // epoch ms of the cached snapshot being shown while disconnected
  outbox: number;            // acknowledgements waiting for the link to come back
  noStream: string | null;   // server has no real-time stream to replay (e.g. the real public-data region)
}

/** Who is at the console: written into the decision log with every acknowledgement and feedback. */
export function getActor(): string {
  try { return localStorage.getItem("nwis.actor") || "RTOC"; } catch { return "RTOC"; }
}
export function setActor(v: string) {
  try { localStorage.setItem("nwis.actor", v); } catch { /* private mode: keep default */ }
}

const MAX_SAMPLES = 480;
const SNAP_KEY = "nwis.rigSnapshot";
const OUTBOX_KEY = "nwis.outbox";
const MODE_KEY = "nwis.streamMode";

function load<T>(key: string, dflt: T): T {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : dflt; } catch { return dflt; }
}
function save(key: string, v: unknown) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage full or blocked: offline cache is best-effort */ }
}

class LiveStore {
  d: LiveData = { connected: false, playing: true, speed: 4, status: null, samples: [], alerts: new Map(), zones: [], tops: {},
    window: {}, grid: [], episodes: [], ribbon: [], events: [], sections: [], version: 0, autoPause: true, pausedOn: null,
    sessionId: null, handover: null, mode: load<StreamMode>(MODE_KEY, "replay"), offlineSince: null, outbox: 0, noStream: null };
  seenCritical = new Set<string>();
  ws: WebSocket | null = null;
  subs = new Set<() => void>();
  timer: number | null = null;
  retry: number | null = null;
  attempts = 0;
  lastSnap = 0;
  queue: Record<string, unknown>[] = load(OUTBOX_KEY, []);

  constructor() {
    this.d.outbox = this.queue.length;
    this.hydrate();
    if (typeof window !== "undefined") window.addEventListener("online", () => { if (!this.ws) this.connect(); });
  }

  /** Show the last known rig picture immediately (and whenever the link is down). */
  hydrate() {
    const snap = load<any>(SNAP_KEY, null);
    if (!snap || this.d.status) return;
    const d = this.d;
    d.status = snap.status; d.zones = snap.zones ?? []; d.tops = snap.tops ?? {}; d.window = snap.window ?? {};
    d.sections = snap.sections ?? []; d.sessionId = snap.sessionId ?? null; d.offlineSince = snap.savedAt;
    d.alerts = new Map((snap.alerts ?? []).map((a: Alert) => [a.key, a]));
  }

  snapshot(force = false) {
    const now = Date.now();
    if (!this.d.status || (!force && now - this.lastSnap < 5000)) return;
    this.lastSnap = now;
    const open = [...this.d.alerts.values()].filter((a) => a.status !== "cleared").slice(0, 12)
      .map((a) => ({ ...a, analogs: [], evidence: a.evidence?.slice(0, 2) ?? [], history: a.history?.slice(-4) ?? [] }));
    save(SNAP_KEY, { savedAt: now, mode: this.d.mode, status: this.d.status, alerts: open, zones: this.d.zones.slice(0, 12),
      tops: this.d.tops, window: this.d.window, sections: this.d.sections, sessionId: this.d.sessionId });
  }

  connect() {
    if (this.ws) return;
    if (this.retry != null) { clearTimeout(this.retry); this.retry = null; }
    const ws = new WebSocket(wsUrl(`/ws/live${this.d.mode === "live" ? "?mode=live" : ""}`));
    this.ws = ws;
    ws.onopen = () => { this.attempts = 0; this.d.connected = true; this.bump(true); };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;             // an intentional reconnect (mode switch) already replaced it
      this.d.connected = false; this.ws = null;
      if (this.d.noStream) { this.bump(true); return; }   // nothing to reconnect to
      if (ev.code === 4000) {                 // the feed was replaced from the System view: reconnect now
        this.attempts = 0;
        this.retry = window.setTimeout(() => this.connect(), 300);
        this.bump(true);
        return;
      }
      if (ev.code === 4401) {
        // the server refused the session (signed out or expired): show sign-in instead of retrying forever;
        // LiveProvider reconnects once the user is back in
        this.bump(true);
        window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
        return;
      }
      this.snapshot(true);
      if (this.d.offlineSince == null) this.d.offlineSince = Date.now();
      this.bump(true);
      // exponential back-off: VSAT links flap, hammering them does not help
      const delay = Math.min(2500 * 2 ** this.attempts++, 30000);
      this.retry = window.setTimeout(() => this.connect(), delay);
    };
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data));
  }

  setMode(m: StreamMode) {
    if (m === this.d.mode) return;
    this.d.mode = m; save(MODE_KEY, m); this.d.noStream = null;
    const old = this.ws; this.ws = null;
    old?.close();
    this.d.connected = false; this.d.status = null; this.d.samples = []; this.d.alerts = new Map();
    this.bump(true);
    this.connect();
  }

  flushOutbox() {
    if (!this.queue.length || this.ws?.readyState !== 1) return;
    for (const c of this.queue) this.ws.send(JSON.stringify(c));
    this.queue = []; save(OUTBOX_KEY, []); this.d.outbox = 0;
  }

  onMessage(m: any) {
    const d = this.d;
    if (m.type === "error" && m.code === "no_stream") {
      d.noStream = m.message;
    } else if (m.type === "init") {
      // the server serves the replay when no live feed is connected (e.g. it was switched off in System)
      if (m.mode && m.mode !== d.mode) { d.mode = m.mode; save(MODE_KEY, m.mode); }
      d.playing = true; d.offlineSince = null; d.noStream = null;
      d.samples = []; d.alerts = new Map(); d.events = [];
      d.episodes = m.episodes; d.zones = m.zones; d.tops = m.tops; d.window = m.window; d.grid = m.grid; d.ribbon = m.ribbon;
      d.sections = m.sections; d.status = m.status; d.pausedOn = null; d.sessionId = m.session_id ?? null;
      this.seenCritical = new Set((m.alerts as Alert[]).map((a) => a.id));
      for (const a of m.alerts as Alert[]) d.alerts.set(a.key, a);
      this.flushOutbox();
    } else if (m.type === "tick") {
      if (m.samples?.length) {
        d.samples = d.samples.concat(m.samples);
        if (d.samples.length > MAX_SAMPLES) d.samples = d.samples.slice(d.samples.length - MAX_SAMPLES);
      }
      for (const a of m.alerts as Alert[]) {
        d.alerts.set(a.key, a);
        // stop the replay on every new critical alert so the audience sees it (at any replay speed); a live feed never pauses
        if (a.level === "critical" && a.status === "active" && !this.seenCritical.has(a.id)) {
          this.seenCritical.add(a.id);
          if (d.mode === "replay" && d.autoPause && d.playing) { d.pausedOn = a.key; this.send({ cmd: "pause" }); }
        }
      }
      if (m.status) d.status = m.status;
      if (m.events?.length) d.events = [...m.events, ...d.events].slice(0, 30);
      if (m.ribbon) d.ribbon = m.ribbon;
      if (m.zones) d.zones = m.zones;
      if (m.tops) d.tops = m.tops;
      this.snapshot();
    } else if (m.type === "handover") {
      d.handover = m as Brief;
    } else if (m.type === "analogs") {
      (d as any).analogSnapshot = m;
    }
    this.bump(m.type === "init");
  }
  bump(now = false) {
    this.d.version++;
    if (now) { this.flush(); return; }
    if (this.timer == null) this.timer = window.setTimeout(() => this.flush(), 200);
  }
  flush() {
    if (this.timer != null) { clearTimeout(this.timer); this.timer = null; }
    this.d = { ...this.d };
    this.subs.forEach((f) => f());
  }
  send(cmd: Record<string, unknown>) {
    if (cmd.cmd === "autoPause") { this.d.autoPause = Boolean(cmd.value); this.bump(true); return; }
    if (cmd.cmd === "closeHandover") { this.d.handover = null; this.bump(true); return; }
    if (cmd.cmd === "ack") {
      const a = [...this.d.alerts.values()].find((x) => x.id === cmd.id);
      cmd = { ...cmd, actor: getActor(), key: a?.key, acted_at: new Date().toISOString() };
      if (this.ws?.readyState !== 1) {
        // link down: record the acknowledgement locally and deliver it when the feed comes back
        this.queue.push({ ...cmd, queued_offline: true }); save(OUTBOX_KEY, this.queue); this.d.outbox = this.queue.length;
        if (a) this.d.alerts.set(a.key, { ...a, status: "acknowledged" });
        this.snapshot(true);
        this.bump(true);
        return;
      }
    }
    if (cmd.cmd === "play" || cmd.cmd === "jump" || cmd.cmd === "restart") { this.d.playing = true; this.d.pausedOn = null; }
    if (cmd.cmd === "pause") this.d.playing = false;
    if (cmd.cmd === "speed") this.d.speed = Number(cmd.value);
    this.ws?.readyState === 1 && this.ws.send(JSON.stringify(cmd));
    this.bump(true);
  }
}

const store = new LiveStore();
const Ctx = createContext<LiveStore>(store);

export function LiveProvider({ children }: { children: ReactNode }) {
  useEffect(() => { store.connect(); }, []);
  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useLive(): [LiveData, (c: Record<string, unknown>) => void, (m: StreamMode) => void] {
  const s = useContext(Ctx);
  const [, setV] = useState(0);
  const ref = useRef(s);
  useEffect(() => {
    const f = () => setV((v) => v + 1);
    ref.current.subs.add(f);
    return () => { ref.current.subs.delete(f); };
  }, []);
  return [s.d, (c) => s.send(c), (m) => s.setMode(m)];
}
