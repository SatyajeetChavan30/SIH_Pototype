"""ML risk model (HistGradientBoosting per hazard) stacked on offset evidence.

Training data are depth bins of every offset well, featurised *as a planner would
have seen them*: tops predicted from wells spudded before it, evidence only from
earlier offsets, planned MW/ECD/inclination, mud system, geology context.
Evaluation is leave-wells-out (GroupKFold) against three baselines:
  base-rate (formation only), nearest-offset, and the Beta-Binomial evidence model.
"""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits

from .. import config
from ..correlation import target_from_well
from ..db import DB
from ..domain.ontology import FORMATION_ORDER, RIBBON_HAZARDS
from ..kb import KnowledgeBase
from .evidence import formation_summary, risk_profile

MUDS = ["WBM (lignosulphonate)", "KCl-PHPA", "KCl-PHPA-Glycol", "SOBM"]
FEATURE_GROUPS = {
    "formation": [f"fm_{c}" for c in FORMATION_ORDER],
    "position in formation / depth": ["rel_mid", "tvd_mid"],
    "trajectory (inclination)": ["inc"],
    "mud weight / ECD": ["mw", "ecd", "ecd_minus_mw"],
    "mud system": [f"mud_{i}" for i in range(len(MUDS))],
    "spud year (depletion)": ["spud_year"],
    "distance to thrust front": ["thrust_km"],
    "offset evidence": ["ev_p", "ev_prior", "ev_neff", "ev_nearest", "fm_p"],
}
FEATURES = [f for g in FEATURE_GROUPS.values() for f in g]
LABEL_TOL_M = 30.0  # "hazard within +/-30 m of this depth"


def featurize(bins: list[dict], target, offsets, kb: KnowledgeBase, hz: str, fm_cache: dict | None = None) -> np.ndarray:
    fm_cache = {} if fm_cache is None else fm_cache
    thrust = kb.thrust_km(target.lat, target.lon)
    mud = [1.0 if target.mud_system == m else 0.0 for m in MUDS]
    X = np.zeros((len(bins), len(FEATURES)), dtype=float)
    for i, b in enumerate(bins):
        h = b["hazards"][hz]
        key = (b["formation"], hz)
        if key not in fm_cache:
            fm_cache[key] = formation_summary(offsets, hz, b["formation"])["p"]
        row = {f"fm_{c}": 1.0 if b["formation"] == c else 0.0 for c in FORMATION_ORDER}
        row.update(rel_mid=(b["rel0"] + b["rel1"]) / 2, tvd_mid=(b["tvd0"] + b["tvd1"]) / 2, inc=b["inc"],
                   mw=b["mw"], ecd=b["ecd"], ecd_minus_mw=b["ecd"] - b["mw"], spud_year=target.spud_year,
                   thrust_km=thrust, ev_p=h["p"], ev_prior=h["prior"], ev_neff=h["n_eff"],
                   ev_nearest=h["nearest"] if not np.isnan(h["nearest"]) else -1.0, fm_p=fm_cache[key])
        for k, v in enumerate(mud):
            row[f"mud_{k}"] = v
        X[i] = [row[f] for f in FEATURES]
    return X


class RiskModel:
    def __init__(self, models: dict, medians: dict, metrics: dict | None = None, blend_w: dict | None = None):
        self.models = models
        self.medians = medians
        self.metrics = metrics or {}
        self.blend_w = blend_w or {}

    def annotate(self, bins, target, offsets, kb) -> None:
        # Hundreds of tiny predict calls: OpenMP fan-out costs more than it saves, and from a server worker
        # thread it made /api/risk/profile ~13x slower (8 s vs 0.6 s). One thread per call is fastest here.
        with threadpool_limits(1, user_api="openmp"):
            self._annotate(bins, target, offsets, kb)

    def _annotate(self, bins, target, offsets, kb) -> None:
        cache: dict = {}
        for hz, m in self.models.items():
            X = featurize(bins, target, offsets, kb, hz, cache)
            p = m.predict_proba(X)[:, 1]
            for b, pi, x in zip(bins, p, X):
                h = b["hazards"][hz]
                h["ml"] = round(float(pi), 4)
                h["w_ml"] = self.blend_w.get(hz, 0.5)
                if pi >= 0.12 or h["p"] >= 0.12:
                    h["drivers"] = self.explain(hz, x, float(pi))

    def explain(self, hz: str, x: np.ndarray, p: float, top: int = 3) -> list[dict]:
        m = self.models[hz]
        med = self.medians[hz]
        out = []
        for g, cols in FEATURE_GROUPS.items():
            idx = [FEATURES.index(c) for c in cols]
            xx = x.copy()
            xx[idx] = med[idx]
            p0 = float(m.predict_proba(xx.reshape(1, -1))[0, 1])
            out.append({"factor": g, "delta": round(p - p0, 3)})
        out.sort(key=lambda d: -abs(d["delta"]))
        return [d for d in out[:top] if abs(d["delta"]) >= 0.01]

    def save(self, path):
        import joblib
        joblib.dump({"models": self.models, "medians": self.medians, "metrics": self.metrics,
                     "blend_w": self.blend_w}, path)

    @classmethod
    def load(cls, path) -> "RiskModel | None":
        import joblib
        try:
            d = joblib.load(path)
            return cls(d["models"], d["medians"], d.get("metrics"), d.get("blend_w"))
        except Exception:
            return None


def build_dataset(kb: KnowledgeBase, radius_km: float = 8.0):
    data = {hz: {"X": [], "y": [], "g": [], "base": [], "near": [], "ev": []} for hz in RIBBON_HAZARDS}
    for w in sorted(kb.offsets(), key=lambda w: w.id):
        if w.traj is None or not w.sections:
            continue
        target = target_from_well(kb, w.id)
        prof = risk_profile(kb, target, radius_km, md_from=100.0, before_year=w.spud_year)
        from .evidence import build_offsets
        offsets = build_offsets(kb, target, radius_km, w.spud_year)
        cache: dict = {}
        for hz in RIBBON_HAZARDS:
            X = featurize(prof["bins"], target, offsets, kb, hz, cache)
            ev_md = [e["md"] for e in w.events if e["hazard"] == hz and e["md"] is not None]
            for b, x in zip(prof["bins"], X):
                y = any(b["md0"] - LABEL_TOL_M <= m <= b["md1"] + LABEL_TOL_M for m in ev_md)
                h = b["hazards"][hz]
                d = data[hz]
                d["X"].append(x)
                d["y"].append(int(y))
                d["g"].append(w.id)
                d["base"].append(h["prior"])
                d["near"].append(h["nearest"] if not np.isnan(h["nearest"]) else h["prior"])
                d["ev"].append(h["p"])
    for hz in data:
        for k in data[hz]:
            data[hz][k] = np.array(data[hz][k])
    return data


def _metrics(y, p) -> dict:
    if y.sum() == 0 or y.sum() == len(y):
        return {"auc": None, "ap": None, "brier": None}
    return {"auc": round(float(roc_auc_score(y, p)), 3), "ap": round(float(average_precision_score(y, p)), 3),
            "brier": round(float(brier_score_loss(y, np.clip(p, 0, 1))), 4)}


def make_clf():
    return HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=25,
                                          l2_regularization=1.0, random_state=0)


def train_and_evaluate(db: DB | None = None, n_splits: int = 10) -> dict:
    kb = KnowledgeBase(db or DB())
    data = build_dataset(kb)
    pooled = {k: ([], []) for k in ("base_rate", "nearest_offset", "offset_evidence", "ml", "blend")}
    per_hazard = {}
    models, medians, blend_w = {}, {}, {}
    for hz, d in data.items():
        X, y, g = d["X"], d["y"], d["g"]
        oof = np.zeros(len(y))
        gkf = GroupKFold(n_splits=min(n_splits, len(set(g))))
        for tr, te in gkf.split(X, y, g):
            if y[tr].sum() < 3:
                oof[te] = d["ev"][te]
                continue
            m = make_clf().fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]
        # stacking weight between evidence and ML chosen on out-of-fold average precision
        best = max((0.0, 0.25, 0.5, 0.75, 1.0), key=lambda w: average_precision_score(y, w * oof + (1 - w) * d["ev"])
                   if 0 < y.sum() < len(y) else 0)
        blend_w[hz] = best
        preds = {"base_rate": d["base"], "nearest_offset": d["near"], "offset_evidence": d["ev"], "ml": oof,
                 "blend": best * oof + (1 - best) * d["ev"]}
        per_hazard[hz] = {"positives": int(y.sum()), "rows": int(len(y)), "blend_w_ml": best,
                          **{k: _metrics(y, v) for k, v in preds.items()}}
        for k, v in preds.items():
            pooled[k][0].append(y)
            pooled[k][1].append(v)
        if y.sum() >= 3:
            models[hz] = make_clf().fit(X, y)
            medians[hz] = np.median(X, axis=0)
    pooled_m = {k: _metrics(np.concatenate(v[0]), np.concatenate(v[1])) for k, v in pooled.items()}
    # calibration of the blended score (reliability curve, pooled)
    yb, pb = np.concatenate(pooled["blend"][0]), np.concatenate(pooled["blend"][1])
    edges = np.array([0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0])
    calib = []
    for a, b in zip(edges[:-1], edges[1:]):
        mask = (pb >= a) & (pb < b)
        if mask.sum() >= 10:
            calib.append({"bin": f"{a:.2f}-{b:.2f}", "predicted": round(float(pb[mask].mean()), 3),
                          "observed": round(float(yb[mask].mean()), 3), "n": int(mask.sum())})
    metrics = {"pooled": pooled_m, "per_hazard": per_hazard, "calibration": calib,
               "n_wells": len(set(np.concatenate([d["g"] for d in data.values()]))),
               "protocol": "GroupKFold by well (leave-wells-out); features use only offsets spudded before each well."}
    model = RiskModel(models, medians, metrics, blend_w)
    model.save(config.MODELS_DIR / "risk_model.joblib")
    (db or DB()).kv_set("risk_metrics", metrics)
    return metrics
