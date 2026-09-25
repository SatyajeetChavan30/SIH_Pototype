// Hazard identity colors: validated categorical palette (dark steps), fixed order, never cycled.
// Originally validated against dark surface #141a21; all hues are mid-dark and remain legible on the white theme.
export const HAZARD_COLOR: Record<string, string> = {
  LOSS: "#3987e5",
  KICK: "#d95926",
  STUCK: "#199e70",
  TIGHT: "#c98500",
  INSTAB: "#d55181",
  TORQUE: "#008300",
  CEMENT: "#9085e9",
  FISH: "#e66767",
};
export const HAZARD_SHORT: Record<string, string> = {
  LOSS: "Losses", KICK: "Kick/OP", STUCK: "Stuck", TIGHT: "Tight/Ball", INSTAB: "Instab.", TORQUE: "Torque", CEMENT: "Cement", FISH: "Fishing",
};
export const HAZARD_ABBR: Record<string, string> = {
  LOSS: "LOS", KICK: "KCK", STUCK: "STK", TIGHT: "TGT", INSTAB: "INS", TORQUE: "TRQ", CEMENT: "CMT", FISH: "FSH",
};
// Reserved status colors (always shown with icon + label, never color alone)
export const LEVEL: Record<string, { color: string; icon: string; label: string }> = {
  critical: { color: "#d03b3b", icon: "▲", label: "CRITICAL" },
  warning: { color: "#ec835a", icon: "◆", label: "WARNING" },
  watch: { color: "#b27a00", icon: "●", label: "WATCH" },
  info: { color: "#6b6b6b", icon: "i", label: "INFO" },
};

// Sequential risk ramp: one hue (orange), receding to the surface at 0
const SURF = [0xf5, 0xf5, 0xf5];
const HOT = [0xf0, 0x7a, 0x3c];
export function riskColor(p: number): string {
  const t = Math.min(1, Math.sqrt(Math.max(0, p) / 0.8));
  const c = SURF.map((s, i) => Math.round(s + (HOT[i] - s) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export const fmt = {
  m: (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString("en-IN")} m`),
  n0: (v: number | null | undefined) => (v == null ? "–" : Math.round(v).toLocaleString("en-IN")),
  n1: (v: number | null | undefined) => (v == null ? "–" : v.toFixed(1)),
  n2: (v: number | null | undefined) => (v == null ? "–" : v.toFixed(2)),
  pct: (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`),
  hours: (t: number) => {
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
    return `${h}h ${String(m).padStart(2, "0")}m`;
  },
};
