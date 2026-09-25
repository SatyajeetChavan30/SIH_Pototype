"""Outcome-weighted mitigation recommendations ("what actually worked in offsets")."""
from __future__ import annotations

import math

import numpy as np

from ..domain.ontology import HAZARD_BY_CODE, MITIGATION_BY_CODE, MITIGATIONS
from ..kb import KnowledgeBase


def efficacy(events: list[dict]) -> list[dict]:
    """Per mitigation: attempts, cure rate (Laplace-smoothed), first-try cure rate, NPT of resolved events."""
    stats: dict[str, dict] = {}
    for e in events:
        for i, a in enumerate(e.get("actions", [])):
            s = stats.setdefault(a["code"], {"code": a["code"], "n": 0, "k": 0, "first_n": 0, "first_k": 0,
                                             "npt_resolved": [], "wells": set(), "events": []})
            s["n"] += 1
            s["k"] += int(bool(a["success"]))
            s["wells"].add(e["well_id"])
            s["events"].append(e["id"])
            if i == 0:
                s["first_n"] += 1
                s["first_k"] += int(bool(a["success"]))
            if a["success"] and e.get("npt_hours"):
                s["npt_resolved"].append(e["npt_hours"])
    out = []
    for code, s in stats.items():
        m = MITIGATION_BY_CODE.get(code)
        rate = (s["k"] + 1) / (s["n"] + 2)
        out.append({"code": code, "label": m.label if m else ("Back-off and fish" if code == "BACKOFF" else code),
                    "attempts": s["n"], "cured": s["k"], "cure_rate": round(s["k"] / s["n"], 3),
                    "cure_rate_smoothed": round(rate, 3),
                    "first_try": f"{s['first_k']}/{s['first_n']}" if s["first_n"] else "-",
                    "median_npt_h": round(float(np.median(s["npt_resolved"])), 1) if s["npt_resolved"] else None,
                    "wells": sorted(s["wells"]), "events": s["events"][:12],
                    "score": round(rate * math.log(2 + s["n"]), 3),
                    "preventive": m.preventive if m else ""})
    out.sort(key=lambda x: -x["score"])
    for x in out:
        x["verdict"] = "recommended" if x["cure_rate_smoothed"] >= 0.55 else (
            "avoid" if x["cure_rate"] <= 0.3 and x["attempts"] >= 2 else "mixed")
    return out


def recommend(kb: KnowledgeBase, hazard: str, formation: str | None = None, wells: set[str] | None = None,
              min_events: int = 3) -> dict:
    """Efficacy for a hazard, local (offset wells in radius) first, widening to basin if evidence is thin."""
    def pick(scope_wells, fm):
        return [e for e in kb.events if e["hazard"] == hazard and (fm is None or e["formation"] == fm)
                and (scope_wells is None or e["well_id"] in scope_wells) and e.get("actions")]
    scopes = [("offset wells, same formation", wells, formation), ("basin, same formation", None, formation),
              ("basin, all formations", None, None)]
    for label, ws, fm in scopes:
        if ws is None and label.startswith("offset"):
            continue
        evs = pick(ws, fm)
        if len(evs) >= min_events or label.startswith("basin, all"):
            eff = efficacy(evs)
            break
    preventive = []
    for m in MITIGATIONS:
        if m.hazard == hazard and m.preventive:
            preventive.append({"code": m.code, "text": m.preventive,
                               "support": next((x["cure_rate"] for x in eff if x["code"] == m.code), None)})
    preventive.sort(key=lambda p: -(p["support"] or 0))
    lessons = [l for l in kb.lessons if (l["hazard"] == hazard or (l["text"] and HAZARD_BY_CODE[hazard].label.split()[0].lower() in l["text"].lower()))
               and (formation is None or l["formation"] in (None, formation)) and (wells is None or l["well_id"] in wells)]
    return {"hazard": hazard, "formation": formation, "scope": label, "n_events": len(evs),
            "actions": eff, "preventive": preventive[:4],
            "lessons": [{"id": l["id"], "well_id": l["well_id"], "text": l["text"], "doc_id": l["doc_id"],
                         "page_no": l["page_no"], "start": l["start"], "end": l["end"], "title": l.get("doc_title")}
                        for l in lessons[:5]]}
