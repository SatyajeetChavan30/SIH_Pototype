"""Uncertainty-aware offset-evidence risk ribbon (differentiator #3).

For each depth bin of the target and each hazard:
  * only offsets that actually drilled the equivalent formation interval count
    as exposures (censoring-aware),
  * each offset is weighted by distance, same-structure, recency and data quality,
  * a Beta prior from the basin-wide formation base rate is updated with the
    weighted exposures/events -> posterior mean + 90% credible interval +
    effective evidence size, so thin evidence *looks* thin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import beta as beta_dist

from ..correlation import Target, formation_at, offset_event_rel, predict_tops, project_rel, thickness
from ..domain.ontology import FORMATION_ORDER, RIBBON_HAZARDS
from ..kb import KnowledgeBase, WellRec

LAMBDA_KM = 3.5
PRIOR_N = 2.0
BIN_M = 25.0
REL_TOL_M = 12.0          # intrinsic depth scatter of a hazard inside a formation
MAX_TOL_M = 45.0


def offset_weight(target: Target, w: WellRec, d_km: float) -> float:
    wd = math.exp(-d_km / LAMBDA_KM)
    ws = 1.5 if (target.structure_id and w.structure_id == target.structure_id) else 1.0
    age = max((target.spud_year or 2026) - (w.spud_year or target.spud_year or 2026), 0)
    wr = 0.35 + 0.65 * math.exp(-age / 15.0)
    confs = [e["confidence"] for e in w.events if e.get("confidence") is not None]
    wq = 0.8 + 0.2 * (float(np.mean(confs)) if confs else 1.0)
    return wd * ws * wr * wq


@dataclass
class OffsetInfo:
    well: WellRec
    dist: float
    weight: float
    spans: dict[str, float]
    events: dict[tuple[str, str], list[tuple[float, dict]]]   # (formation, hazard) -> [(rel, event)]


def build_offsets(kb: KnowledgeBase, target: Target, radius_km: float, before_year: int | None = None) -> list[OffsetInfo]:
    near = [(w, d) for w, d in kb.nearby(target.lat, target.lon, radius_km, target.exclude, before_year)]
    infos = []
    for w, d in near:
        spans = {c: w.formation_span(c, kb.thick) for c in FORMATION_ORDER if c in w.tops_tvd}
        evs: dict[tuple[str, str], list] = {}
        for e in w.events:
            if not e.get("formation") or e["hazard"] not in RIBBON_HAZARDS:
                continue
            r = offset_event_rel(w, e, kb)
            if r is None:
                continue
            evs.setdefault((e["formation"], e["hazard"]), []).append((r, e))
        infos.append(OffsetInfo(w, d, offset_weight(target, w, d), spans, evs))
    return infos


class BaseRates:
    """Basin-wide prior: fraction of exposed wells with an event of hazard H inside a rel-window of F."""

    def __init__(self, kb: KnowledgeBase, exclude: set[str] | None = None, before_year: int | None = None):
        self.rels: dict[tuple[str, str], list[float]] = {}
        self.spans: dict[str, list[float]] = {}
        for w in kb.offsets(exclude, before_year):
            for c in FORMATION_ORDER:
                if c in w.tops_tvd:
                    self.spans.setdefault(c, []).append(w.formation_span(c, kb.thick))
            for e in w.events:
                if e.get("formation") and e["hazard"] in RIBBON_HAZARDS:
                    r = offset_event_rel(w, e, kb)
                    if r is not None:
                        self.rels.setdefault((e["formation"], e["hazard"]), []).append(r)

    def rate(self, fm: str, hz: str, r0: float, r1: float) -> float:
        spans = self.spans.get(fm, [])
        exposed = sum(1 for s in spans if s > r0)
        if exposed == 0:
            return 0.01
        n = sum(1 for r in self.rels.get((fm, hz), []) if r0 <= r <= r1)
        return float(np.clip((n + 0.05) / (exposed + 1), 0.002, 0.95))


def make_bins(target: Target, tops: dict, kb: KnowledgeBase, bin_m: float = BIN_M, md_from: float = 0.0,
              md_to: float | None = None) -> list[dict]:
    md_to = md_to or target.td_md
    bins = []
    m = max(0.0, math.floor(md_from / bin_m) * bin_m)
    while m < md_to - 1e-6:
        m1 = min(m + bin_m, md_to)
        t0, t1 = float(target.traj.tvd_at_md(m)), float(target.traj.tvd_at_md(m1))
        fm, _ = formation_at(tops, (t0 + t1) / 2, kb)
        th = thickness(tops, fm, kb)
        r0 = float(np.clip((t0 - tops[fm]["tvd"]) / th, 0, 1))
        r1 = float(np.clip((t1 - tops[fm]["tvd"]) / th, 0, 1))
        sec = target.section_at((m + m1) / 2)
        bins.append({"md0": round(m, 1), "md1": round(m1, 1), "tvd0": round(t0, 1), "tvd1": round(t1, 1),
                     "formation": fm, "rel0": round(r0, 4), "rel1": round(r1, 4), "thick": th,
                     "inc": float(target.traj.inc_at_md((m + m1) / 2)), "mw": sec["mw_ppg"], "ecd": sec["ecd_ppg"],
                     "section": sec.get("hole")})
        m = m1
    return bins


def evidence_for_bin(b: dict, hz: str, offsets: list[OffsetInfo], base: BaseRates, tops: dict, kb: KnowledgeBase,
                     target: Target, keep_evidence: bool = True) -> dict:
    fm = b["formation"]
    # projection tolerance = hazard scatter (+) uncertainty of the predicted formation top
    tol_m = float(np.clip(math.hypot(REL_TOL_M, tops.get(fm, {}).get("sd", 0.0)), 15.0, MAX_TOL_M))
    tol = tol_m / max(b["thick"], 1.0)
    r0, r1 = b["rel0"] - tol, b["rel1"] + tol
    p0 = base.rate(fm, hz, max(r0, 0), min(r1, 1))
    a = PRIOR_N * p0
    bb = PRIOR_N * (1 - p0)
    n_exp = n_ev = 0
    w_sum = 0.0
    nearest_hit = None
    ev_items = []
    for o in offsets:
        span = o.spans.get(fm, 0.0)
        if span <= b["rel0"]:
            continue  # offset never drilled this interval -> no information (censored)
        hits = [(r, e) for r, e in o.events.get((fm, hz), []) if r0 <= r <= r1]
        n_exp += 1
        w_sum += o.weight
        if nearest_hit is None:
            nearest_hit = 1.0 if hits else 0.0
        if hits:
            n_ev += 1
            a += o.weight
            if keep_evidence:
                r, e = hits[0]
                ev_items.append({"well_id": o.well.id, "distance_km": round(o.dist, 2), "weight": round(o.weight, 3),
                                 "event_id": e["id"], "md": e["md"], "rel": round(r, 3), "summary": e["summary"],
                                 "projected_md": round(float(target.traj.md_at_tvd(project_rel(tops, fm, r, kb))), 1),
                                 "spud_year": o.well.spud_year, "citation": (e["citations"][0] if e.get("citations") else None)})
        else:
            bb += o.weight
    mean = a / (a + bb)
    lo, hi = beta_dist.ppf([0.05, 0.95], a, bb)
    return {"p": round(float(mean), 4), "lo": round(float(lo), 4), "hi": round(float(hi), 4), "prior": round(p0, 4),
            "n_eff": round(w_sum, 2), "n_exposed": n_exp, "n_events": n_ev,
            "nearest": nearest_hit if nearest_hit is not None else float("nan"),
            "evidence": sorted(ev_items, key=lambda x: -x["weight"])[:6]}


def formation_summary(offsets: list[OffsetInfo], hz: str, fm: str) -> dict:
    """'k of n offsets that drilled F had H' with a weighted posterior."""
    n = k = 0
    a, b = 0.5, 0.5
    wells = []
    for o in offsets:
        if o.spans.get(fm, 0) < 0.5:
            continue
        n += 1
        hit = bool(o.events.get((fm, hz)))
        if hit:
            k += 1
            a += o.weight
            wells.append(o.well.id)
        else:
            b += o.weight
    return {"k": k, "n": n, "p": round(a / (a + b), 3), "wells": wells}


def risk_profile(kb: KnowledgeBase, target: Target, radius_km: float = 8.0, bin_m: float = BIN_M,
                 md_from: float = 0.0, md_to: float | None = None, model=None, before_year: int | None = None) -> dict:
    offsets = build_offsets(kb, target, radius_km, before_year)
    tops = predict_tops(kb, target, kb.nearby(target.lat, target.lon, max(radius_km, 25.0), target.exclude, before_year))
    base = BaseRates(kb, target.exclude, before_year)
    bins = make_bins(target, tops, kb, bin_m, md_from, md_to)
    for b in bins:
        b["hazards"] = {hz: evidence_for_bin(b, hz, offsets, base, tops, kb, target) for hz in RIBBON_HAZARDS}
    if model is not None:
        model.annotate(bins, target, offsets, kb)
    for b in bins:
        for hz, h in b["hazards"].items():
            h["final"] = round(h["w_ml"] * h["ml"] + (1 - h["w_ml"]) * h["p"], 4) if "ml" in h else h["p"]
    formations = sorted({b["formation"] for b in bins}, key=FORMATION_ORDER.index)
    zones = hazard_zones({"tops": tops, "bins": bins, "_base": base}, kb=kb, target=target, offsets=offsets)
    summary = {fm: {hz: formation_summary(offsets, hz, fm) for hz in RIBBON_HAZARDS} for fm in formations}
    return {"target": {"id": target.id, "name": target.name, "lat": target.lat, "lon": target.lon,
                       "td_md": target.td_md, "structure_id": target.structure_id, "spud_year": target.spud_year,
                       "sections": target.sections},
            "radius_km": radius_km, "tops": tops, "bins": bins, "formation_summary": summary, "zones": zones,
            "offsets": [{"well_id": o.well.id, "distance_km": round(o.dist, 2), "weight": round(o.weight, 3),
                         "structure_id": o.well.structure_id, "spud_year": o.well.spud_year,
                         "n_events": sum(len(v) for v in o.events.values())} for o in offsets]}


def hazard_zones(profile: dict, threshold: float = 0.25, key: str = "final", min_wells: int = 2,
                 kb: KnowledgeBase | None = None, target: Target | None = None, offsets: list[OffsetInfo] | None = None,
                 gap_m: float = 60.0) -> list[dict]:
    """Look-ahead zones = clusters of offset events projected onto the target (formation-aligned).

    Zone probability is the distance/recency-weighted share of *exposed* offsets that had the hazard in the
    cluster interval (Beta posterior with the basin prior), reported together with the peak ML/blended bin risk.
    """
    tops = profile["tops"]
    bins = profile["bins"]
    offsets = offsets if offsets is not None else profile.get("_offsets", [])
    kb = kb or profile.get("_kb")
    zones: list[dict] = []
    for hz in RIBBON_HAZARDS:
        pts = []
        for o in offsets:
            for (fm, h), lst in o.events.items():
                if h != hz or fm not in tops:
                    continue
                for r, e in lst:
                    md = float(target.traj.md_at_tvd(project_rel(tops, fm, r, kb))) if target is not None else None
                    if md is None or md > target.td_md + 30:
                        continue
                    pts.append((md, fm, r, o, e))
        pts.sort(key=lambda x: x[0])
        clusters: list[list] = []
        for p in pts:
            if clusters and p[0] - clusters[-1][-1][0] <= gap_m and p[1] == clusters[-1][-1][1]:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        for c in clusters:
            fm = max({x[1] for x in c}, key=lambda f: sum(1 for x in c if x[1] == f))
            wells = {x[3].well.id for x in c}
            if len(wells) < min_wells:
                continue
            tol = float(np.clip(tops[fm].get("sd", 10.0), 15.0, MAX_TOL_M))
            md0 = max(min(x[0] for x in c) - tol, 0.0)
            md1 = min(max(x[0] for x in c) + tol, target.td_md)
            rmin = min(x[2] for x in c)
            base = profile.get("_base")
            p0 = base.rate(fm, hz, max(rmin - 0.05, 0), min(max(x[2] for x in c) + 0.05, 1)) if base else 0.05
            a, b = PRIOR_N * p0, PRIOR_N * (1 - p0)
            n_exp = 0
            for o in offsets:
                if o.spans.get(fm, 0) <= rmin:
                    continue
                n_exp += 1
                if o.well.id in wells:
                    a += o.weight
                else:
                    b += o.weight
            p_off = a / (a + b)
            lo, hi = beta_dist.ppf([0.05, 0.95], a, b)
            zb = [x for x in bins if x["md1"] >= md0 and x["md0"] <= md1]
            peak_bin = max((x["hazards"][hz][key] for x in zb), default=0.0)
            drivers = next((x["hazards"][hz].get("drivers", []) for x in sorted(zb, key=lambda x: -x["hazards"][hz][key])), [])
            risk = max(p_off, peak_bin)
            if risk < threshold and len(wells) < 3:
                continue
            ev_items = []
            for md, f, r, o, e in sorted(c, key=lambda x: -x[3].weight):
                ev_items.append({"well_id": o.well.id, "distance_km": round(o.dist, 2), "weight": round(o.weight, 3),
                                 "event_id": e["id"], "md": e["md"], "rel": round(r, 3), "summary": e["summary"],
                                 "projected_md": round(md, 1), "spud_year": o.well.spud_year,
                                 "citation": (e["citations"][0] if e.get("citations") else None)})
            zones.append({"hazard": hz, "md0": round(md0, 1), "md1": round(md1, 1), "formation": fm,
                          "peak": round(float(risk), 4), "p_offsets": round(float(p_off), 4), "p_model": round(float(peak_bin), 4),
                          "lo": round(float(lo), 4), "hi": round(float(hi), 4), "n_exposed": n_exp, "n_events": len(wells),
                          "evidence": ev_items[:8], "drivers": drivers,
                          "mw": zb[0]["mw"] if zb else None, "ecd": zb[0]["ecd"] if zb else None})
    return sorted(zones, key=lambda z: z["md0"])
