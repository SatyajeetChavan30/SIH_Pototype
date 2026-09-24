"""Honest extraction evaluation on held-out phrasing (style B) against generator truth."""
from __future__ import annotations

from collections import defaultdict

from .pipeline import extract_document
from .pdf import extract_pages

MATCH_TOL_M = 30.0


def evaluate_extraction(docs: list[tuple], clf, ctx_lookup) -> dict:
    """docs: list of (pdf_path, well_id, truth_events[list of dict]). Returns P/R/F1 overall and by hazard."""
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    fm_ok = fm_n = 0
    act_ok = act_n = 0
    depth_err: list[float] = []
    fp_examples: list[str] = []
    fn_examples: list[str] = []
    for path, well_id, truth in docs:
        ex = extract_document(extract_pages(path), clf, ctx_lookup, doc_id="eval", well_hint=well_id)
        # merge duplicates inside the document like the store would
        preds = []
        for e in ex.events:
            if any(p.hazard == e.hazard and p.md is not None and e.md is not None and abs(p.md - e.md) <= 40 for p in preds):
                continue
            preds.append(e)
        used = set()
        for t in truth:
            match = None
            for i, p in enumerate(preds):
                if i in used or p.hazard != t["hazard"] or p.md is None:
                    continue
                if abs(p.md - t["md"]) <= MATCH_TOL_M or (t["hazard"] == "CEMENT" and abs(p.md - t["md"]) <= 250):
                    match = i
                    break
            if match is None:
                fn[t["hazard"]] += 1
                if len(fn_examples) < 8:
                    fn_examples.append(f"{well_id} {t['hazard']} @ {t['md']:.0f} m")
                continue
            used.add(match)
            p = preds[match]
            tp[t["hazard"]] += 1
            depth_err.append(abs(p.md - t["md"]))
            fm_n += 1
            fm_ok += int(p.formation == t["formation"])
            codes_t = [a["code"] for a in t["attempts"]]
            codes_p = [a["code"] for a in p.actions]
            if codes_t:
                act_n += 1
                inter = len(set(codes_t) & set(codes_p))
                act_ok += inter / len(set(codes_t) | set(codes_p))
        for i, p in enumerate(preds):
            if i not in used:
                fp[p.hazard] += 1
                if len(fp_examples) < 8:
                    fp_examples.append(f"{well_id} {p.hazard} @ {p.md} : {p.citations[0]['text'][:90] if p.citations else ''}")
    hazards = sorted(set(tp) | set(fp) | set(fn))

    def prf(a, b, c):
        p = a / (a + b) if a + b else 0.0
        r = a / (a + c) if a + c else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return round(p, 3), round(r, 3), round(f, 3)

    by = {h: dict(zip(("precision", "recall", "f1"), prf(tp[h], fp[h], fn[h])), support=tp[h] + fn[h]) for h in hazards}
    P, R, F = prf(sum(tp.values()), sum(fp.values()), sum(fn.values()))
    return {"precision": P, "recall": R, "f1": F, "by_hazard": by, "n_docs": len(docs),
            "n_truth": sum(tp.values()) + sum(fn.values()),
            "formation_accuracy": round(fm_ok / fm_n, 3) if fm_n else None,
            "depth_mae_m": round(sum(depth_err) / len(depth_err), 1) if depth_err else None,
            "action_jaccard": round(act_ok / act_n, 3) if act_n else None,
            "fp_examples": fp_examples, "fn_examples": fn_examples,
            "note": "Held-out phrasing style (ALL-CAPS rig shorthand, different unit system) never seen by the classifier."}
