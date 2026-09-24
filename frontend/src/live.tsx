import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { wsUrl } from "./api";
import type { Alert, Episode, LiveStatus, RibbonBin, Sample, TopPred, Zone } from "./types";

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
  events: { type: string; message: string; md: number; t: number }[];
  sections: any[];
  version: number;
}

const MAX_SAMPLES = 480;

class LiveStore {
  d: LiveData = { connected: false, playing: true, speed: 4, status: null, samples: [], alerts: new Map(), zones: [], tops: {},
    window: {}, grid: [], episodes: [], ribbon: [], events: [], sections: [], version: 0 };
  ws: WebSocket | null = null;
  subs = new Set<() => void>();
  timer: number | null = null;
  retry: number | null = null;

  connect() {
    if (this.ws) return;
    const ws = new WebSocket(wsUrl("/ws/live"));
    this.ws = ws;
    ws.onopen = () => { this.d.connected = true; this.bump(true); };
    ws.onclose = () => {
      this.d.connected = false; this.ws = null; this.bump(true);
      this.retry = window.setTimeout(() => this.connect(), 2500);
    };
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data));
  }
  onMessage(m: any) {
    const d = this.d;
    if (m.type === "init") {
      d.samples = []; d.alerts = new Map(); d.events = [];
      d.episodes = m.episodes; d.zones = m.zones; d.tops = m.tops; d.window = m.window; d.grid = m.grid; d.ribbon = m.ribbon;
      d.sections = m.sections; d.status = m.status;
      for (const a of m.alerts as Alert[]) d.alerts.set(a.key, a);
    } else if (m.type === "tick") {
      if (m.samples?.length) {
        d.samples = d.samples.concat(m.samples);
        if (d.samples.length > MAX_SAMPLES) d.samples = d.samples.slice(d.samples.length - MAX_SAMPLES);
      }
      for (const a of m.alerts as Alert[]) d.alerts.set(a.key, a);
      if (m.status) d.status = m.status;
      if (m.events?.length) d.events = [...m.events, ...d.events].slice(0, 30);
      if (m.ribbon) d.ribbon = m.ribbon;
      if (m.zones) d.zones = m.zones;
      if (m.tops) d.tops = m.tops;
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
    if (cmd.cmd === "play") this.d.playing = true;
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

export function useLive(): [LiveData, (c: Record<string, unknown>) => void] {
  const s = useContext(Ctx);
  const [, setV] = useState(0);
  const ref = useRef(s);
  useEffect(() => {
    const f = () => setV((v) => v + 1);
    ref.current.subs.add(f);
    return () => { ref.current.subs.delete(f); };
  }, []);
  return [s.d, (c) => s.send(c)];
}
