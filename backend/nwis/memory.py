"""Institutional memory: cited shift-handover briefs and after-action reviews.

Both are *extractive first*: every statement is assembled from recorded facts (the live session, the
knowledge base) and carries a numbered citation to a report page or data record. An optional on-prem LLM
may rephrase the facts, but the citation guard in `llm.py` drops any sentence that does not cite them.
"""
from __future__ import annotations

import numpy as np

from .domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE, MITIGATION_BY_CODE
from .kb import KnowledgeBase
from .llm import llm_summarize
from .risk.recommend import recommend

LEVEL_ORDER = {"critical": 0, "warning": 1, "watch": 2, "info": 3}
MATERIAL_GAP = 0.10   # adjusted cure-rate gap needed before a review says another first response would be better


class _Cites:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, c: dict | None, label: str) -> str:
        if not c:
            return ""
        for it in self.items:   # the same source span keeps its number
            if (it["doc_id"], it["page_no"], it["start"]) == (c.get("doc_id"), c.get("page_no"), c.get("start")):
                return f" [{it['n']}]"
        self.items.append({"n": len(self.items) + 1, "label": label,
                           **{k: c.get(k) for k in ("doc_id", "page_no", "start", "end", "text", "title")}})
        return f" [{len(self.items)}]"


def _fm(code: str | None) -> str:
    return FORMATION_BY_CODE[code].name if code in FORMATION_BY_CODE else (code or "unknown formation")


def _hz(code: str) -> str:
    return HAZARD_BY_CODE[code].label if code in HAZARD_BY_CODE else code


def _mit(code: str) -> str:
    m = MITIGATION_BY_CODE.get(code)
    return m.label if m else code


# ---------------------------------------------------------------------------------------------- handover
def handover_brief(session, hours: float = 12.0) -> dict:
    """End-of-shift brief for the live well: what was drilled, what happened, what is open, what is next."""
    kb: KnowledgeBase = session.kb
    st = session.status()
    t_now = st["t"]
    t0 = t_now - hours * 3600
    idx = np.nonzero(session.data["t"][: max(session.i, 1)] >= t0)[0]
    md_start = float(session.data["md"][idx[0]]) if len(idx) else st["md"]
    cites = _Cites()
    sections: list[dict] = []

    # 1. progress
    geo = [e for e in session.history_events if e["t"] >= t0]
    prog = [f"Drilled {md_start:,.0f} → {st['md']:,.0f} m MD ({st['md'] - md_start:,.0f} m) in {_fm(st['formation'])}; "
            f"bit {st['tvd']:,.0f} m TVD. MW {st['mw']:.2f} ppg, ECD {st['ecd']:.2f} ppg."]
    for e in geo:
        prog.append(e["message"] + ".")
    w = st.get("window") or {}
    bounds = [f"MW ≥ {w['min_mw']} ppg" if w.get("min_mw") else None,
              f"ECD ≤ {w['max_ecd']} ppg" if w.get("max_ecd") else None]
    bounds = [b for b in bounds if b]
    if bounds:
        prog.append(f"Offset-derived window in {_fm(st['formation'])}: {', '.join(bounds)}.")
    sections.append({"heading": "Progress", "items": prog})

    # 2. what happened this shift
    happened = []
    for a in sorted(session.alerts.alerts.values(), key=lambda a: a.t):
        if a.source == "look-ahead" or a.hazard == "GEO":
            continue
        evs = [h for h in a.history if h["t"] is not None and h["t"] >= t0]
        if not evs and a.t < t0:
            continue
        acked = any("acknowledged" in h["event"] for h in a.history)
        line = (f"{a.level.upper()} {a.title} at {a.md:,.0f} m ({a.status}"
                f"{', acknowledged' if acked and a.status != 'acknowledged' else ''}).")
        if a.evidence:
            ev = a.evidence[0]
            line += f" Offsets saw this too: {ev['well_id']} at {ev['md']:,.0f} m." + cites.add(ev.get("citation"), ev["well_id"])
        happened.append(line)
    if session.digest:
        held = [d for d in session.digest if d["t"] >= t0]
        if held:
            happened.append(f"{len(held)} lower-priority signal(s) were held in the digest by the alarm budget "
                            f"({', '.join(sorted({d['title'] for d in held}))[:160]}).")
    sections.append({"heading": "Alerts this shift", "items": happened or ["No real-time alerts this shift."]})

    # 3. still open
    open_ = [a for a in session.alerts.alerts.values() if a.status != "cleared" and a.source != "look-ahead" and a.hazard != "GEO"]
    open_.sort(key=lambda a: LEVEL_ORDER.get(a.level, 9))
    sections.append({"heading": "Open items to hand over",
                     "items": [f"{a.level.upper()}: {a.title} — {a.message}" for a in open_[:6]] or ["Nothing open."]})

    # 4. next 300 m
    ahead = []
    offsets = {o["well_id"] for o in session.profile["offsets"]}
    for z in [z for z in session.zones if z["md1"] >= st["md"] and z["md0"] - st["md"] <= 300][:4]:
        dist = z["md0"] - st["md"]
        where = "now inside" if dist <= 0 else f"{dist:,.0f} m ahead"
        line = (f"{_hz(z['hazard'])} zone in {_fm(z['formation'])} at {z['md0']:,.0f}–{z['md1']:,.0f} m ({where}): "
                f"{z['n_events']} of {z['n_exposed']} offsets that drilled it had it (90% CI {z['lo']:.0%}–{z['hi']:.0%}).")
        if z.get("evidence"):
            ev = z["evidence"][0]
            line += cites.add(ev.get("citation"), ev["well_id"])
        rec = recommend(kb, z["hazard"], z["formation"], offsets)
        best = next((x for x in rec["actions"] if x["verdict"] == "recommended"), None)
        if best:
            line += (f" If it happens: {best['label']} worked {best['cured']}/{best['attempts']} times "
                     f"(case-mix-adjusted cure rate {best['cure_rate_adjusted']:.0%}).")
        avoid = next((x for x in rec["actions"] if x["verdict"] == "avoid"), None)
        if avoid:
            line += f" Low success offsets: {avoid['label']} ({avoid['cured']}/{avoid['attempts']})."
        ahead.append(line)
    sections.append({"heading": "Next 300 m", "items": ahead or ["No offset hazard zones in the next 300 m."]})

    facts = [i for s in sections for i in s["items"]]
    generated = llm_summarize("Write the drilling shift handover.", facts, cites.items)
    return {"title": f"Shift handover — {session.well.name}", "well_id": session.well.id, "hours": hours,
            "period": {"t0": t0, "t1": t_now, "md0": round(md_start, 1), "md1": st["md"]},
            "sections": sections, "citations": cites.items, "summary": generated, "mode": "llm" if generated else "extractive",
            "session_id": session.session_id}


# ---------------------------------------------------------------------------------------------- AAR
def after_action_review(kb: KnowledgeBase, event_id: str) -> dict | None:
    """Draft an after-action review for a recorded NPT event, grounded in its reports and offset outcomes."""
    e = next((x for x in kb.events if x["id"] == event_id), None)
    if e is None:
        return None
    cites = _Cites()
    hz, fm = e["hazard"], e.get("formation")
    severity = f" ({e['severity']})" if e.get("severity") else ""
    rate = f", {e['rate_bbl_hr']:.0f} bbl/hr" if e.get("rate_bbl_hr") else ""
    what = f"{_hz(hz)} in {e['well_id']} at {e['md']:,.0f} m MD in {_fm(fm)}{severity}{rate}."
    what += cites.add(e["citations"][0] if e.get("citations") else None, e["well_id"])
    timeline = []
    for c in sorted(e.get("citations", []), key=lambda c: (c.get("doc_id") or "", c.get("page_no") or 0))[:6]:
        timeline.append(f"{c.get('title') or c.get('doc_id')}, p.{c.get('page_no')}: \"{(c.get('text') or '').strip()[:220]}\""
                        + cites.add(c, e["well_id"]))
    actions = []
    for i, a in enumerate(e.get("actions", [])):
        actions.append(f"{i + 1}. {_mit(a['code'])}: {'cured' if a['success'] else 'did not cure'}"
                       + (f" — \"{a['text'][:140]}\"" if a.get("text") else ""))
    ctx = [f"Mud weight {e['mw_ppg']} ppg" if e.get("mw_ppg") else None,
           f"ECD {e['ecd_ppg']} ppg" if e.get("ecd_ppg") else None,
           f"cause recorded as {e['cause']}" if e.get("cause") else None,
           f"NPT {e['npt_hours']:.1f} h" if e.get("npt_hours") else None]
    ctx = [c for c in ctx if c]

    rec = recommend(kb, hz, fm, None)
    ranked = [x for x in rec["actions"] if x["code"] in MITIGATION_BY_CODE or x["code"] == "BACKOFF"]
    best = ranked[0] if ranked else None
    tried = [a["code"] for a in e.get("actions", [])]
    first = tried[0] if tried else None
    better = []
    f_stats = next((x for x in ranked if x["code"] == first), None) if first else None
    f_rate = f_stats["cure_rate_adjusted"] if f_stats else None
    # only claim a better first response when the adjusted gap is material (thin offset data is noisy)
    clearly_better = bool(best and first and best["code"] != first and
                          (f_rate is None or best["cure_rate_adjusted"] - f_rate >= MATERIAL_GAP))
    if clearly_better:
        better.append(f"Across {rec['n_events']} {_hz(hz).lower()} events ({rec['scope']}), {best['label']} has the best "
                      f"case-mix-adjusted cure rate ({best['cure_rate_adjusted']:.0%}, {best['cured']}/{best['attempts']})"
                      + (f" versus {f_rate:.0%} for {_mit(first)}, which was tried first here." if f_rate is not None else "."))
    elif best and first == best["code"]:
        better.append(f"The first response ({best['label']}) is also the best-performing option across "
                      f"{rec['n_events']} offset events ({best['cure_rate_adjusted']:.0%} case-mix-adjusted).")
    elif best and first:
        better.append(f"The first response ({_mit(first)}, {f_rate:.0%} case-mix-adjusted) is as good as any option across "
                      f"{rec['n_events']} offset events: no alternative is clearly better (best: {best['label']}, "
                      f"{best['cure_rate_adjusted']:.0%})." if f_rate is not None else
                      f"The first response ({best['label']}) is the best-performing option across {rec['n_events']} offset events.")
    similar = [x for x in kb.events if x["hazard"] == hz and x.get("formation") == fm and x["id"] != e["id"]]
    sim_lines = []
    for x in sorted(similar, key=lambda x: -(x.get("npt_hours") or 0))[:3]:
        cured = next((_mit(a["code"]) for a in x.get("actions", []) if a["success"]), None)
        sim_lines.append(f"{x['well_id']} at {x['md']:,.0f} m: {x['summary']}"
                         + (f" Cured by {cured}." if cured else " Not cured by recorded actions.")
                         + cites.add(x["citations"][0] if x.get("citations") else None, x["well_id"]))
    first_ok = e.get("actions") and e["actions"][0]["success"]
    lesson = (f"{_fm(fm)} {_hz(hz).lower()} at ~{e['md']:,.0f} m ({e['well_id']}): "
              + (f"first response {_mit(first)} {'worked' if first_ok else 'failed'}. " if first else "")
              + (f"Offset evidence favours {best['label']} as the first response "
                 f"({best['cure_rate_adjusted']:.0%} case-mix-adjusted)." if clearly_better
                 else f"Offset evidence confirms {best['label']} as the best first response." if best and first == best["code"]
                 else "Offset evidence shows no clearly better first response." if best else ""))
    sections = [{"heading": "What happened", "items": [what] + ([", ".join(ctx) + "."] if ctx else [])},
                {"heading": "Timeline from the reports", "items": timeline or ["No report excerpts available."]},
                {"heading": "Actions and outcomes", "items": actions or ["No actions recorded."]},
                {"heading": "What the offset evidence says", "items": better or ["Not enough offset evidence to compare."]},
                {"heading": "Similar events elsewhere", "items": sim_lines or ["None recorded."]}]
    facts = [i for s in sections for i in s["items"]]
    generated = llm_summarize("Write a short after-action review of this drilling event.", facts, cites.items)
    return {"event_id": event_id, "title": f"After-action review — {_hz(hz)}, {e['well_id']} @ {e['md']:,.0f} m",
            "well_id": e["well_id"], "hazard": hz, "formation": fm, "sections": sections, "citations": cites.items,
            "draft_lesson": lesson.strip(), "summary": generated, "mode": "llm" if generated else "extractive",
            "status": "approved" if any(l["id"] == f"AAR-{event_id}" for l in kb.lessons) else "draft"}


def approve_aar(kb: KnowledgeBase, event_id: str, text: str, reviewer: str) -> dict:
    """Store an approved review as a first-class, cited lesson (it then shows up in search, Ask and briefs)."""
    e = next((x for x in kb.events if x["id"] == event_id), None)
    if e is None:
        raise KeyError(event_id)
    c = e["citations"][0] if e.get("citations") else {}
    kb.db.insert("lessons", {"id": f"AAR-{event_id}", "well_id": e["well_id"], "hazard": e["hazard"],
                             "formation": e.get("formation"), "text": f"{text.strip()} (After-action review approved by {reviewer})",
                             "doc_id": c.get("doc_id"), "page_no": c.get("page_no"), "start": c.get("start"),
                             "end": c.get("end")})
    kb.db.commit()
    return {"ok": True, "lesson_id": f"AAR-{event_id}"}
