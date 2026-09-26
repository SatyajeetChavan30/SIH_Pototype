export type Hazard = "LOSS" | "KICK" | "STUCK" | "TIGHT" | "INSTAB" | "TORQUE" | "CEMENT" | "FISH";

export interface Formation { code: string; name: string; age: string; lithology: string; color: string }
export interface HazardDef { code: Hazard; label: string; color: string; description: string; in_ribbon: boolean }
export interface Meta {
  ontology: { region?: Region; formations: Formation[]; hazards: HazardDef[]; mitigations: { code: string; hazard: string; label: string; preventive: string }[] };
  structures: { id: string; name: string; lat: number; lon: number; prod_start: number }[];
  active_well: string;
  ocr: { available: boolean; engine: string | null };
  llm: { backend: string; model: string | null };
  asr?: { available: boolean; model: string | null; hint: string | null };
  top_pick_mode?: string;
  stream?: { live_available: boolean; spec: string; describe: string };
  map_tiles?: { url: string; attribution: string };
  synthetic?: boolean;
  boot?: string;
  datasets?: { current: string; locked: boolean; options: { code: string; label: string; synthetic: boolean; built: boolean; stream?: boolean }[] };
  stream_source?: { source: string; wellbore: string; attribution: string; url: string; derived: string[]; logs: string[]; incidents?: number;
    window: { start: string; end: string; hours: number; md_from: number; md_to: number } } | null;
  build: { seed: number; seconds: number; n_wells: number; n_docs: number; ocr_pages: number } | null;
  formation_order: string[];
  ribbon_hazards: Hazard[];
}

export interface Citation { doc_id: string; page_no: number; start: number; end: number; text: string; title?: string; kind?: string }
export interface Action { code: string; success: boolean; text?: string }
export interface WellEvent {
  id: string; well_id: string; hazard: Hazard; subtype: string; md: number | null; tvd: number | null; formation: string | null;
  rel: number | null; severity: string; rate_bbl_hr: number | null; npt_hours: number | null; mw_ppg: number | null;
  ecd_ppg: number | null; cause: string | null; date: string | null; confidence: number; status: string; summary: string;
  actions: Action[]; citations: Citation[];
}
export interface WellSummary {
  id: string; name: string; structure_id: string; lat: number; lon: number; spud_year: number; td_md: number; td_tvd: number;
  status: string; traj_type: string; target: string; mud_system: string; is_active: boolean; hazard_counts: Record<string, number>;
  n_events: number; npt_hours: number; trajectory?: [number, number][]; distance_km?: number; same_structure?: boolean;
}
export interface Section { idx: number; hole: string; casing: string; top_md: number; shoe_md: number; mw_ppg: number; ecd_ppg: number }
export interface TopPred { tvd: number; md: number; sd: number; n: number; picked?: boolean; shifted_m?: number }

export interface Evidence {
  well_id: string; distance_km: number; weight: number; event_id: string; md: number; rel: number; summary: string;
  projected_md: number; spud_year: number; citation: Citation | null;
}
export interface Driver { factor?: string; delta?: number; channel?: string; value?: number | string; baseline?: number | null; unit?: string }
export interface Zone {
  hazard: Hazard; md0: number; md1: number; formation: string; peak: number; p_offsets: number; p_model: number; lo: number; hi: number;
  n_exposed: number; n_events: number; evidence?: Evidence[]; drivers?: Driver[]; distance_m?: number;
}
export interface HazardCell { p: number; lo: number; hi: number; prior: number; n_eff: number; n_exposed: number; n_events: number; ml?: number; final: number; evidence: Evidence[]; drivers?: Driver[] }
export interface Bin {
  md0: number; md1: number; tvd0: number; tvd1: number; formation: string; rel0: number; rel1: number; inc: number; mw: number; ecd: number;
  section: string; hazards: Record<string, HazardCell>;
}
export interface Profile {
  target: { id: string | null; name: string; lat: number; lon: number; td_md: number; structure_id: string | null; spud_year: number; sections: Section[] };
  radius_km: number; tops: Record<string, TopPred>; bins: Bin[]; zones: Zone[];
  formation_summary: Record<string, Record<string, { k: number; n: number; p: number; wells: string[] }>>;
  offsets: { well_id: string; distance_km: number; weight: number; structure_id: string; spud_year: number; n_events: number }[];
}

export interface Recommendation {
  hazard: string; formation: string | null; scope: string; n_events: number;
  actions: { code: string; label: string; attempts: number; cured: number; cure_rate: number; first_try: string; median_npt_h: number | null;
    verdict: "recommended" | "mixed" | "avoid"; wells: string[]; preventive: string;
    cure_rate_smoothed?: number; cure_rate_adjusted?: number; confounded?: boolean;
    adjustment?: { severity: string; attempt: string; n: number; k: number; pooled_rate: number }[] }[];
  preventive: { code: string; text: string; support: number | null }[];
  lessons: { id: string; well_id: string; text: string; doc_id: string; page_no: number; start: number; end: number; title?: string }[];
}
export interface Analog { well_id: string; md: number; formation: string; similarity: number; next: { id: string; hazard: Hazard; md: number; summary: string; citation: Citation | null }[] }

export interface Alert {
  id: string; key: string; hazard: string; source: "look-ahead" | "real-time" | "mud-window" | "fused" | "geology"; level: "info" | "watch" | "warning" | "critical";
  title: string; message: string; md: number; t: number; formation: string | null; confidence: number; drivers: Driver[]; evidence: Evidence[];
  recommendations: Recommendation | null; analogs: Analog[]; zone: Zone | null; corroborated: boolean; status: "active" | "acknowledged" | "cleared";
  updated_t: number; count: number; history: { t: number; md: number; level: string; event: string; title?: string }[];
  p_value?: number | null; calibrated?: boolean;
}
export interface Sample { t: number; md: number; tvd: number; gr: number; rop: number; wob: number; rpm: number; torque: number; spp: number; flow_in: number; flow_out: number; pit: number; hookload: number; gas: number; mw: number; ecd: number; dxc: number; state: number; formation?: string;
  exp_torque?: number; exp_spp?: number; exp_hookload?: number; exp_ecd?: number }
export interface LiveStatus {
  i: number; n: number; t: number; md: number; tvd: number; formation: string; rel: number;
  next_top: { formation: string; md: number; tvd: number; sd: number; distance_m: number } | null;
  mw: number; ecd: number; window: { min_mw: number | null; max_ecd: number | null } | null; picked: Record<string, number>;
  zones_ahead: Zone[]; progress: number; episode: string | null;
  alert_load?: AlertLoad; budget?: BudgetState; session_id?: string; digest?: DigestItem[];
  mode?: "live" | "replay"; waiting?: boolean; top_pick_mode?: string; stream?: StreamStats | null;
}
export interface StreamStats { kind: string; describe: string; connected: boolean; peer: string | null; packets: number; errors: number;
  reconnects: number; last_packet_age_s: number | null; last_error: string | null; derived: string[]; dropped: number }
export interface AlertLoad { window_h: number; opened: number; non_critical: number; per_hour: number; non_critical_per_hour: number; budget_per_hour: number; within_budget: boolean }
export interface BudgetState { budget_per_hour: number; alpha: number; calibrated: Record<string, boolean>; reservoir: Record<string, number>; suppressed: Record<string, number>; passed: Record<string, number> }
export interface DigestItem { t: number; md: number; hazard: string; detector: string; level: string; title: string; message: string; p_value: number | null; reason: string }
export interface LiveEvent { type: string; message: string; md: number; t: number; source?: string; conflict?: boolean; formation?: string }
export interface AuditRow { seq: number; ts_wall: string; session_id: string | null; well_id: string | null; t: number | null; md: number | null;
  alert_id: string | null; alert_key: string | null; hazard: string | null; event: string; level: string | null; actor: string; payload: Record<string, any>; hash: string }
export interface MemoSection { heading: string; items: string[] }
export interface NumberedCitation extends Citation { n: number; label: string }
export interface Brief { title: string; sections: MemoSection[]; citations: NumberedCitation[]; summary: string | null; mode: string;
  draft_lesson?: string; status?: string; event_id?: string; period?: { t0: number; t1: number; md0: number; md1: number } }
export interface Episode { id: string; hazard: string; label: string; md: number; onset_md?: number }
export interface RibbonBin { md0: number; md1: number; formation: string; risk: Record<string, number> }
export interface Region { code: string; label: string; synthetic: boolean; data_notice: string; default_td: string;
  search_examples: string[]; ask_examples: string[]; ask_default: string; default_formation: string; default_radius_km?: number }
