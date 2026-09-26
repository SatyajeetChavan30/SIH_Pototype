"""Outcome-weighted mitigation recommendations ("what actually worked in offsets")."""
from __future__ import annotations

import math

import numpy as np

from ..domain.ontology import HAZARD_BY_CODE, MITIGATION_BY_CODE, MITIGATIONS
from ..kb import KnowledgeBase


CONFOUNDED_GAP = 0.15   # |adjusted - crude| at or above this is flagged as confounded by case difficulty


def _stratum(e: dict, attempt_idx: int) -> tuple[str, str]:
    """Pre-treatment difficulty of the case when this action was tried: reported severity, and whether an
    earlier treatment had already failed. NPT is an *outcome* and is deliberately not used (adjusting for a
    post-treatment variable would bias the estimate)."""
    return (str(e.get("severity") or "unknown"), "first" if attempt_idx == 0 else "after-failure")


def adjusted_efficacy(events: list[dict]) -> dict[str, dict]:
    """Case-mix-adjusted cure rate per mitigation (indirect standardisation, observed vs expected).

    Raw cure rates are confounded: cement plugs are used on total losses and after other treatments have
    failed, fine LCM on seepage, so a raw comparison penalises whatever is used on hard cases. For each
    mitigation m we compare what it achieved with what an *average* treatment achieves on the same cases:

        expected(m) = sum over m's own attempts of the pooled cure rate in that attempt's difficulty stratum
        adjusted(m) = overall rate + (observed(m) - expected(m))

    Indirect standardisation (as in standardised mortality ratios) is used rather than direct
    standardisation because offset data are small: it never extrapolates a mitigation into strata where it
    was not tried.
    """
    attempts: list[tuple[str, tuple, int]] = []
    for e in events:
        for i, a in enumerate(e.get("actions", [])):
            attempts.append((a["code"], _stratum(e, i), int(bool(a["success"]))))
    if not attempts:
        return {}
    n_s: dict[tuple, int] = {}
    k_s: dict[tuple, int] = {}
    for _, s, ok in attempts:
        n_s[s] = n_s.get(s, 0) + 1
        k_s[s] = k_s.get(s, 0) + ok
    pooled = {s: (k_s[s] + 1) / (n_s[s] + 2) for s in n_s}          # Laplace-smoothed stratum cure rate
    overall = (sum(k_s.values()) + 1) / (len(attempts) + 2)
    by_m: dict[str, dict] = {}
    for code, s, ok in attempts:
        m = by_m.setdefault(code, {"n": 0, "k": 0, "exp": 0.0, "cells": {}})
        m["n"] += 1
        m["k"] += ok
        m["exp"] += pooled[s]
        c = m["cells"].setdefault(s, [0, 0])
        c[0] += 1
        c[1] += ok
    out = {}
    for code, m in by_m.items():
        observed = (m["k"] + 1) / (m["n"] + 2)
        expected = (m["exp"] + 1) / (m["n"] + 2)        # same smoothing as observed so they are comparable
        # the observed-minus-expected lift is shrunk by n/(n+2): two lucky attempts cannot claim a 97% cure rate
        adj = float(np.clip(overall + (observed - expected) * m["n"] / (m["n"] + 2), 0.01, 0.99))
        out[code] = {"cure_rate_adjusted": round(adj, 3), "expected_on_same_cases": round(expected, 3),
                     "confounded": abs(adj - observed) >= CONFOUNDED_GAP,
                     "strata": [{"severity": s[0], "attempt": s[1], "n": n, "k": k, "pooled_rate": round(pooled[s], 3)}
                                for s, (n, k) in m["cells"].items()]}
    return out


def efficacy(events: list[dict]) -> list[dict]:
    """Per mitigation: attempts, cure rate (Laplace-smoothed), first-try cure rate, NPT of resolved events,
    plus the severity-adjusted cure rate used for ranking."""
    adjusted = adjusted_efficacy(events)
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
        adj = adjusted.get(code, {})
        adj_rate = adj.get("cure_rate_adjusted", rate)
        out.append({"code": code, "label": m.label if m else ("Back-off and fish" if code == "BACKOFF" else code),
                    "attempts": s["n"], "cured": s["k"], "cure_rate": round(s["k"] / s["n"], 3),
                    "cure_rate_smoothed": round(rate, 3), "cure_rate_adjusted": adj_rate,
                    "confounded": adj.get("confounded", False), "adjustment": adj.get("strata", []),
                    "first_try": f"{s['first_k']}/{s['first_n']}" if s["first_n"] else "-",
                    "median_npt_h": round(float(np.median(s["npt_resolved"])), 1) if s["npt_resolved"] else None,
                    "wells": sorted(s["wells"]), "events": s["events"][:12],
                    "score": round(adj_rate * math.log(2 + s["n"]), 3),
                    "preventive": m.preventive if m else ""})
    for x in out:
        x["verdict"] = "recommended" if x["cure_rate_adjusted"] >= 0.55 else (
            "avoid" if x["cure_rate_adjusted"] <= 0.35 and x["cure_rate"] <= 0.3 and x["attempts"] >= 2 else "mixed")
    rank = {"recommended": 0, "mixed": 1, "avoid": 2}
    # the adjusted rate already shrinks thin evidence, so rank by it directly (more attempts break ties)
    out.sort(key=lambda x: (rank[x["verdict"]], -x["cure_rate_adjusted"], -x["attempts"]))
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
