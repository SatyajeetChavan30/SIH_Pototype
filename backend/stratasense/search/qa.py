"""'Ask StrataSense': grounded, extractive answers with statistics and numbered citations.

No LLM is required. If an on-prem LLM is enabled (STRATASENSE_LLM=ollama) it may only
rephrase the grounded facts, and a citation guard drops any sentence that does
not cite evidence we actually retrieved.
"""
from __future__ import annotations

import numpy as np

from ..domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE
from ..geo import haversine_km
from ..kb import KnowledgeBase
from ..llm import llm_summarize
from ..risk.recommend import efficacy
from .index import SearchIndex
from .query import parse_query


def answer(kb: KnowledgeBase, index: SearchIndex, question: str, center: tuple[float, float] | None = None) -> dict:
    pq = parse_query(question)
    if (pq.radius_km or pq.near_well) and center is None and kb.active:
        center = (kb.active.lat, kb.active.lon)
    res = index.search(pq, types=("event", "lesson"), limit=30, center=center)
    events = [r for r in res["results"] if r["type"] == "event"]
    lessons = [r for r in res["results"] if r["type"] == "lesson"]
    full_events = [next(e for e in kb.events if e["id"] == r["id"]) for r in events]

    # exposure: offsets in scope that drilled the formation
    scope_wells = {w.id for w in kb.offsets()}
    if pq.radius_km or pq.near_well:
        c = center
        if pq.near_well and pq.near_well in kb.wells:
            c = (kb.wells[pq.near_well].lat, kb.wells[pq.near_well].lon)
        if c:
            scope_wells = {w.id for w in kb.offsets() if float(haversine_km(c[0], c[1], w.lat, w.lon)) <= (pq.radius_km or 5)}
    if pq.formation:
        exposed = {wid for wid in scope_wells if pq.formation in kb.wells[wid].tops_tvd}
    else:
        exposed = scope_wells

    facts: list[str] = []
    cites: list[dict] = []

    def cite(c: dict | None, label: str) -> str:
        if not c:
            return ""
        cites.append({"n": len(cites) + 1, "label": label, **c})
        return f" [{len(cites)}]"

    hz_label = ", ".join(HAZARD_BY_CODE[h].label.lower() for h in pq.hazards) or "drilling problems"
    fm_label = FORMATION_BY_CODE[pq.formation].name if pq.formation else None
    if full_events:
        wells = sorted({e["well_id"] for e in full_events})
        mds = [e["md"] for e in full_events if e["md"] is not None]
        years = sorted({kb.wells[e["well_id"]].spud_year for e in full_events if e["well_id"] in kb.wells})
        scope = f" within {pq.radius_km:g} km" if pq.radius_km else ""
        where = f" in {fm_label}" if fm_label else ""
        facts.append(f"{len(wells)} of {len(exposed)} offset wells{scope}{' that drilled ' + fm_label if fm_label else ''} "
                     f"recorded {hz_label}{where} ({len(full_events)} events: {', '.join(wells[:8])}{'…' if len(wells) > 8 else ''}).")
        if mds:
            facts.append(f"Depths ranged {min(mds):,.0f}–{max(mds):,.0f} m MD (median {np.median(mds):,.0f} m); "
                         f"wells spudded {years[0]}–{years[-1]}.")
        npt = [e["npt_hours"] for e in full_events if e["npt_hours"]]
        if npt:
            facts.append(f"Total NPT {sum(npt):,.0f} h (median {np.median(npt):.1f} h per event).")
        sev = {}
        for e in full_events:
            sev[e["severity"]] = sev.get(e["severity"], 0) + 1
        if len(sev) > 1:
            facts.append("Severity mix: " + ", ".join(f"{k} {v}" for k, v in sorted(sev.items(), key=lambda kv: -kv[1])) + ".")
        eff = efficacy(full_events)
        good = [x for x in eff if x["verdict"] == "recommended"][:2]
        bad = [x for x in eff if x["verdict"] == "avoid"][:2]
        if good:
            facts.append("What worked: " + "; ".join(f"{x['label']} cured {x['cured']}/{x['attempts']}"
                                                   + (f" (median NPT {x['median_npt_h']} h)" if x["median_npt_h"] else "")
                                                   for x in good) + ".")
        if bad:
            facts.append("Low success: " + "; ".join(f"{x['label']} ({x['cured']}/{x['attempts']})" for x in bad) + ".")
        for e in full_events[:4]:
            facts.append(f"{e['well_id']}: {e['summary']}." + cite(e["citations"][0] if e["citations"] else None, e["well_id"]))
    else:
        facts.append(f"No recorded {hz_label} matched this question in {len(exposed)} offset wells in scope.")
    for l in lessons[:3]:
        facts.append("Lesson (" + (l["well_id"] or "?") + "): " + l["text"] + cite(l["citation"], f"lesson {l['well_id']}"))

    text = " ".join(facts)
    generated = llm_summarize(question, facts, cites)
    return {"question": question, "parsed": pq.to_dict(), "answer": generated or text, "mode": "llm" if generated else "extractive",
            "facts": facts, "citations": cites, "events": [r["event"] for r in events][:15],
            "stats": {"n_events": len(full_events), "n_wells_exposed": len(exposed)}}
