"""Honest extraction evaluation on held-out phrasing (style B) against generator truth."""
from __future__ import annotations

import difflib
from collections import defaultdict
from pathlib import Path

from .pipeline import extract_document
from .pdf import extract_pages

MATCH_TOL_M = 30.0


def evaluate_extraction(docs: list[tuple], clf, ctx_lookup) -> dict:
    """docs: list of (pdf_path, well_id, truth_events[list of dict]). Returns P/R/F1 overall and by hazard."""
    return evaluate_pages([(extract_pages(path), well_id, truth) for path, well_id, truth in docs], clf, ctx_lookup)


def evaluate_pages(docs: list[tuple], clf, ctx_lookup, note: str | None = None, match_tol_m: float = MATCH_TOL_M) -> dict:
    """docs: list of (pages, well_id, truth_events). Same matching rules for PDFs, XML-rendered pages or any text."""
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    fm_ok = fm_n = 0
    act_ok = act_n = 0
    depth_err: list[float] = []
    fp_examples: list[str] = []
    fn_examples: list[str] = []
    for pages, well_id, truth in docs:
        ex = extract_document(pages, clf, ctx_lookup, doc_id="eval", well_hint=well_id)
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
                if abs(p.md - t["md"]) <= match_tol_m or (t["hazard"] == "CEMENT" and abs(p.md - t["md"]) <= 250):
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
            if "formation" in t:          # public data has no Assam formations to score
                fm_n += 1
                fm_ok += int(p.formation == t["formation"])
            codes_t = [a["code"] for a in t.get("attempts", [])]
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
            "note": note or "Held-out phrasing style (ALL-CAPS rig shorthand, different unit system) never seen by the classifier."}


def evaluate_scans(docs: list[tuple], clf, ctx_lookup, qualities=("standard", "poor")) -> dict:
    """OCR end to end: the same reports as text PDFs and as scans, scored against generator truth.

    docs: list of (text_pdf_path, well_id, truth_events, {quality: scanned_pdf_path}). The text-PDF score is the
    ceiling (NLP alone); the gap to it is what OCR loses. `char_diff` is 1 - similarity of the OCR text to the
    text layer (a character-error proxy).
    """
    text_pages = [(extract_pages(t), wid, truth) for t, wid, truth, _ in docs]
    out = {"text_pdf": _brief(evaluate_pages(text_pages, clf, ctx_lookup)), "n_docs": len(docs),
           "n_truth": sum(len(truth) for _, _, truth, _ in docs)}
    for q in qualities:
        scan_pages, diffs = [], []
        for (tp, wid, truth), (_, _, _, scans) in zip(text_pages, docs):
            sp = extract_pages(Path(scans[q]))
            a = "\n".join(p.text for p in tp)
            b = "\n".join(p.text for p in sp)
            diffs.append(1 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio())
            scan_pages.append((sp, wid, truth))
        r = evaluate_pages(scan_pages, clf, ctx_lookup)
        out[q] = {**_brief(r), "char_diff": round(sum(diffs) / len(diffs), 3), "fn_examples": r["fn_examples"],
                  "ocr_pages": sum(1 for sp, _, _ in scan_pages for p in sp if p.ocr)}
    return out


def _brief(r: dict) -> dict:
    return {k: r[k] for k in ("precision", "recall", "f1", "depth_mae_m", "formation_accuracy")}
