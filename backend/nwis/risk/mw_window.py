"""Probabilistic, offset-derived mud-weight window (differentiator #2).

Offset outcomes are *censored* pressure evidence:
  * losses at ECD x         -> loss/fracture gradient <= x
  * no losses at ECD x      -> loss gradient > x (censored)
  * kick / gas at MW x      -> pore pressure >= x
  * cavings at MW x         -> collapse gradient >= x
Per formation we fit distance/recency-weighted logistic curves
P(loss | ECD), P(kick | MW), P(instability | MW) and read the window off them.
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression

from ..correlation import Target
from ..domain.ontology import FORMATION_ORDER
from ..kb import KnowledgeBase
from .evidence import build_offsets

GRID = np.round(np.arange(8.4, 14.01, 0.05), 2)
RISK_LEVEL = 0.10          # lower-bound (kick / collapse) acceptable risk
LOSS_ALERT = 0.25          # ECD alert level
LOSS_MARGIN_PPG = 0.2      # window top = P50 loss ECD minus margin


def _fit_curve(x: np.ndarray, y: np.ndarray, w: np.ndarray, increasing: bool, year: np.ndarray | None = None,
               target_year: int | None = None):
    """Weighted logistic fit of P(event | mud) (optionally depletion-aware via spud year).

    Returns (curve on GRID evaluated at target_year, status)."""
    if len(x) < 3:
        return None, "insufficient offsets"
    if y.sum() == 0:
        return None, "no events (censored)"
    if y.sum() == len(y):
        return None, "events in all offsets"
    use_year = year is not None and target_year is not None and np.ptp(year) >= 5
    X = (x - 10.0).reshape(-1, 1)
    if use_year:
        X = np.column_stack([X[:, 0], (year - 2010) / 10.0])
    m = LogisticRegression(C=3.0)
    m.fit(X, y, sample_weight=w)
    coef = float(m.coef_[0][0])
    if (increasing and coef <= 0) or (not increasing and coef >= 0):
        return None, "no monotonic trend"
    G = (GRID - 10.0).reshape(-1, 1)
    if use_year:
        G = np.column_stack([G[:, 0], np.full(len(GRID), (target_year - 2010) / 10.0)])
    status = "fitted (depletion-aware)" if use_year and float(m.coef_[0][1]) > 0 else "fitted"
    return m.predict_proba(G)[:, 1], status


def _crossing(curve: np.ndarray, level: float, increasing: bool) -> float | None:
    if curve is None:
        return None
    idx = np.where(curve >= level)[0] if increasing else np.where(curve <= level)[0]
    if len(idx) == 0:
        return None
    return float(GRID[idx[0]])


def mw_window(kb: KnowledgeBase, target: Target, radius_km: float = 10.0, formations: list[str] | None = None) -> dict:
    offsets = build_offsets(kb, target, radius_km)
    formations = formations or [c for c in FORMATION_ORDER if c not in ("ALLUVIUM", "BASEMENT")]
    out = {}
    for fm in formations:
        rows = []
        for o in offsets:
            w = o.well
            span = o.spans.get(fm, 0)
            if span < 0.2:
                continue
            top = w.tops_md.get(fm)
            if top is None:
                continue
            probe_md = top + 20
            sec = w.section_at(probe_md)
            if sec is None:
                continue
            evs = {hz: [e for r, e in o.events.get((fm, hz), [])] for hz in ("LOSS", "KICK", "INSTAB")}
            ecd = max((e.get("ecd_ppg") or sec["ecd_ppg"]) for e in evs["LOSS"]) if evs["LOSS"] else sec["ecd_ppg"]
            mw = sec["mw_ppg"]
            kick = 1.0 if any(e["subtype"] == "kick" for e in evs["KICK"]) else (0.6 if evs["KICK"] else 0.0)
            rows.append({"well_id": w.id, "weight": o.weight, "mw": mw, "ecd": ecd, "loss": int(bool(evs["LOSS"])),
                         "loss_natural": int(bool(evs["LOSS"]) and all(e.get("cause") in ("natural fractures", "unconsolidated formation")
                                                                      for e in evs["LOSS"])),
                         "kick": kick, "instab": int(bool(evs["INSTAB"])), "spud_year": w.spud_year,
                         "kill_mw": max([e["extra"].get("kill_mw", 0) for e in evs["KICK"]] + [0]) or None})
        if not rows:
            continue
        W = np.array([r["weight"] for r in rows])
        ecd = np.array([r["ecd"] for r in rows])
        mw = np.array([r["mw"] for r in rows])
        years = np.array([r["spud_year"] or target.spud_year for r in rows], dtype=float)
        # ECD-driven losses only: natural-fracture / unconsolidated losses are not controlled by MW
        induced = np.array([r["loss"] and not r["loss_natural"] for r in rows], dtype=int)
        loss_curve, loss_status = _fit_curve(ecd, induced, W, True, years, target.spud_year)
        ky = np.array([r["kick"] for r in rows])
        kick_curve, kick_status = _fit_curve(mw, (ky >= 1.0).astype(int), W, False)
        inst_curve, inst_status = _fit_curve(mw, np.array([r["instab"] for r in rows]), W, False)
        loss_p10 = _crossing(loss_curve, RISK_LEVEL, True)
        loss_p50 = _crossing(loss_curve, 0.5, True)
        kick_p10 = _crossing(kick_curve, RISK_LEVEL, False)
        inst_p10 = _crossing(inst_curve, RISK_LEVEL, False)
        # censored fall-backs
        natural = sum(r["loss_natural"] for r in rows)
        if loss_curve is None and loss_status.startswith("no events"):
            loss_note = f"no ECD-induced losses in {len(rows)} offsets up to ECD {ecd.max():.2f} ppg"
        else:
            loss_note = None
        if natural:
            loss_note = ((loss_note + "; ") if loss_note else "") + \
                f"{natural} offset(s) lost returns to natural fractures/unconsolidated sand (not MW-controlled: plan LCM)"
        if loss_status.startswith("fitted (depletion"):
            loss_note = ((loss_note + "; ") if loss_note else "") + f"curve evaluated at {target.spud_year} depletion level"
        kills = [r["kill_mw"] for r in rows if r["kill_mw"]]
        cands = [v for v in (kick_p10, inst_p10) if v is not None]
        lower = max(cands) if cands else None
        upper = round(max(loss_p50 - LOSS_MARGIN_PPG, float(GRID[0])), 2) if loss_p50 else None
        out[fm] = {
            "n_offsets": len(rows), "rows": rows,
            "loss": {"status": loss_status, "ecd_p10": loss_p10, "ecd_p50": loss_p50, "note": loss_note,
                     "curve": None if loss_curve is None else np.round(loss_curve, 4).tolist(),
                     "n_losses": int(sum(r["loss"] for r in rows)), "n_natural": int(natural)},
            "kick": {"status": kick_status, "mw_p10": kick_p10, "kill_mw_max": max(kills) if kills else None,
                     "n_kicks": int((ky >= 1.0).sum()), "n_gas": int(((ky > 0) & (ky < 1.0)).sum()),
                     "curve": None if kick_curve is None else np.round(kick_curve, 4).tolist()},
            "instability": {"status": inst_status, "mw_p10": inst_p10,
                            "curve": None if inst_curve is None else np.round(inst_curve, 4).tolist()},
            "window": {"min_mw": round(lower, 2) if lower else None, "max_ecd": upper},
        }
    return {"grid": GRID.tolist(), "risk_level": RISK_LEVEL, "formations": out, "radius_km": radius_km}


def check_against_window(win: dict, formation: str, mw: float, ecd: float) -> list[dict]:
    """Findings when current/planned mud sits outside the offset-derived window."""
    f = win["formations"].get(formation)
    if not f:
        return []
    grid = np.array(win["grid"])
    out = []
    if f["loss"]["curve"] is not None:
        p = float(np.interp(ecd, grid, f["loss"]["curve"]))
        if p >= LOSS_ALERT:
            out.append({"hazard": "LOSS", "p": round(p, 3), "value": ecd, "limit": f["window"]["max_ecd"],
                        "message": f"ECD {ecd:.2f} ppg gives P(loss)={p:.0%} in {formation.title()} "
                                   f"(offset window max ECD {f['window']['max_ecd'] or '-'} ppg)"})
    if f["kick"]["curve"] is not None:
        p = float(np.interp(mw, grid, f["kick"]["curve"]))
        if p >= RISK_LEVEL:
            out.append({"hazard": "KICK", "p": round(p, 3), "value": mw, "limit": f["window"]["min_mw"],
                        "message": f"MW {mw:.2f} ppg gives P(kick)={p:.0%} in {formation.title()} "
                                   f"(offsets needed >= {f['window']['min_mw'] or '-'} ppg)"})
    if f["instability"]["curve"] is not None:
        p = float(np.interp(mw, grid, f["instability"]["curve"]))
        if p >= 0.2:
            out.append({"hazard": "INSTAB", "p": round(p, 3), "value": mw, "limit": f["instability"]["mw_p10"],
                        "message": f"MW {mw:.2f} ppg gives P(instability)={p:.0%} in {formation.title()}"})
    return out
