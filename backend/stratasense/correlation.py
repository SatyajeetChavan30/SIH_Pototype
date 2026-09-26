"""Formation-aligned correlation between a target well and its offsets.

Key idea (differentiator #1): hazards follow *formations*, not measured depth. An
offset event at relative position r inside formation F is projected onto the
target at  top'(F) + r * thickness'(F)  where the target's tops are interpolated
from offsets (inverse-distance weighting with an uncertainty band) and re-anchored
live as the bit penetrates actual tops.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config
from .domain.ontology import DEFAULT_TD, FORMATION_ORDER, IS_ASSAM, SURFACE
from .geo import Trajectory, haversine_km
from .kb import KnowledgeBase, WellRec


@dataclass
class Target:
    id: str | None
    name: str
    lat: float
    lon: float
    traj: Trajectory
    sections: list[dict]
    spud_year: int
    structure_id: str | None
    td_md: float
    mud_system: str = "KCl-PHPA-Glycol"
    picked_tops_tvd: dict[str, float] = field(default_factory=dict)
    exclude: set[str] = field(default_factory=set)

    @property
    def td_tvd(self) -> float:
        return float(self.traj.tvd_at_md(self.td_md))

    def section_at(self, md: float) -> dict:
        for s in self.sections:
            if s["top_md"] <= md <= s["shoe_md"] + 1e-6:
                return s
        return self.sections[-1]


def target_from_well(kb: KnowledgeBase, well_id: str, picked_tops_tvd: dict[str, float] | None = None,
                     planning_view: bool = True) -> Target:
    w = kb.wells[well_id]
    return Target(id=w.id, name=w.name, lat=w.lat, lon=w.lon, traj=w.traj, sections=w.sections,
                  spud_year=w.spud_year or config.REFERENCE_YEAR, structure_id=w.structure_id, td_md=w.td_md,
                  mud_system=w.mud_system or "KCl-PHPA-Glycol", picked_tops_tvd=dict(picked_tops_tvd or {}),
                  exclude={w.id})


def default_plan(kb: KnowledgeBase, lat: float, lon: float, td_formation: str = DEFAULT_TD, radius_km: float = 10.0,
                 name: str = "Planned well") -> Target:
    """Vertical plan with casing points/mud from recent offset practice (for 'what if we drill here?')."""
    near = kb.nearby(lat, lon, radius_km) or kb.nearby(lat, lon, 50.0)
    probe = Target(None, name, lat, lon, Trajectory([0, 5000], [0, 0], [0, 0]), [], config.REFERENCE_YEAR,
                   kb.structure_of(lat, lon), 5000.0)
    tops = predict_tops(kb, probe, near)
    i = FORMATION_ORDER.index(td_formation)
    nxt = FORMATION_ORDER[i + 1]
    td_tvd = tops[td_formation]["tvd"] + 0.6 * (tops[nxt]["tvd"] - tops[td_formation]["tvd"])
    md = np.arange(0, td_tvd + 30, 30.0)
    md[-1] = td_tvd
    traj = Trajectory(md, np.zeros_like(md), np.zeros_like(md))
    recent = [w for w, _ in near if (w.spud_year or 0) >= 2012 and len(w.sections) == 4] or [w for w, _ in near if len(w.sections) == 4]

    def med(idx, key, default):
        v = [w.sections[idx][key] for w in recent]
        return round(float(np.median(v)), 2) if v else default

    if not IS_ASSAM:
        return Target(None, name, lat, lon, traj, _offset_casing_plan([w for w, _ in near], td_tvd), config.REFERENCE_YEAR,
                      kb.structure_of(lat, lon), td_tvd)
    shoes = [90.0, tops["GIRUJAN"]["tvd"] - 40, tops["BARAIL"]["tvd"] - 12, td_tvd]
    sections = []
    for k, (hole, csg) in enumerate([("26\"", "20\""), ("17-1/2\"", "13-3/8\""), ("12-1/4\"", "9-5/8\""), ("8-1/2\"", "7\" liner")]):
        mw = med(k, "mw_ppg", [8.9, 9.15, 9.9, 10.8][k])
        ecd = med(k, "ecd_ppg", mw + 0.4)
        sections.append({"idx": k, "hole": hole, "casing": csg, "top_md": 0.0 if k == 0 else shoes[k - 1],
                         "shoe_md": shoes[k], "mw_ppg": mw, "ecd_ppg": max(ecd, mw + 0.1)})
    return Target(None, name, lat, lon, traj, sections, config.REFERENCE_YEAR, kb.structure_of(lat, lon), td_tvd)


def _offset_casing_plan(wells: list[WellRec], td: float) -> list[dict]:
    """Casing programme copied from nearby offsets: their most common number of sections, median shoe depth and
    mud weight per section (used where no regional casing rule is coded, e.g. real public North Sea data)."""
    with_secs = [w for w in wells if w.sections]
    if not with_secs:
        return [{"idx": 0, "hole": "12-1/4\"", "casing": "open hole", "top_md": 0.0, "shoe_md": td, "mw_ppg": 9.5,
                 "ecd_ppg": 9.9}]
    counts: dict[int, int] = {}
    for w in with_secs:
        counts[len(w.sections)] = counts.get(len(w.sections), 0) + 1
    n = max(counts, key=lambda k: (counts[k], k))
    group = [w for w in with_secs if len(w.sections) == n]
    out, top = [], 0.0
    for k in range(n):
        col = [w.sections[k] for w in group]
        shoe = td if k == n - 1 else min(float(np.median([c["shoe_md"] for c in col])), td - 10.0 * (n - 1 - k))
        mw = round(float(np.median([c["mw_ppg"] for c in col])), 2)
        ecd = round(float(np.median([c["ecd_ppg"] for c in col])), 2)
        out.append({"idx": k, "hole": col[0]["hole"], "casing": col[0]["casing"], "top_md": top, "shoe_md": max(shoe, top + 10.0),
                    "mw_ppg": mw, "ecd_ppg": max(ecd, mw + 0.1)})
        top = out[-1]["shoe_md"]
    return out


# ---------------------------------------------------------------------------
# Formation tops prediction
# ---------------------------------------------------------------------------
def predict_tops(kb: KnowledgeBase, target: Target, near: list[tuple[WellRec, float]], k: int = 10) -> dict[str, dict]:
    out: dict[str, dict] = {}
    prev = 0.0
    near = [(w, d) for w, d in near if w.id not in target.exclude][:max(k, 1)]
    absent: list[str] = []
    for code in FORMATION_ORDER:
        if code == SURFACE:
            out[code] = {"tvd": 0.0, "sd": 0.0, "n": len(near)}
            continue
        vals = [(w.tops_tvd[code], d) for w, d in near if code in w.tops_tvd]
        if not vals and not IS_ASSAM:
            # real data: a unit no nearby well penetrated is absent here (eroded / not deposited), not "typical
            # thickness below the last one". It is pinched out at the top of the next unit that is present.
            absent.append(code)
            continue
        if vals:
            z = np.array([v for v, _ in vals])
            d = np.array([x for _, x in vals])
            wts = 1.0 / (d ** 2 + 0.25)
            zh = float(np.sum(wts * z) / np.sum(wts))
            sd = float(np.sqrt(np.sum(wts * (z - zh) ** 2) / np.sum(wts))) + 6.0 + 3.0 * float(d.min())
            n = len(vals)
        else:
            i = FORMATION_ORDER.index(code)
            zh = out[FORMATION_ORDER[i - 1]]["tvd"] + kb.thick.get(FORMATION_ORDER[i - 1], 350.0)
            sd = out[FORMATION_ORDER[i - 1]]["sd"] + 40.0
            n = 0
        zh = max(zh, prev + 30.0)
        out[code] = {"tvd": zh, "sd": sd, "n": n}
        prev = zh
    for code in absent:
        i = FORMATION_ORDER.index(code)
        below = next((out[c] for c in FORMATION_ORDER[i + 1:] if c in out), None)
        above = next((out[c] for c in reversed(FORMATION_ORDER[:i]) if c in out), {"tvd": 0.0, "sd": 0.0})
        ref = below or above
        out[code] = {"tvd": ref["tvd"], "sd": ref["sd"], "n": 0, "absent": True}
    # live re-anchoring on picked (penetrated) tops
    if target.picked_tops_tvd:
        deepest = max(target.picked_tops_tvd, key=lambda c: FORMATION_ORDER.index(c))
        delta = target.picked_tops_tvd[deepest] - out[deepest]["tvd"]
        di = FORMATION_ORDER.index(deepest)
        for code in FORMATION_ORDER:
            ci = FORMATION_ORDER.index(code)
            if code in target.picked_tops_tvd:
                out[code].update(tvd=target.picked_tops_tvd[code], sd=2.0, picked=True)
            elif ci > di:
                out[code].update(tvd=out[code]["tvd"] + delta, sd=out[code]["sd"] * 0.7, shifted_m=round(delta, 1))
    out = {c: out[c] for c in FORMATION_ORDER if c in out}
    for code, v in out.items():
        v["md"] = float(target.traj.md_at_tvd(v["tvd"]))
        v["tvd"] = round(v["tvd"], 1)
        v["sd"] = round(v["sd"], 1)
        v["md"] = round(v["md"], 1)
    return out


def thickness(tops: dict[str, dict], code: str, kb: KnowledgeBase) -> float:
    i = FORMATION_ORDER.index(code)
    if i + 1 < len(FORMATION_ORDER) and FORMATION_ORDER[i + 1] in tops:
        return max(tops[FORMATION_ORDER[i + 1]]["tvd"] - tops[code]["tvd"], 1.0)
    return kb.thick.get(code, 350.0)


def formation_at(tops: dict[str, dict], tvd: float, kb: KnowledgeBase) -> tuple[str, float]:
    cur = SURFACE
    for code in FORMATION_ORDER:
        if code in tops and tops[code]["tvd"] <= tvd:
            cur = code
    th = thickness(tops, cur, kb)
    return cur, float(np.clip((tvd - tops[cur]["tvd"]) / th, 0, 1))


def project_rel(tops: dict[str, dict], code: str, rel: float, kb: KnowledgeBase) -> float:
    return tops[code]["tvd"] + rel * thickness(tops, code, kb)


def offset_event_rel(w: WellRec, e: dict, kb: KnowledgeBase) -> float | None:
    if e.get("rel") is not None:
        return float(e["rel"])
    if e.get("formation") in w.tops_tvd and e.get("tvd") is not None:
        i = FORMATION_ORDER.index(e["formation"])
        nxt = FORMATION_ORDER[i + 1] if i + 1 < len(FORMATION_ORDER) else None
        base = w.tops_tvd.get(nxt) if nxt else None
        th = (base - w.tops_tvd[e["formation"]]) if base else kb.thick.get(e["formation"], 350.0)
        return float(np.clip((e["tvd"] - w.tops_tvd[e["formation"]]) / max(th, 1), 0, 1))
    return None


def correlation_panel(kb: KnowledgeBase, target: Target, radius_km: float, max_wells: int = 8) -> dict:
    near = [(w, d) for w, d in kb.nearby(target.lat, target.lon, radius_km) if w.id not in target.exclude][:max_wells]
    tops = predict_tops(kb, target, kb.nearby(target.lat, target.lon, max(radius_km, 8)))
    tracks = []
    for w, d in near:
        tracks.append({
            "well_id": w.id, "distance_km": round(d, 2), "same_structure": w.structure_id == target.structure_id,
            "spud_year": w.spud_year, "td_md": w.td_md, "td_tvd": w.td_tvd,
            "tops": [{"formation": c, "md": w.tops_md[c], "tvd": w.tops_tvd[c]} for c in FORMATION_ORDER if c in w.tops_md],
            "casing": [{"casing": s["casing"], "shoe_md": s["shoe_md"],
                        "shoe_tvd": round(float(w.traj.tvd_at_md(s["shoe_md"])), 1) if w.traj else s["shoe_md"],
                        "mw_ppg": s["mw_ppg"]} for s in w.sections],
            "events": [{"id": e["id"], "hazard": e["hazard"], "subtype": e["subtype"], "md": e["md"], "tvd": e["tvd"],
                        "formation": e["formation"], "rel": offset_event_rel(w, e, kb), "summary": e["summary"]}
                       for e in w.events if e["md"] is not None],
            "log": load_log_decimated(w.id),
        })
    return {"target": {"id": target.id, "name": target.name, "td_md": target.td_md, "td_tvd": round(target.td_tvd, 1),
                       "predicted_tops": tops},
            "tracks": tracks}


def load_log_decimated(well_id: str, step: int = 5) -> dict | None:
    p = config.LOGS_DIR / f"{well_id}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    sl = slice(0, None, step)
    return {k: np.round(z[k][sl].astype(float), 2).tolist() for k in ("md", "tvd", "gr", "rop", "dxc")}
