"""Analog Replay: automatic case capture from offset logs + kNN matching of the live
drilling state -> 'what happened next' in the most similar historical situations.

Unlike classic case-based reasoning (e.g. DrillEdge), cases are not hand-built: every
30 m window of every offset well is a case, labelled with the events that followed.
"""
from __future__ import annotations

import numpy as np

from .. import config
from ..domain.ontology import FORMATION_ORDER
from ..kb import KnowledgeBase

WIN_M = 30.0
AHEAD_M = 60.0
STEP_M = 10.0


def window_features(md, torque, spp, rop, gas, flow_out, ecd, mw, dxc, fm_idx) -> np.ndarray:
    """Scale-free features of a depth window."""
    def slope(y):
        if len(y) < 3 or np.ptp(md) < 1:
            return 0.0
        return float(np.polyfit(md - md[0], y, 1)[0] * WIN_M)
    t_ratio = torque / (np.median(torque) + 1e-6)
    f = [
        float(np.mean(torque[-5:]) / (np.median(torque) + 1e-6)), slope(t_ratio), float(np.std(t_ratio)),
        float(np.mean(spp[-5:]) / (np.median(spp) + 1e-6)), float(np.std(spp / (np.median(spp) + 1e-6))),
        float(np.mean(rop[-5:]) / (np.median(rop) + 1e-6)), slope(rop / (np.median(rop) + 1e-6)),
        float(np.log1p(np.mean(gas[-5:]))), slope(np.log1p(gas)),
        float(np.mean(flow_out[-5:]) - 100) / 10.0,
        float(np.mean(ecd - mw)),
        slope(dxc / (np.median(dxc) + 1e-6)),
    ]
    fm = np.zeros(len(FORMATION_ORDER))
    fm[int(round(fm_idx))] = 1.2
    return np.concatenate([np.array(f), fm])


class AnalogIndex:
    def __init__(self, kb: KnowledgeBase):
        feats, meta = [], []
        for w in kb.offsets():
            p = config.LOGS_DIR / f"{w.id}.npz"
            if not p.exists():
                continue
            z = np.load(p)
            md = z["md"].astype(float)
            evs = [e for e in w.events if e["md"] is not None]
            for end in np.arange(WIN_M + 200, md[-1] - 5, STEP_M):
                sel = (md > end - WIN_M) & (md <= end)
                if sel.sum() < 8:
                    continue
                f = window_features(md[sel], z["torque"][sel], z["spp"][sel], z["rop"][sel], z["gas"][sel],
                                    z["flow_out"][sel], z["ecd"][sel], z["mw"][sel], z["dxc"][sel], z["fm"][sel][-1])
                nxt = [e for e in evs if end < e["md"] <= end + AHEAD_M]
                feats.append(f)
                meta.append({"well_id": w.id, "md": float(end), "formation": FORMATION_ORDER[int(z["fm"][sel][-1])],
                             "next": [{"id": e["id"], "hazard": e["hazard"], "md": e["md"], "summary": e["summary"],
                                       "resolved": e.get("resolved"), "citation": e["citations"][0] if e["citations"] else None}
                                      for e in nxt]})
        width = 12 + len(FORMATION_ORDER)
        self.X = np.array(feats) if feats else np.zeros((0, width))
        # no offset logs at all (e.g. public data without drilling-parameter logs): an empty index, not a crash
        self.mu = self.X.mean(axis=0) if len(self.X) else np.zeros(width)
        self.sd = self.X.std(axis=0) + 1e-6 if len(self.X) else np.ones(width)
        self.sd[12:] = 1.0
        self.Z = (self.X - self.mu) / self.sd
        self.meta = meta

    def query(self, f: np.ndarray, k: int = 5, exclude_well: str | None = None) -> dict:
        if len(self.Z) == 0:
            return {"analogs": [], "forecast": {}}
        z = (f - self.mu) / self.sd
        d = np.linalg.norm(self.Z - z, axis=1)
        order = np.argsort(d)
        out, seen = [], set()
        for i in order:
            m = self.meta[i]
            if m["well_id"] == exclude_well or m["well_id"] in seen:
                continue
            seen.add(m["well_id"])
            out.append({**m, "similarity": round(float(np.exp(-d[i] / 4)), 3)})
            if len(out) >= k:
                break
        forecast: dict[str, int] = {}
        for a in out:
            for hz in {n["hazard"] for n in a["next"]}:
                forecast[hz] = forecast.get(hz, 0) + 1
        return {"analogs": out, "forecast": forecast, "k": len(out)}


def live_features(samples: list[dict]) -> np.ndarray | None:
    on = [s for s in samples if s.get("state", 0) == 0 and s["flow_in"] > 50]
    if len(on) < 8:
        return None
    md = np.array([s["md"] for s in on])
    sel = md > md[-1] - WIN_M
    if sel.sum() < 6:
        sel = slice(-min(len(on), 30), None)
    g = lambda k: np.array([s[k] for s in on])[sel]
    return window_features(md[sel], g("torque"), g("spp"), g("rop"), g("gas"), g("flow_out"), g("ecd"), g("mw"),
                           g("dxc"), on[-1]["fm_est"])
