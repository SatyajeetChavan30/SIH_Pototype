"""Automatic formation-top picking while drilling, by dynamic time warping (DTW) against offset gamma-ray logs.

For the next formation expected below the bit, every nearby offset well contributes a *template*: its
smoothed gamma-ray (GR) profile from 80 m above to 24 m below its own picked top. The live well's GR,
binned to 2 m TVD and smoothed the same way, is searched with open-begin/open-end (subsequence) DTW. The
warping path maps the offset's top onto a live depth. A top is picked when at least MIN_AGREE offsets
land within AGREE_M of their median and the pick lies inside the offset-predicted prognosis window
(+/- 2 sigma). Uncertainty is the robust spread of the per-offset estimates.

Why GR: formation boundaries in the Upper Assam column change the sand/shale/coal/limestone mix
(e.g. Girujan clay -> Tipam sand, Kopili shale -> Sylhet limestone), which moves smoothed GR strongly.
Reference: DTW top picking, US11914099B2; automated multi-well correlation, Basin Research 2023.
"""
from __future__ import annotations

import numpy as np

from .. import config
from ..domain.ontology import BOTTOM, FORMATION_ORDER
from ..kb import KnowledgeBase

BIN_M = 2.0
SMOOTH_BINS = 9          # ~18 m moving average: suppresses bed-scale noise, keeps formation-scale GR level
ABOVE_M = 80.0           # template span above the offset top
BELOW_M = 40.0           # template span below it: the bit must have drilled this far past the live top
LIVE_SPAN_M = 260.0
N_REFS = 6
MIN_AGREE = 3
AGREE_M = 15.0
MOVE_PENALTY = 0.35      # extra cost for non-diagonal DTW steps (discourages extreme stretching)
CONTRAST_M = 30.0        # window either side of a pick for the GR-contrast sanity check


def _bin(tvd: np.ndarray, gr: np.ndarray, z0: float, z1: float) -> tuple[np.ndarray, np.ndarray]:
    edges = np.arange(z0, z1 + BIN_M, BIN_M)
    idx = np.digitize(tvd, edges) - 1
    ok = (idx >= 0) & (idx < len(edges) - 1)
    sums = np.bincount(idx[ok], weights=gr[ok], minlength=len(edges) - 1)
    cnt = np.bincount(idx[ok], minlength=len(edges) - 1)
    centres = edges[:-1] + BIN_M / 2
    with np.errstate(invalid="ignore", divide="ignore"):
        vals = sums / cnt
    good = cnt > 0
    if good.sum() < 2:
        return centres, np.full(len(centres), np.nan)
    vals = np.interp(centres, centres[good], vals[good])   # fill small gaps (connections)
    return centres, vals


def _smooth(v: np.ndarray) -> np.ndarray:
    k = np.ones(SMOOTH_BINS) / SMOOTH_BINS
    pad = SMOOTH_BINS // 2
    return np.convolve(np.pad(v, pad, mode="edge"), k, mode="valid")


def subsequence_dtw(template: np.ndarray, series: np.ndarray) -> tuple[float, np.ndarray]:
    """Align the whole template to the best-matching stretch of `series`.

    Returns (normalised cost, mapping) where mapping[i] is the series index matched to template index i.
    """
    m, n = len(template), len(series)
    c = np.abs(template[:, None] - series[None, :])
    pen = MOVE_PENALTY * float(np.median(c)) + 1e-9
    D = np.full((m, n), np.inf)
    P = np.zeros((m, n), dtype=np.int8)          # 0 diag, 1 up (template advances), 2 left (series advances)
    D[0] = c[0]                                  # open begin: the template may start anywhere in the series
    for i in range(1, m):
        Di, Dp, ci = D[i], D[i - 1], c[i]
        for j in range(n):
            best, move = Dp[j] + pen, 1
            if j > 0:
                if Dp[j - 1] < best:
                    best, move = Dp[j - 1], 0
                if Di[j - 1] + pen < best:
                    best, move = Di[j - 1] + pen, 2
            Di[j] = ci[j] + best
            P[i, j] = move
    j = int(np.argmin(D[-1]))                    # open end
    cost = float(D[-1, j]) / m
    mapping = np.zeros(m, dtype=int)
    i = m - 1
    while i >= 0:
        mapping[i] = j
        mv = P[i, j]
        if i == 0:
            break
        if mv == 0:
            i, j = i - 1, j - 1
        elif mv == 1:
            i -= 1
        else:
            j -= 1
    return cost, mapping


class DTWTopPicker:
    def __init__(self, kb: KnowledgeBase, lat: float, lon: float, exclude: set[str], refs: list[dict] | None = None):
        self.refs: list[dict] = list(refs or [])
        self.tvd: list[float] = []
        self.gr: list[float] = []
        self.last_eval_tvd = -1e9
        if refs is not None:
            return
        for w, d in kb.nearby(lat, lon, 25.0, exclude):
            p = config.LOGS_DIR / f"{w.id}.npz"
            if not p.exists() or not w.tops_tvd:
                continue
            z = np.load(p)
            self.refs.append({"well_id": w.id, "dist": d, "tvd": z["tvd"].astype(float), "gr": z["gr"].astype(float),
                              "tops": dict(w.tops_tvd)})
            if len(self.refs) >= N_REFS * 2:
                break

    def add(self, s: dict) -> None:
        if s.get("state", 0) == 0 and s.get("rop", 0) > 0 and s["gr"] > 0:
            self.tvd.append(s["tvd"])
            self.gr.append(s["gr"])

    def _template(self, ref: dict, code: str) -> tuple[np.ndarray, np.ndarray, float] | None:
        top = ref["tops"].get(code)
        if top is None:
            return None
        z0, z1 = top - ABOVE_M - 40, top + BELOW_M + 40     # extra margin so smoothing edges don't bias the template
        m = (ref["tvd"] >= z0 - 5) & (ref["tvd"] <= z1 + 5)
        if m.sum() < 20:
            return None
        centres, vals = _bin(ref["tvd"][m], ref["gr"][m], z0, z1)
        sm = _smooth(vals)
        keep = (centres >= top - ABOVE_M) & (centres <= top + BELOW_M)
        return sm[keep], centres[keep], top

    def try_pick(self, code: str, prior_tvd: float, prior_sd: float, bit_tvd: float) -> dict | None:
        """Return a pick for formation `code` if enough offsets agree, else None."""
        if len(self.tvd) < 40 or bit_tvd - self.last_eval_tvd < 4.0:
            return None
        if bit_tvd < prior_tvd - 2 * prior_sd + BELOW_M:
            return None                                        # top cannot have been drilled far enough yet
        self.last_eval_tvd = bit_tvd
        tvd = np.asarray(self.tvd)
        gr = np.asarray(self.gr)
        z0 = max(bit_tvd - LIVE_SPAN_M, float(tvd.min()))
        centres, vals = _bin(tvd, gr, z0, bit_tvd)
        if np.isnan(vals).any() or len(vals) < 30:
            return None
        live = _smooth(vals)
        # the smoothing window straddles the bit at the bottom; trust only bins fully drilled
        live_c, live = centres[: len(centres) - SMOOTH_BINS // 2], live[: len(live) - SMOOTH_BINS // 2]
        ests = []
        for ref in self.refs:
            tpl = self._template(ref, code)
            if tpl is None:
                continue
            t_vals, t_c, top = tpl
            if len(t_vals) < 10 or len(t_vals) >= len(live):
                continue
            cost, mapping = subsequence_dtw(t_vals, live)
            i_top = int(np.argmin(np.abs(t_c - top)))
            est = float(live_c[mapping[i_top]])
            if mapping[-1] >= len(live) - 2:
                continue                                       # template bottom not yet covered by live data
            jump = float(np.mean(t_vals[t_c > top][:int(CONTRAST_M / BIN_M)]) - np.mean(t_vals[t_c < top][-int(CONTRAST_M / BIN_M):]))
            ests.append({"well_id": ref["well_id"], "tvd": est, "cost": round(cost, 2), "jump": round(jump, 1)})
            if len(ests) >= N_REFS:
                break
        if len(ests) < MIN_AGREE:
            return None
        vals_e = np.array([e["tvd"] for e in ests])
        med = float(np.median(vals_e))
        agree = [e for e in ests if abs(e["tvd"] - med) <= AGREE_M]
        if len(agree) < MIN_AGREE or abs(med - prior_tvd) > 2 * prior_sd + 5:
            return None
        a = np.array([e["tvd"] for e in agree])
        w = np.array([1.0 / (e["cost"] + 1.0) for e in agree])
        pick = float(np.sum(w * a) / np.sum(w))
        # physical sanity check: across the pick, live GR must shift the same way (and by at least half as much)
        # as it does across the offsets' tops; a sandy streak inside a clay does not pass this
        ref_jump = float(np.mean([e["jump"] for e in agree]))
        above = vals[(centres >= pick - CONTRAST_M) & (centres < pick)]
        below = vals[(centres > pick) & (centres <= pick + CONTRAST_M)]
        if len(above) < 4 or len(below) < 4:
            return None
        live_jump = float(np.median(below) - np.median(above))
        if abs(ref_jump) < 5 or np.sign(live_jump) != np.sign(ref_jump) or abs(live_jump) < 0.5 * abs(ref_jump):
            return None
        sd = max(float(np.median(np.abs(a - np.median(a)))) * 1.4826, BIN_M)
        return {"formation": code, "tvd": round(pick, 1), "sd": round(sd, 1), "n_refs": len(agree),
                "refs": [e["well_id"] for e in agree], "per_ref": ests}


def next_formation(picked: dict[str, float], tops: dict[str, dict], tvd: float) -> str | None:
    """First formation below the deepest picked one that has a predicted top."""
    deepest = max((FORMATION_ORDER.index(c) for c in picked), default=0)
    for code in FORMATION_ORDER[deepest + 1:]:
        if code in tops and code != BOTTOM and not tops[code].get("absent"):
            return code
    return None


def pick_errors(picks: list[dict], truth: dict[str, float]) -> dict:
    errs = [abs(p["tvd"] - truth[p["formation"]]) for p in picks if p["formation"] in truth]
    return {"n": len(errs), "mae_m": round(float(np.mean(errs)), 1) if errs else None,
            "max_m": round(float(np.max(errs)), 1) if errs else None,
            "per_formation": {p["formation"]: round(p["tvd"] - truth[p["formation"]], 1) for p in picks
                              if p["formation"] in truth}}
