"""What-if planner: change the mud programme or casing points and see the risk picture recompute.

Honesty note shown to users: the offset-evidence part of the risk (Beta-Binomial over exposed offsets)
depends on *where* the well is and *which formations* it drills, not on the planned mud weight. A mud or
casing change moves (a) the ML layer, which uses planned MW/ECD per bin, and (b) the checks against the
offset-derived mud-weight window. Both are reported separately so nobody reads more into a delta than
the evidence supports.
"""
from __future__ import annotations

import copy

from ..domain.ontology import FORMATION_ORDER, RIBBON_HAZARDS
from ..kb import KnowledgeBase
from .evidence import risk_profile
from .mw_window import check_against_window, mw_window

EDITABLE = ("mw_ppg", "ecd_ppg", "shoe_md")
NOTE = ("Offset-evidence probabilities depend on location and formations, not on planned mud weight. "
        "Mud and casing changes move the ML layer (planned MW/ECD are model inputs) and the checks against "
        "the offset-derived mud-weight window.")


def apply_overrides(target, overrides: dict):
    """Deep-copy the plan (sections are shared with the knowledge base) and apply section edits."""
    t = copy.copy(target)
    t.sections = copy.deepcopy(target.sections)
    t.picked_tops_tvd = dict(target.picked_tops_tvd)
    by_idx = {s["idx"]: s for s in t.sections}
    for edit in overrides.get("sections", []):
        s = by_idx.get(int(edit.get("idx", -1)))
        if s is None:
            continue
        for k in EDITABLE:
            if edit.get(k) is not None:
                s[k] = round(float(edit[k]), 2)
        if s["ecd_ppg"] < s["mw_ppg"]:
            s["ecd_ppg"] = round(s["mw_ppg"] + 0.1, 2)   # ECD can never sit below static mud weight
    ordered = sorted(t.sections, key=lambda s: s["idx"])
    for prev, nxt in zip(ordered, ordered[1:]):          # keep sections contiguous after a shoe move
        nxt["top_md"] = prev["shoe_md"]
        nxt["shoe_md"] = max(nxt["shoe_md"], nxt["top_md"] + 10.0)
    ordered[-1]["shoe_md"] = max(ordered[-1]["shoe_md"], t.td_md)
    return t


def _peaks(profile: dict) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for b in profile["bins"]:
        for hz, h in b["hazards"].items():
            cell = out.setdefault(b["formation"], {}).setdefault(hz, {"final": 0.0, "evidence": 0.0, "ml": None})
            cell["final"] = max(cell["final"], h["final"])
            cell["evidence"] = max(cell["evidence"], h["p"])
            if "ml" in h:
                cell["ml"] = max(cell["ml"] or 0.0, h["ml"])
    return out


def _plan_checks(kb: KnowledgeBase, target, profile: dict, win: dict) -> list[dict]:
    rows = []
    for code in FORMATION_ORDER:
        top = profile["tops"].get(code)
        if not top or top["md"] > target.td_md or code not in win["formations"]:
            continue
        sec = target.section_at(top["md"] + 5)
        rows.append({"formation": code, "top_md": top["md"], "section": sec.get("hole"), "mw": sec["mw_ppg"],
                     "ecd": sec["ecd_ppg"], "window": win["formations"][code].get("window"),
                     "findings": check_against_window(win, code, sec["mw_ppg"], sec["ecd_ppg"])})
    return rows


def run_whatif(kb: KnowledgeBase, target, overrides: dict, model=None, radius_km: float = 8.0) -> dict:
    scenario_target = apply_overrides(target, overrides)
    base = risk_profile(kb, target, radius_km, model=model)
    scen = risk_profile(kb, scenario_target, radius_km, model=model)
    win = mw_window(kb, target, max(radius_km, 10.0))
    pb, ps = _peaks(base), _peaks(scen)
    deltas = []
    for fm in sorted(set(pb) | set(ps), key=FORMATION_ORDER.index):
        for hz in RIBBON_HAZARDS:
            b = pb.get(fm, {}).get(hz)
            s = ps.get(fm, {}).get(hz)
            if not b or not s:
                continue
            d = round(s["final"] - b["final"], 4)
            deltas.append({"formation": fm, "hazard": hz, "baseline": round(b["final"], 4),
                           "scenario": round(s["final"], 4), "delta": d, "evidence": round(s["evidence"], 4),
                           "ml_baseline": b["ml"], "ml_scenario": s["ml"]})
    checks_b = _plan_checks(kb, target, base, win)
    checks_s = _plan_checks(kb, scenario_target, scen, win)
    return {"note": NOTE, "sections": {"baseline": target.sections, "scenario": scenario_target.sections},
            "deltas": deltas,
            "top_changes": sorted([d for d in deltas if abs(d["delta"]) >= 0.02], key=lambda d: -abs(d["delta"]))[:10],
            "window_checks": {"baseline": checks_b, "scenario": checks_s},
            "findings_count": {"baseline": sum(len(c["findings"]) for c in checks_b),
                               "scenario": sum(len(c["findings"]) for c in checks_s)},
            "zones": {"baseline": len(base["zones"]), "scenario": len(scen["zones"])}}
