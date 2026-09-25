"""Replay evaluation of the live alerting: detections, lead time and false alarms per hour.

Hidden episode truth is used *only* here, to score the replay after the fact; the live engine never sees it.
An alert counts as a detection when it matches the episode's hazard between (onset - 120 m) and (event + 60 m),
the same rule as tests/test_system.py. Every other real-time or fused alert counts as a false alarm.
"""
from __future__ import annotations

import numpy as np

from ..kb import KnowledgeBase
from .engine import LiveSession

BUDGETS = (0.5, 1.0, 2.0, 4.0)


def add_nuisance(data: dict, level: float, seed: int = 26121) -> dict:
    """Stress test: rig-floor nuisances that trip naive thresholds without any downhole problem.
    - flow-out paddle noise and short surges (pipe movement, shaker blinding)
    - pit-volume steps from mud transfers / additions (the classic false kick or loss)
    - background-gas spikes (connection gas, coal)
    - torque noise and brief stick-slip bursts
    `level` scales everything; 0 returns the data unchanged."""
    if level <= 0:
        return data
    rng = np.random.default_rng(seed)
    d = {k: v.copy() for k, v in data.items()}
    n = len(d["t"])
    on = (d["state"] == 0) & (d["flow_in"] > 50)
    d["flow_out"] = d["flow_out"] + on * rng.normal(0, 1.2 * level, n)
    for i in rng.choice(n, size=int(n * 0.004 * level), replace=False):          # 3-6 sample flow surges
        k = int(rng.integers(3, 7))
        d["flow_out"][i:i + k] += on[i:i + k] * rng.choice([-1, 1]) * rng.uniform(4, 7) * level
    dpit = np.zeros(n)
    for i in rng.choice(n, size=int(n * 0.003 * level), replace=False):          # mud transfers: pit ramps
        k = int(rng.integers(4, 12))
        dpit[i:i + k] += rng.choice([-1, 1]) * rng.uniform(8, 20) * level / k   # bbl per sample, permanent step
    d["pit"] = d["pit"] + np.cumsum(dpit)
    for i in rng.choice(n, size=int(n * 0.006 * level), replace=False):          # gas spikes
        d["gas"][i:i + 3] += rng.uniform(2, 6) * level
    d["torque"] = d["torque"] + on * rng.normal(0, 0.5 * level, n)
    for i in rng.choice(n, size=int(n * 0.003 * level), replace=False):          # stick-slip bursts
        k = int(rng.integers(2, 6))
        d["torque"][i:i + k] *= 1 + on[i:i + k] * rng.uniform(0.25, 0.5) * level
    return d


def replay(kb: KnowledgeBase, model=None, budget: float | None = 2.0, nuisance: float = 0.0) -> dict:
    s = LiveSession(kb, model, None)
    if nuisance > 0:
        s.data = add_nuisance(s.data, nuisance)
        s.reset(0)
    if budget is None:
        s.conformal.enabled = False
    else:
        s.set_budget(budget)
    opened: dict[str, dict] = {}
    while True:
        r = s.step(200)
        for a in r["alerts"]:
            if a["source"] in ("real-time", "fused") and a["id"] not in opened:
                opened[a["id"]] = a
        if r["done"]:
            break
    hours = float(s.data["t"][-1] - s.data["t"][0]) / 3600.0
    alerts = list(opened.values())
    matched: set[str] = set()
    episodes = []
    for ep in s.episodes:
        lo, hi = ep.get("onset_md", ep["md"]) - 120, ep["md"] + 60
        hits = sorted([a for a in alerts if a["hazard"] == ep["hazard"] and lo <= a["md"] <= hi], key=lambda a: a["t"])
        matched |= {a["id"] for a in alerts if lo - 40 <= a["md"] <= hi + 40}   # related alerts around an episode
        first = hits[0] if hits else None
        episodes.append({"id": ep["id"], "hazard": ep["hazard"], "label": ep.get("label"), "detected": bool(hits),
                         "first_alert_md": first["md"] if first else None,
                         "metres_before_event": round(ep["md"] - first["md"], 1) if first else None,
                         "corroborated": any(a["corroborated"] for a in hits)})
    false = [a for a in alerts if a["id"] not in matched]
    return {"budget_per_hour": budget, "gated": budget is not None, "nuisance": nuisance, "hours": round(hours, 1),
            "alerts": len(alerts), "detected": sum(e["detected"] for e in episodes), "episodes": episodes,
            "false_alarms": len(false), "false_per_hour": round(len(false) / max(hours, 1e-6), 3),
            "false_alarm_titles": sorted({a["title"] for a in false})[:8],
            "suppressed": s.conformal.state()["suppressed"]}


def top_pick_eval(kb: KnowledgeBase, model=None) -> dict:
    """Accuracy of fully automatic (DTW-only) top picks vs the hidden truth, and whether the live
    look-ahead still catches every episode when re-anchoring relies on DTW alone."""
    from .. import config
    from .toppick import pick_errors
    truth = kb.db.kv_get("active_truth_tops", {})
    out = {}
    for mode in ("dtw", "mudlogger"):
        old = config.TOP_PICK_MODE
        config.TOP_PICK_MODE = mode
        try:
            s = LiveSession(kb, model, None)
        finally:
            config.TOP_PICK_MODE = old
        opened: dict[str, dict] = {}
        while True:
            r = s.step(200)
            opened.update({a["id"]: a for a in r["alerts"] if a["source"] in ("real-time", "fused")})
            if r["done"]:
                break
        picks = s.dtw_picks if mode == "dtw" else [{"formation": c, "tvd": v} for c, v in s.picked.items()]
        detected = sum(1 for ep in s.episodes if any(
            a["hazard"] == ep["hazard"] and ep.get("onset_md", ep["md"]) - 120 <= a["md"] <= ep["md"] + 60
            for a in opened.values()))
        out[mode] = {**pick_errors(picks, truth), "picks": picks, "episodes_detected": detected,
                     "n_episodes": len(s.episodes)}
    return out


def budget_sweep(kb: KnowledgeBase, model=None, nuisance_levels: tuple[float, ...] = (0.0, 1.0)) -> dict:
    rows = []
    for nz in nuisance_levels:
        rows += [replay(kb, model, None, nz)] + [replay(kb, model, b, nz) for b in BUDGETS]
    return {"rows": rows, "n_episodes": len(rows[0]["episodes"]), "budgets": list(BUDGETS),
            "nuisance_levels": list(nuisance_levels)}


def evaluate_live(kb: KnowledgeBase, model=None) -> dict:
    """Everything the Analytics view shows about live alerting; stored in kv 'live_eval'."""
    import datetime as dt
    res = {"budget": budget_sweep(kb, model), "top_picks": top_pick_eval(kb, model),
           "computed": dt.datetime.now().isoformat(timespec="seconds")}
    kb.db.kv_set("live_eval", res)
    return res
