"""Deterministic synthetic Upper-Assam offset-well world.

Everything here is SYNTHETIC but calibrated to published geology of the Upper
Assam Shelf (see docs/RESEARCH.md): stratigraphy, a regional dip towards the
Naga/Schuppen thrust front, anticlinal structures, Tipam depletion that grows
with production age, Girujan reactive clays, Barail coal/shale instability and
thrust-proximal overpressure, and Sylhet fractured-limestone losses.

Hazards are generated from *latent physical fields* (pore pressure, loss
gradient, collapse gradient) and the mud actually used, so the analytics can
be scored against a known truth (e.g. the mud-weight window must recover the
latent loss gradient).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np

from ..domain.ontology import FORMATION_ORDER, MITIGATION_BY_CODE
from ..geo import Trajectory, haversine_km, offset_latlon

# ---------------------------------------------------------------------------
# Structures (fictitious names, real basin geography)
# ---------------------------------------------------------------------------


@dataclass
class Structure:
    id: str
    name: str
    lat: float
    lon: float
    relief_m: float        # structural uplift at crest
    sigma_km: float        # structural width
    prod_start: int        # Tipam production start year -> depletion
    fracture: float        # Sylhet natural-fracture intensity 0..1
    thief_rel: float       # relative position of the Tipam thief sand
    n_wells: int
    prefix: str
    dev_start: int = 1990  # start of development drilling


STRUCTURES = [
    Structure("DKH", "Dikhow Dome", 27.475, 95.020, 230, 3.2, 1962, 0.25, 0.32, 11, "DKH", 1990),
    Structure("BDA", "Burhi Dihing Anticline", 27.395, 95.245, 260, 3.6, 1968, 0.35, 0.40, 12, "BDA", 1990),
    Structure("TGP", "Tengapani Structure", 27.300, 95.470, 180, 3.0, 1978, 0.85, 0.36, 12, "TGP", 1994),
    Structure("NDH", "Namdang High", 27.360, 95.650, 150, 2.8, 1980, 0.60, 0.34, 15, "NDH", 1997),
    Structure("SSN", "Sessa Nose", 27.560, 95.400, 120, 2.6, 2008, 0.30, 0.45, 9, "SSN", 2006),
]
STRUCT_BY_ID = {s.id: s for s in STRUCTURES}

# Regional reference tops (TVD m) at the NW end of the area, before dip and uplift.
REGIONAL_TOPS = {
    "ALLUVIUM": 0.0, "DHEKIAJULI": 240.0, "NAMSANG": 820.0, "GIRUJAN": 1180.0, "TIPAM": 1820.0,
    "BARAIL": 2480.0, "KOPILI": 3120.0, "SYLHET": 3380.0, "LANGPAR": 3700.0, "BASEMENT": 4060.0,
}
# Fraction of the SE dip applied to each top (deeper tops dip more).
DIP_FACTOR = {"ALLUVIUM": 0, "DHEKIAJULI": 0.2, "NAMSANG": 0.45, "GIRUJAN": 0.6, "TIPAM": 0.75,
              "BARAIL": 0.9, "KOPILI": 1.0, "SYLHET": 1.0, "LANGPAR": 1.0, "BASEMENT": 1.0}

# Naga thrust front approximated as a line; wells SE of the area are closer to it.
THRUST_A = (27.02, 95.35)
THRUST_B = (27.62, 96.05)


def thrust_distance_km(lat: float, lon: float) -> float:
    """Perpendicular distance (km) from the thrust-front line (always >= 0 in the area)."""
    ky = 111.32
    kx = 111.32 * math.cos(math.radians(27.35))
    ax, ay = THRUST_A[1] * kx, THRUST_A[0] * ky
    bx, by = THRUST_B[1] * kx, THRUST_B[0] * ky
    px, py = lon * kx, lat * ky
    num = abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax)
    return num / math.hypot(by - ay, bx - ax)


def se_trend(lat: float, lon: float) -> float:
    """0 (NW, far from thrust) .. 1 (SE, near thrust)."""
    d = thrust_distance_km(lat, lon)
    return float(np.clip(1 - (d - 5) / 45, 0, 1))


def structural_uplift(lat: float, lon: float) -> float:
    up = 0.0
    for s in STRUCTURES:
        d = float(haversine_km(lat, lon, s.lat, s.lon))
        up += s.relief_m * math.exp(-(d ** 2) / (2 * s.sigma_km ** 2))
    return up


def nearest_structure(lat: float, lon: float) -> Structure:
    return min(STRUCTURES, key=lambda s: float(haversine_km(lat, lon, s.lat, s.lon)))


def true_tops_tvd(lat: float, lon: float, rng: np.random.Generator | None = None) -> dict[str, float]:
    """Latent formation tops (TVD, m) at a location."""
    t = se_trend(lat, lon)
    up = structural_uplift(lat, lon)
    tops = {}
    prev = -1e9
    for code in FORMATION_ORDER:
        base = REGIONAL_TOPS[code]
        if code == "ALLUVIUM":
            tops[code] = 0.0
            prev = 0.0
            continue
        z = base + 420 * DIP_FACTOR[code] * t - up * (0.3 + 0.7 * DIP_FACTOR[code])
        # gentle long-wavelength undulation so tops are not a perfect plane
        z += 25 * math.sin(lat * 37.0) * math.cos(lon * 23.0) * DIP_FACTOR[code]
        if rng is not None:
            z += rng.normal(0, 6 + 8 * DIP_FACTOR[code])
        z = max(z, prev + 60)
        tops[code] = round(z, 1)
        prev = z
    return tops


# ---------------------------------------------------------------------------
# Latent pressure / strength fields (ppg EMW)
# ---------------------------------------------------------------------------

def depletion_ppg(struct: Structure, year: int) -> float:
    return 2.4 * float(np.clip((year - struct.prod_start) / 40.0, 0, 1))


def overpressure_amp(lat: float, lon: float) -> float:
    d = thrust_distance_km(lat, lon)
    return 3.0 * math.exp(-max(d - 4, 0) / 9.0)


def pore_pressure(code: str, rel: float, lat: float, lon: float) -> float:
    amp = overpressure_amp(lat, lon)
    if code == "BARAIL":
        # overpressure ramps up in the lower half of Barail (sub-thrust style)
        s = float(np.clip((rel - 0.45) / 0.45, 0, 1))
        return 8.9 + amp * s * s * (3 - 2 * s)
    if code == "KOPILI":
        return 8.9 + amp
    if code == "SYLHET":
        return 9.2 + 0.55 * amp
    if code == "LANGPAR":
        return 9.5 + 0.6 * amp
    if code in ("TIPAM",):
        return 8.8
    return 8.7


def loss_gradient(code: str, lat: float, lon: float, struct: Structure, year: int) -> float:
    """ECD (ppg) above which the formation takes mud."""
    if code == "NAMSANG":
        return 10.3
    if code == "TIPAM":
        return 12.4 - depletion_ppg(struct, year)
    if code == "SYLHET":
        return 11.3 - 0.9 * struct.fracture
    if code in ("ALLUVIUM", "DHEKIAJULI"):
        return 11.0
    if code == "GIRUJAN":
        return 14.0
    if code == "BARAIL":
        return 13.8 + 0.3 * overpressure_amp(lat, lon)
    if code == "KOPILI":
        return 14.2 + 0.3 * overpressure_amp(lat, lon)
    return 14.5


def collapse_gradient(code: str, inc_deg: float) -> float:
    if code == "BARAIL":
        return 9.7 + 0.03 * inc_deg
    if code == "KOPILI":
        return 10.0 + 0.025 * inc_deg
    return 8.0


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


# ---------------------------------------------------------------------------
# Mitigation efficacy model (latent truth the recommender should rediscover)
# ---------------------------------------------------------------------------
EFFICACY = {
    ("LOSS", "depleted sand"): {"LCM_FINE": 0.78, "LCM_COARSE": 0.5, "REDUCE_FLOW": 0.5, "CEMENT_PLUG": 0.9, "POOH_HEAL": 0.3},
    ("LOSS", "natural fractures"): {"LCM_FINE": 0.12, "LCM_COARSE": 0.6, "REDUCE_FLOW": 0.15, "CEMENT_PLUG": 0.88, "POOH_HEAL": 0.2},
    ("LOSS", "unconsolidated formation"): {"LCM_FINE": 0.82, "LCM_COARSE": 0.6, "REDUCE_FLOW": 0.55, "POOH_HEAL": 0.5},
    ("LOSS", "induced fracture (high ECD)"): {"REDUCE_FLOW": 0.72, "LCM_FINE": 0.6, "POOH_HEAL": 0.6, "LCM_COARSE": 0.45},
    ("KICK", "underbalance in overpressured zone"): {"SHUT_IN_DM": 0.9, "WAIT_WEIGHT": 0.96},
    ("KICK", "gas-bearing sand"): {"FLOW_CHECK": 0.75, "RAISE_MW": 0.8},
    ("STUCK", "differential sticking"): {"SPOT_PIPE_LAX": 0.72, "JAR_UP": 0.22, "JAR_DOWN": 0.3, "CIRC_HIVIS": 0.1},
    ("STUCK", "pack-off / poor hole cleaning"): {"CIRC_HIVIS": 0.62, "JAR_DOWN": 0.45, "JAR_UP": 0.18, "SPOT_PIPE_LAX": 0.1},
    ("STUCK", "mechanical (coal/ledges)"): {"JAR_DOWN": 0.58, "JAR_UP": 0.38, "CIRC_HIVIS": 0.2},
    ("TIGHT", "reactive clay swelling"): {"INHIBITION": 0.72, "BACKREAM": 0.6, "BIT_CLEAN": 0.4},
    ("TIGHT", "bit balling"): {"BIT_CLEAN": 0.75, "INHIBITION": 0.55, "BACKREAM": 0.35},
    ("INSTAB", "shear failure (MW below collapse)"): {"RAISE_MW_STAB": 0.8, "ASPHALT": 0.45, "CONTROL_ROP": 0.4},
    ("INSTAB", "coal cleat failure"): {"ASPHALT": 0.72, "RAISE_MW_STAB": 0.5, "CONTROL_ROP": 0.5},
    ("TORQUE", "coal stringers"): {"REDUCE_PARAMS": 0.65, "LUBRICANT": 0.5},
    ("TORQUE", "high dogleg / tortuosity"): {"LUBRICANT": 0.75, "REDUCE_PARAMS": 0.55},
    ("CEMENT", "losses into depleted zone"): {"SQUEEZE": 0.75, "TOP_JOB": 0.6},
    ("FISH", "stuck pipe not freed"): {"OVERSHOT": 0.65, "SPEAR": 0.5, "SIDETRACK": 1.0},
}
# What crews tend to try first (habit), independent of what works.
HABIT = {"LCM_FINE": 3.0, "REDUCE_FLOW": 1.5, "LCM_COARSE": 1.2, "POOH_HEAL": 0.8, "CEMENT_PLUG": 0.3,
         "SHUT_IN_DM": 2.0, "WAIT_WEIGHT": 1.0, "FLOW_CHECK": 2.0, "RAISE_MW": 1.0,
         "JAR_UP": 2.0, "JAR_DOWN": 1.6, "SPOT_PIPE_LAX": 1.0, "CIRC_HIVIS": 1.2,
         "BACKREAM": 2.0, "INHIBITION": 1.0, "BIT_CLEAN": 1.0,
         "RAISE_MW_STAB": 1.0, "ASPHALT": 1.0, "CONTROL_ROP": 1.2,
         "LUBRICANT": 1.0, "REDUCE_PARAMS": 1.5, "SQUEEZE": 1.5, "TOP_JOB": 1.0,
         "OVERSHOT": 2.0, "SPEAR": 1.0, "SIDETRACK": 0.05}
NPT_RANGE = {"LCM_FINE": (1.5, 4), "LCM_COARSE": (2, 5), "REDUCE_FLOW": (0.5, 1.5), "CEMENT_PLUG": (12, 20),
             "POOH_HEAL": (3, 7), "SHUT_IN_DM": (8, 16), "WAIT_WEIGHT": (10, 20), "FLOW_CHECK": (1, 3),
             "RAISE_MW": (1.5, 4), "JAR_UP": (2, 6), "JAR_DOWN": (2, 6), "SPOT_PIPE_LAX": (6, 14),
             "CIRC_HIVIS": (1.5, 4), "BACKOFF": (6, 10), "BACKREAM": (2, 6), "INHIBITION": (1, 3),
             "BIT_CLEAN": (1, 2.5), "RAISE_MW_STAB": (2, 5), "ASPHALT": (1.5, 4), "CONTROL_ROP": (2, 6),
             "LUBRICANT": (0.5, 2), "REDUCE_PARAMS": (0.5, 1.5), "SQUEEZE": (14, 30), "TOP_JOB": (6, 12),
             "OVERSHOT": (12, 36), "SPEAR": (12, 30), "SIDETRACK": (72, 140)}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TruthEvent:
    id: str
    well_id: str
    hazard: str
    subtype: str
    cause: str
    formation: str
    rel: float
    md: float
    tvd: float
    severity: str
    rate_bbl_hr: float | None
    volume_bbl: float | None
    mw_ppg: float
    ecd_ppg: float
    attempts: list[dict]          # [{code, success, npt}]
    npt_hours: float
    day: int = 0
    date: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Well:
    id: str
    name: str
    structure_id: str
    lat: float
    lon: float
    spud_year: int
    spud_date: str
    traj_type: str
    survey_md: list[float]
    survey_inc: list[float]
    survey_azi: list[float]
    tops_tvd: dict[str, float]
    tops_md: dict[str, float]
    td_md: float
    td_tvd: float
    target: str
    mud_system: str
    sections: list[dict]          # hole/casing/mud per section
    unit_system: str
    status: str
    is_active: bool = False
    events: list[TruthEvent] = field(default_factory=list)
    days: list[dict] = field(default_factory=list)
    lessons: list[dict] = field(default_factory=list)
    scanned_wcr: bool = False

    def trajectory(self) -> Trajectory:
        return Trajectory(self.survey_md, self.survey_inc, self.survey_azi)


@dataclass
class World:
    wells: list[Well]
    active: Well
    seed: int


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def sample_traj_params(rng, traj_type: str, tops_tvd: dict[str, float]) -> dict:
    p = {"type": traj_type, "azi0": float(rng.uniform(0, 360))}
    if traj_type == "vertical":
        p["noise_seed"] = int(rng.integers(0, 2**31))
    elif traj_type == "J":
        p.update(kop=float(rng.uniform(600, 1100)), inc_max=float(rng.uniform(22, 40)), br=float(rng.uniform(2.0, 3.0)))
    else:
        inc_max = float(rng.uniform(18, 30))
        p.update(kop=float(rng.uniform(600, 900)), inc_max=inc_max, br=2.5,
                 drop_md=(tops_tvd["TIPAM"] - 100) / math.cos(math.radians(inc_max * 0.8)))
    return p


def make_survey(p: dict, td_md: float):
    step = 30.0
    md = np.arange(0, td_md + step, step)
    md[-1] = td_md
    azi = np.full_like(md, p["azi0"])
    if p["type"] == "vertical":
        r = np.random.default_rng(p["noise_seed"])
        inc = np.clip(np.abs(r.normal(0.6, 0.4, len(md))), 0, 2.0)
        inc[0] = 0.0
        azi = (p["azi0"] + np.cumsum(r.normal(0, 8, len(md)))) % 360
    elif p["type"] == "J":
        inc = np.clip((md - p["kop"]) / 30.0 * p["br"], 0, p["inc_max"])
    else:
        build = np.clip((md - p["kop"]) / 30.0 * p["br"], 0, p["inc_max"])
        drop = np.clip((md - p["drop_md"]) / 30.0 * 2.0, 0, p["inc_max"] - 4)
        inc = np.maximum(build - drop, np.where(md > p["kop"], 4.0, 0.0))
    return md.round(1).tolist(), np.round(inc, 2).tolist(), np.round(azi, 1).tolist()


def mud_system_for(rng, year: int) -> str:
    if year < 2000:
        return str(rng.choice(["WBM (lignosulphonate)", "KCl-PHPA"], p=[0.75, 0.25]))
    if year < 2012:
        return str(rng.choice(["WBM (lignosulphonate)", "KCl-PHPA", "KCl-PHPA-Glycol"], p=[0.3, 0.45, 0.25]))
    return str(rng.choice(["KCl-PHPA", "KCl-PHPA-Glycol", "SOBM"], p=[0.2, 0.6, 0.2]))


TIGHT_P = {"WBM (lignosulphonate)": 0.62, "KCl-PHPA": 0.32, "KCl-PHPA-Glycol": 0.16, "SOBM": 0.07}


def plan_sections(rng, w_tops_tvd, traj: Trajectory, td_md, year, lat, lon, struct: Structure):
    """Casing points and mud weights following regional practice (with human variability)."""
    near_thrust = overpressure_amp(lat, lon)
    aware = year >= 2006  # overpressure knowledge spread after mid-2000s
    s1_shoe_tvd = w_tops_tvd["GIRUJAN"] - float(rng.uniform(20, 60))
    s2_shoe_tvd = w_tops_tvd["BARAIL"] - float(rng.uniform(8, 20))  # 9-5/8" in Tipam-bottom shale
    cond_md = float(rng.uniform(60, 110))
    s1_md = float(traj.md_at_tvd(s1_shoe_tvd))
    s2_md = float(traj.md_at_tvd(s2_shoe_tvd))
    mw1 = round(float(rng.normal(9.15, 0.12)), 2)
    # 12-1/4" MW: engineers learned to lower MW over depleted Tipam in later years (partially)
    mw2 = float(rng.normal(9.95, 0.28))
    if year > 2012 and struct.prod_start < 1990:
        mw2 -= 0.2
    mw2 = round(mw2, 2)
    if aware:
        mw3 = 10.3 + 0.55 * near_thrust + float(rng.normal(0, 0.35))
    else:
        mw3 = 10.3 + 0.15 * near_thrust + float(rng.normal(0, 0.3))
    mw3 = round(float(np.clip(mw3, 9.8, 12.6)), 2)
    sections = [
        {"hole": "26\"", "casing": "20\"", "top_md": 0.0, "shoe_md": round(cond_md, 1), "mw_ppg": 8.9, "ecd_add": 0.1},
        {"hole": "17-1/2\"", "casing": "13-3/8\"", "top_md": round(cond_md, 1), "shoe_md": round(s1_md, 1), "mw_ppg": mw1,
         "ecd_add": round(float(rng.uniform(0.2, 0.35)), 2)},
        {"hole": "12-1/4\"", "casing": "9-5/8\"", "top_md": round(s1_md, 1), "shoe_md": round(s2_md, 1), "mw_ppg": mw2,
         "ecd_add": round(float(rng.uniform(0.3, 0.55)), 2)},
        {"hole": "8-1/2\"", "casing": "7\" liner", "top_md": round(s2_md, 1), "shoe_md": round(td_md, 1), "mw_ppg": mw3,
         "ecd_add": round(float(rng.uniform(0.35, 0.6)), 2)},
    ]
    for s in sections:
        s["ecd_ppg"] = round(s["mw_ppg"] + s["ecd_add"], 2)
    return sections


def section_at(sections, md):
    for s in sections:
        if s["top_md"] <= md <= s["shoe_md"] + 1e-6:
            return s
    return sections[-1]


def choose_attempts(rng, hazard: str, cause: str):
    eff = EFFICACY.get((hazard, cause))
    if not eff:
        return [], 0.0, True
    codes = list(eff.keys())
    attempts = []
    npt = 0.0
    tried = set()
    for _ in range(4):
        cands = [c for c in codes if c not in tried]
        if not cands:
            break
        w = np.array([HABIT.get(c, 1.0) for c in cands])
        c = str(rng.choice(cands, p=w / w.sum()))
        tried.add(c)
        ok = bool(rng.random() < eff[c])
        lo, hi = NPT_RANGE[c]
        h = round(float(rng.uniform(lo, hi)), 1)
        npt += h
        attempts.append({"code": c, "success": ok, "npt": h})
        if ok:
            return attempts, round(npt, 1), True
    return attempts, round(npt, 1), False


def rel_to_md(tops_tvd, code, rel, traj: Trajectory, td_tvd):
    idx = FORMATION_ORDER.index(code)
    top = tops_tvd[code]
    base = tops_tvd[FORMATION_ORDER[idx + 1]] if idx + 1 < len(FORMATION_ORDER) else top + 400
    tvd = top + rel * (base - top)
    tvd = min(tvd, td_tvd - 2)
    return float(traj.md_at_tvd(tvd)), float(tvd)


def formation_span(tops_tvd, code, td_tvd):
    """Fraction (rel range) of a formation penetrated by a well of TD td_tvd."""
    idx = FORMATION_ORDER.index(code)
    top = tops_tvd[code]
    base = tops_tvd[FORMATION_ORDER[idx + 1]] if idx + 1 < len(FORMATION_ORDER) else top + 400
    if td_tvd <= top:
        return 0.0
    return float(np.clip((td_tvd - top) / (base - top), 0, 1))


def generate_events(rng, well: Well, struct: Structure, traj: Trajectory) -> list[TruthEvent]:
    ev: list[TruthEvent] = []
    T = getattr(well, "tops_tvd_full", well.tops_tvd)
    year = well.spud_year
    n = 0

    def add(hazard, subtype, cause, code, rel, severity, rate=None, vol=None, extra=None, force_success=None):
        nonlocal n
        span = formation_span(T, code, well.td_tvd)
        if span <= 0 or rel > span:
            return None
        md, tvd = rel_to_md(T, code, rel, traj, well.td_tvd)
        sec = section_at(well.sections, md)
        attempts, npt, ok = choose_attempts(rng, hazard, cause)
        n += 1
        e = TruthEvent(f"{well.id}-E{n:02d}", well.id, hazard, subtype, cause, code, round(rel, 3), round(md, 1),
                       round(tvd, 1), severity, rate, vol, sec["mw_ppg"], sec["ecd_ppg"], attempts, npt,
                       extra=extra or {})
        e.extra["resolved"] = ok
        ev.append(e)
        return e

    inc_at = lambda code, rel: float(traj.inc_at_md(rel_to_md(T, code, rel, traj, well.td_tvd)[0]))

    # --- Namsang seepage (unconsolidated)
    if rng.random() < 0.14:
        add("LOSS", "seepage", "unconsolidated formation", "NAMSANG", float(rng.uniform(0.2, 0.8)), "seepage",
            rate=round(float(rng.uniform(4, 12)), 0))

    # --- Girujan reactive clay: tight hole / balling, pack-off
    if rng.random() < TIGHT_P[well.mud_system]:
        sub = str(rng.choice(["bit balling", "tight hole"], p=[0.45, 0.55]))
        cause = "bit balling" if sub == "bit balling" else "reactive clay swelling"
        add("TIGHT", sub, cause, "GIRUJAN", float(rng.uniform(0.3, 0.9)), "moderate",
            extra={"overpull_klbs": round(float(rng.uniform(25, 60)), 0)})
    p_pack = 0.16 if well.mud_system.startswith("WBM") else 0.05
    if rng.random() < p_pack + (0.05 if inc_at("GIRUJAN", 0.5) > 25 else 0):
        e = add("STUCK", "pack-off", "pack-off / poor hole cleaning", "GIRUJAN", float(rng.uniform(0.4, 0.95)), "major")
        if e and not e.extra["resolved"]:
            _add_fish(rng, well, e, ev)

    # --- Tipam: depletion-driven losses, differential sticking
    sec2 = section_at(well.sections, rel_to_md(T, "TIPAM", 0.4, traj, well.td_tvd)[0])
    lg = loss_gradient("TIPAM", well.lat, well.lon, struct, year)
    ecd = sec2["ecd_ppg"] + float(rng.normal(0, 0.08))
    p_loss = float(sigmoid((ecd - lg) / 0.2)) * 0.92
    if rng.random() < p_loss:
        ob = ecd - lg
        sev = "partial" if ob < 0.45 else "total"
        rate = round(float(rng.uniform(15, 70)) if sev == "partial" else float(rng.uniform(120, 250)), 0)
        cause = "depleted sand" if depletion_ppg(struct, year) > 0.8 else "induced fracture (high ECD)"
        add("LOSS", sev, cause, "TIPAM", float(np.clip(rng.normal(struct.thief_rel, 0.04), 0.05, 0.95)), sev,
            rate=rate, extra={"loss_grad": round(lg, 2)})
    ob_psi = (sec2["mw_ppg"] - 8.8) * 0.052 * T["TIPAM"] * 3.28
    p_diff = float(sigmoid((ob_psi / 1000 + depletion_ppg(struct, year) * 0.5 - 1.6) / 0.35)) * 0.45
    if rng.random() < p_diff:
        e = add("STUCK", "differential", "differential sticking", "TIPAM",
                float(np.clip(rng.normal(struct.thief_rel + 0.05, 0.08), 0.05, 0.95)), "major")
        if e and not e.extra["resolved"]:
            _add_fish(rng, well, e, ev)

    # --- 9-5/8" cement job across Tipam
    cement_ecd = sec2["mw_ppg"] + (0.4 if (year >= 2015 and struct.prod_start < 1990 and rng.random() < 0.6) else 1.1)
    if rng.random() < float(sigmoid((cement_ecd - lg) / 0.25)) * 0.5:
        sub = str(rng.choice(["losses during cementing", "poor CBL"], p=[0.6, 0.4]))
        add("CEMENT", sub, "losses into depleted zone", "TIPAM", 0.97, "moderate")

    # --- Barail: overpressure/kicks, instability, torque, mechanical sticking
    span_b = formation_span(T, "BARAIL", well.td_tvd)
    if span_b > 0.2:
        sec3 = section_at(well.sections, rel_to_md(T, "BARAIL", min(span_b, 0.95), traj, well.td_tvd)[0])
        mw3 = sec3["mw_ppg"]
        rels = np.linspace(0.05, min(span_b, 0.98), 40)
        pps = np.array([pore_pressure("BARAIL", r, well.lat, well.lon) for r in rels])
        under = pps - mw3
        if under.max() > -0.35:
            i = int(np.argmax(under > -0.35))
            if under.max() > 0.0 and rng.random() < float(sigmoid(under.max() / 0.12)) * 0.95:
                j = int(np.argmax(under > 0))
                add("KICK", "kick", "underbalance in overpressured zone", "BARAIL", float(rels[j]),
                    "major" if under.max() > 0.4 else "moderate", vol=round(float(rng.uniform(6, 25)), 0),
                    extra={"pp_ppg": round(float(pps[j]), 2), "kill_mw": round(float(pps[j]) + 0.3, 2)})
            elif rng.random() < 0.65:
                add("KICK", "gas", "gas-bearing sand", "BARAIL", float(rels[i]), "minor",
                    extra={"gas_pct": round(float(rng.uniform(6, 18)), 1), "pp_ppg": round(float(pps[i]), 2)})
        rel_i = float(rng.uniform(0.35, 0.85))
        rel_i = min(rel_i, span_b)
        inc_i = inc_at("BARAIL", rel_i)
        col = collapse_gradient("BARAIL", inc_i)
        if rng.random() < float(sigmoid((col - mw3) / 0.18)) * 0.8 + 0.04:
            cause = "shear failure (MW below collapse)" if col > mw3 else "coal cleat failure"
            add("INSTAB", "cavings", cause, "BARAIL", rel_i, "moderate",
                extra={"collapse_ppg": round(col, 2)})
            if rng.random() < 0.3:
                e = add("STUCK", "mechanical", "mechanical (coal/ledges)", "BARAIL", min(rel_i + 0.03, span_b), "major")
                if e and not e.extra["resolved"]:
                    _add_fish(rng, well, e, ev)
        p_tq = 0.1 + 0.35 * float(np.clip((inc_at("BARAIL", 0.5) - 12) / 25, 0, 1)) + (0.08 if well.mud_system.startswith("WBM") else 0)
        if rng.random() < p_tq:
            cause = "high dogleg / tortuosity" if inc_at("BARAIL", 0.5) > 20 else "coal stringers"
            add("TORQUE", "stick-slip", cause, "BARAIL", float(rng.uniform(0.15, min(0.9, span_b))), "minor")

    # --- Kopili instability / overpressure
    span_k = formation_span(T, "KOPILI", well.td_tvd)
    if span_k > 0.1:
        sec4 = section_at(well.sections, rel_to_md(T, "KOPILI", min(span_k, 0.9), traj, well.td_tvd)[0])
        col = collapse_gradient("KOPILI", inc_at("KOPILI", 0.5))
        if rng.random() < float(sigmoid((col - sec4["mw_ppg"]) / 0.2)) * 0.6:
            add("INSTAB", "cavings", "shear failure (MW below collapse)", "KOPILI", float(rng.uniform(0.1, span_k)), "moderate")
        pp = pore_pressure("KOPILI", 0.5, well.lat, well.lon)
        if pp - sec4["mw_ppg"] > 0 and rng.random() < 0.7 and not any(e.hazard == "KICK" and e.subtype == "kick" for e in ev):
            add("KICK", "kick", "underbalance in overpressured zone", "KOPILI", float(rng.uniform(0.1, span_k)),
                "major", vol=round(float(rng.uniform(8, 30)), 0), extra={"pp_ppg": round(pp, 2), "kill_mw": round(pp + 0.3, 2)})

    # --- Sylhet fractured limestone: natural + induced losses
    span_s = formation_span(T, "SYLHET", well.td_tvd)
    if span_s > 0.05:
        sec5 = section_at(well.sections, rel_to_md(T, "SYLHET", min(span_s, 0.5), traj, well.td_tvd)[0])
        lg_s = loss_gradient("SYLHET", well.lat, well.lon, struct, year)
        p = float(sigmoid((sec5["ecd_ppg"] - lg_s) / 0.22)) * 0.85 + 0.25 * struct.fracture
        if rng.random() < min(p, 0.95):
            sev = str(rng.choice(["partial", "total"], p=[0.45, 0.55]))
            rate = round(float(rng.uniform(30, 90)) if sev == "partial" else float(rng.uniform(150, 300)), 0)
            add("LOSS", sev, "natural fractures", "SYLHET", float(rng.uniform(0.04, min(0.45, span_s))), sev,
                rate=rate, extra={"loss_grad": round(lg_s, 2)})

    # --- Langpar gas
    span_l = formation_span(T, "LANGPAR", well.td_tvd)
    if span_l > 0.05:
        sec6 = section_at(well.sections, well.td_md)
        pp = pore_pressure("LANGPAR", 0.5, well.lat, well.lon)
        if pp - sec6["mw_ppg"] > -0.3 and rng.random() < 0.6:
            add("KICK", "gas", "gas-bearing sand", "LANGPAR", float(rng.uniform(0.05, span_l)), "minor",
                extra={"gas_pct": round(float(rng.uniform(8, 25)), 1), "pp_ppg": round(pp, 2)})

    ev.sort(key=lambda e: e.md)
    return ev


def _add_fish(rng, well: Well, stuck: TruthEvent, ev: list[TruthEvent]):
    attempts, npt, ok = choose_attempts(rng, "FISH", "stuck pipe not freed")
    attempts = [{"code": "BACKOFF", "success": True, "npt": round(float(rng.uniform(6, 10)), 1)}] + attempts
    npt = round(npt + attempts[0]["npt"], 1)
    e = TruthEvent(stuck.id + "F", well.id, "FISH", "fishing", "stuck pipe not freed", stuck.formation, stuck.rel,
                   stuck.md, stuck.tvd, "major", None, None, stuck.mw_ppg, stuck.ecd_ppg, attempts, npt,
                   extra={"resolved": ok, "parent": stuck.id})
    ev.append(e)


# ---------------------------------------------------------------------------
# Daily timeline (feeds DDR text generation)
# ---------------------------------------------------------------------------

def build_days(rng, well: Well):
    """Split the well into drilling days with depth progress, casing jobs and events."""
    days = []
    md = 0.0
    day = 0
    start = dt.date.fromisoformat(well.spud_date)
    drilling_events = sorted([e for e in well.events if e.hazard != "CEMENT"], key=lambda e: e.md)
    cement_events = [e for e in well.events if e.hazard == "CEMENT"]
    sec_i = 0
    sections = well.sections
    while md < well.td_md - 0.5:
        sec = sections[sec_i]
        rate = {0: 150, 1: 180, 2: 120, 3: 85}[sec_i] * float(rng.uniform(0.75, 1.25))
        target = min(md + rate, sec["shoe_md"], well.td_md)
        day_events = [e for e in drilling_events if md < e.md <= target + 1e-6 or (md == 0 and e.md == 0)]
        date = (start + dt.timedelta(days=day)).isoformat()
        days.append({"day": day, "date": date, "from_md": round(md, 1), "to_md": round(target, 1),
                     "section": sec_i, "kind": "drilling", "events": day_events})
        for e in day_events:
            e.day, e.date = day, date
        npt = sum(e.npt_hours for e in day_events)
        day += 1 + int(npt // 24)
        md = target
        if md >= sec["shoe_md"] - 0.5 and md < well.td_md - 0.5:
            cem = [e for e in cement_events if sec_i == 2]
            date = (start + dt.timedelta(days=day)).isoformat()
            for e in cem:
                e.day, e.date = day, date
            days.append({"day": day, "date": date, "from_md": round(md, 1), "to_md": round(md, 1),
                         "section": sec_i, "kind": "casing", "events": cem})
            day += 2
            sec_i = min(sec_i + 1, len(sections) - 1)
    for e in well.events:
        if not e.date:
            e.day, e.date = days[-1]["day"], days[-1]["date"]
            days[-1]["events"].append(e)
    return days


# ---------------------------------------------------------------------------
# Lessons learned (WCR)
# ---------------------------------------------------------------------------

def build_lessons(well: Well):
    from ..domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE
    lessons = []
    for e in well.events:
        fm = FORMATION_BY_CODE[e.formation].name
        hz = HAZARD_BY_CODE[e.hazard].label.lower()
        succ = [a for a in e.attempts if a["success"]]
        fail = [a for a in e.attempts if not a["success"]]
        parts = []
        if succ:
            parts.append(f"{hz.capitalize()} in {fm} at {e.md:,.0f} m MD was resolved by "
                         f"{MITIGATION_BY_CODE[succ[-1]['code']].label.lower() if succ[-1]['code'] in MITIGATION_BY_CODE else 'back-off'}")
        else:
            parts.append(f"{hz.capitalize()} in {fm} at {e.md:,.0f} m MD was not resolved by the actions taken")
        if fail:
            names = ", ".join(sorted({MITIGATION_BY_CODE[a['code']].label.lower() for a in fail if a['code'] in MITIGATION_BY_CODE}))
            if names:
                parts.append(f"; {names} proved ineffective")
        rec = ""
        if e.hazard == "LOSS" and e.formation == "TIPAM":
            rec = f" Recommend keeping ECD below {e.extra.get('loss_grad', e.ecd_ppg) - 0.2:.1f} ppg across Tipam and pre-treating with sized CaCO3."
        elif e.hazard == "LOSS" and e.formation == "SYLHET":
            rec = " Fine LCM is ineffective in fractured Sylhet limestone; keep coarse LCM and cement plug contingency ready."
        elif e.hazard == "KICK" and e.subtype == "kick":
            rec = f" Future wells should raise MW to at least {e.extra.get('kill_mw', e.mw_ppg + 0.5):.1f} ppg before entering the lower {fm}."
        elif e.hazard == "TIGHT":
            rec = " Use KCl-PHPA-glycol with anti-balling additive through Girujan; plan wiper trips."
        elif e.hazard == "INSTAB":
            rec = f" Increase MW above {e.extra.get('collapse_ppg', e.mw_ppg + 0.3):.1f} ppg for this inclination and add sulphonated asphalt before coal seams."
        elif e.hazard == "STUCK" and e.subtype == "differential":
            rec = " Minimise static time across depleted Tipam sands and keep overbalance low."
        elif e.hazard == "CEMENT":
            rec = " Use lightweight lead slurry or two-stage cementing across depleted Tipam."
        if e.npt_hours >= 3 or rec:
            lessons.append({"event_id": e.id, "hazard": e.hazard, "formation": e.formation,
                            "text": "".join(parts) + f" (NPT {e.npt_hours:.1f} h)." + rec})
    return lessons


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

ACTIVE_LOC = (27.352, 95.632)


def generate_world(seed: int = 26121) -> World:
    rng = np.random.default_rng(seed)
    wells: list[Well] = []
    placed: list[tuple[float, float]] = [ACTIVE_LOC]
    for s in STRUCTURES:
        for k in range(s.n_wells):
            for _ in range(50):
                north = rng.normal(0, s.sigma_km * 0.8) * 1000
                east = rng.normal(0, s.sigma_km * 0.9) * 1000
                lat, lon = offset_latlon(s.lat, s.lon, north, east)
                if all(float(haversine_km(lat, lon, a, b)) > 0.5 for a, b in placed):
                    break
            placed.append((lat, lon))
            year = int(rng.integers(s.dev_start, 2025))
            wells.append(_make_well(rng, f"{s.prefix}-{k + 1:02d}", s, lat, lon, year))
    # well numbering by spud order within each structure (older = lower number)
    for s in STRUCTURES:
        ws = sorted([w for w in wells if w.structure_id == s.id], key=lambda w: w.spud_date)
        for i, w in enumerate(ws):
            w.id = w.name = f"{s.prefix}-{i + 1:02d}"
            for e in w.events:
                e.well_id = w.id
                e.id = f"{w.id}-{e.id.split('-')[-1]}"
            for l in w.lessons:
                l["event_id"] = f"{w.id}-{l['event_id'].split('-')[-1]}"
    # scanned (legacy) WCRs for the 4 oldest wells
    for w in sorted(wells, key=lambda w: w.spud_date)[:4]:
        w.scanned_wcr = True
    active = _make_active(rng)
    return World(wells=wells, active=active, seed=seed)


def _make_well(rng, name, s: Structure, lat, lon, year) -> Well:
    tops_tvd = true_tops_tvd(lat, lon, rng)
    target = str(rng.choice(["BARAIL", "KOPILI", "SYLHET", "LANGPAR"], p=[0.38, 0.2, 0.27, 0.15]))
    rel_td = {"BARAIL": 0.9, "KOPILI": 0.6, "SYLHET": 0.7, "LANGPAR": 0.55}[target]
    idx = FORMATION_ORDER.index(target)
    td_tvd = tops_tvd[target] + rel_td * (tops_tvd[FORMATION_ORDER[idx + 1]] - tops_tvd[target])
    traj_type = str(rng.choice(["vertical", "J", "S"], p=[0.5, 0.35, 0.15]))
    # iterate: survey needs td_md; approximate then refine
    params = sample_traj_params(rng, traj_type, tops_tvd)
    td_md = td_tvd * 1.15 if traj_type != "vertical" else td_tvd + 5
    for _ in range(4):
        md, inc, azi = make_survey(params, td_md)
        traj = Trajectory(md, inc, azi)
        td_md = float(traj.md_at_tvd(td_tvd))
    md, inc, azi = make_survey(params, td_md)
    traj = Trajectory(md, inc, azi)
    td_tvd = float(traj.tvd[-1])
    tops_md = {c: round(float(traj.md_at_tvd(z)), 1) for c, z in tops_tvd.items() if z < td_tvd}
    month = int(rng.integers(1, 13))
    dday = int(rng.integers(1, 28))
    spud_date = dt.date(year, month, dday).isoformat()
    mud = mud_system_for(rng, year)
    sections = plan_sections(rng, tops_tvd, traj, td_md, year, lat, lon, s)
    w = Well(id=name, name=name, structure_id=s.id, lat=round(lat, 6), lon=round(lon, 6), spud_year=year,
             spud_date=spud_date, traj_type=traj_type, survey_md=md, survey_inc=inc, survey_azi=azi,
             tops_tvd={c: z for c, z in tops_tvd.items() if z < td_tvd}, tops_md=tops_md,
             td_md=round(td_md, 1), td_tvd=round(td_tvd, 1), target=target, mud_system=mud, sections=sections,
             unit_system=str(rng.choice(["metric_ppg", "metric_sg", "field_ft"], p=[0.55, 0.25, 0.2])),
             status=str(rng.choice(["Oil producer", "Gas producer", "Suspended", "Plugged & abandoned"], p=[0.5, 0.2, 0.15, 0.15])))
    w.tops_tvd_full = tops_tvd  # includes formations below TD (latent)
    w.events = generate_events(rng, w, s, traj)
    w.days = build_days(rng, w)
    w.lessons = build_lessons(w)
    return w


def _make_active(rng) -> Well:
    """The well currently drilling (fed to the real-time simulator)."""
    s = STRUCT_BY_ID["NDH"]
    lat, lon = ACTIVE_LOC
    tops_tvd = true_tops_tvd(lat, lon, None)
    # the truth deviates from what offsets predict (re-anchoring demo)
    tops_tvd = {c: (z + (18 if c in ("TIPAM", "BARAIL", "KOPILI", "SYLHET", "LANGPAR", "BASEMENT") else 0))
                for c, z in tops_tvd.items()}
    target = "SYLHET"
    td_tvd = tops_tvd["SYLHET"] + 0.35 * (tops_tvd["LANGPAR"] - tops_tvd["SYLHET"])
    md = np.arange(0, 5000, 30.0)
    kop, br, inc_max = 800.0, 2.5, 28.0
    inc = np.clip((md - kop) / 30.0 * br, 0, inc_max)
    azi = np.full_like(md, 132.0)
    traj = Trajectory(md, inc, azi)
    td_md = float(traj.md_at_tvd(td_tvd))
    md = np.append(md[md < td_md], td_md)
    inc = np.clip((md - kop) / 30.0 * br, 0, inc_max)
    azi = np.full_like(md, 132.0)
    traj = Trajectory(md, inc, azi)
    sections = plan_sections(np.random.default_rng(7), tops_tvd, traj, td_md, 2026, lat, lon, s)
    # Planned mud (deliberately "offset-naive" so StrataSense has something to catch)
    sections[2]["mw_ppg"], sections[2]["ecd_add"] = 10.1, 0.45
    sections[3]["mw_ppg"], sections[3]["ecd_add"] = 10.6, 0.5
    for sec in sections:
        sec["ecd_ppg"] = round(sec["mw_ppg"] + sec["ecd_add"], 2)
    w = Well(id="NDH-21", name="NDH-21 (ACTIVE)", structure_id="NDH", lat=lat, lon=lon, spud_year=2026,
             spud_date="2026-08-30", traj_type="J", survey_md=md.round(1).tolist(), survey_inc=inc.round(2).tolist(),
             survey_azi=azi.tolist(), tops_tvd={}, tops_md={}, td_md=round(td_md, 1), td_tvd=round(td_tvd, 1),
             target=target, mud_system="KCl-PHPA-Glycol", sections=sections, unit_system="metric_ppg",
             status="Drilling", is_active=True)
    w.tops_tvd_full = tops_tvd  # truth, hidden from the analytics (only revealed as the bit penetrates)
    return w
