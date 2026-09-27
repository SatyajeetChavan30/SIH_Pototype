"""Real-data check on the public Equinor Volve daily drilling reports (WITSML 1.4 drillReport XML).

What is measured, honestly:
- Labels are the **operator's own activity codes** (<proprietaryCode>, e.g. "interruption -- lost circulation"),
  not a hand-labelled gold standard. Results are "agreement with operator coding".
- StrataSense reads **free text only**: pages are rendered without the codes or NPT tags (no label leakage), and the
  sentence classifier was trained on synthetic Assam reports, so the zero-shot number is a transfer test to a
  different operator, basin, language style and unit system.
- **Local adaptation**: k labelled report-days from *other* wells are added to the classifier and the test wells
  are re-scored (wells split into folds, never mixed). DrillScribe found DDR models barely transfer between
  operators until a few dozen local report-days are labelled; this reproduces that check on StrataSense.
Volve data: Equinor Open Data Licence (attribution; no resale). Download it from Equinor after accepting the licence.
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .. import config
from ..ingest.evaluate import evaluate_pages
from ..ingest.nlp import SentenceClassifier
from ..ingest.witsml import parse_drill_reports, reports_to_pages

# operator activity code -> StrataSense hazard (checked in this order; conservative on purpose)
CODE_RULES = [
    (("lost circ", "loss of circ", "losses"), "LOSS"),
    (("stuck",), "STUCK"),
    (("fish",), "FISH"),
    (("kick", "well control", "shut in", "shut-in", "influx"), "KICK"),
    (("tight hole", "hole problem", "pack off", "pack-off"), "TIGHT"),
]
MERGE_M = 40.0
K_STEPS = (0, 20, 60, 140)
N_FOLDS = 3


def code_hazard(code: str) -> str | None:
    c = (code or "").lower()
    for keys, hz in CODE_RULES:
        if any(k in c for k in keys):
            return hz
    return None


def load_reports(folder: Path | str, limit: int | None = None) -> dict[str, list[dict]]:
    """Well name -> drill reports (sorted by date) from every *.xml under folder; unreadable files are skipped."""
    by_well: dict[str, list[dict]] = defaultdict(list)
    files = sorted(Path(folder).rglob("*.xml"))[: limit or None]
    for f in files:
        try:
            for r in parse_drill_reports(f.read_bytes()):
                by_well[r["well"]].append(r)
        except Exception:  # noqa: BLE001 - public archives contain the odd malformed file
            continue
    for w in by_well:
        by_well[w].sort(key=lambda r: r["date"])
    return dict(by_well)


def truth_events(reports: list[dict]) -> list[dict]:
    """Operator-coded hazard events: consecutive activities with the same hazard within MERGE_M merged into one."""
    out: list[dict] = []
    for r in reports:
        for a in r["activities"]:
            hz = code_hazard(a["code"])
            if hz is None or a["md"] is None:
                continue
            last = out[-1] if out else None
            if last and last["hazard"] == hz and abs(last["md"] - a["md"]) <= MERGE_M:
                continue
            out.append({"hazard": hz, "md": a["md"], "code": a["code"]})
    return out


def labelled_sentences(reports: list[dict]) -> list[dict]:
    """Weak sentence labels from the codes: first activity of a coded interruption = hazard, follow-ups = ACTION."""
    out, prev = [], None
    for r in reports:
        for a in r["activities"]:
            text = (a["comments"] or "").strip()
            if not text:
                continue
            hz = code_hazard(a["code"])
            out.append({"text": text, "label": ("ACTION" if hz == prev else hz) if hz else "NONE"})
            prev = hz
    return out


def training_sentences() -> list[dict]:
    """The synthetic training set the shipped classifier learnt from (cached; regenerated from the seed if absent)."""
    path = config.MODELS_DIR / "train_sentences.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    import tempfile

    from ..data.docs_gen import write_ddr
    from ..data.synth import generate_world
    labelled: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for w in generate_world(config.SEED).wells:
            labelled += write_ddr(w, Path(tmp) / f"{w.id}.pdf", "A")
    path.write_text(json.dumps(labelled), encoding="utf-8")
    return labelled


def _score(by_well: dict[str, list[dict]], wells: list[str], clf) -> dict:
    docs = [(reports_to_pages(by_well[w], include_codes=False), w, truth_events(by_well[w])) for w in wells]
    return evaluate_pages(docs, clf, lambda _wid: None,
                          note="Equinor Volve DDRs, free text only, scored against the operator's activity codes.")


def evaluate_volve(folder: Path | str, limit: int | None = None, k_steps: tuple[int, ...] = K_STEPS,
                   folds: int = N_FOLDS, seed: int = 7, log=print) -> dict:
    by_well = load_reports(folder, limit)
    wells = sorted(w for w, rs in by_well.items() if rs)
    if len(wells) < 2:
        raise ValueError(f"need drill reports from at least 2 wells under {folder}, found {len(wells)}")
    n_reports = sum(len(by_well[w]) for w in wells)
    codes = Counter(a["code"] for w in wells for r in by_well[w] for a in r["activities"] if a["code"])
    mapped = {c: code_hazard(c) for c in codes if code_hazard(c)}
    unmapped_int = [c for c, _ in codes.most_common() if "interruption" in c.lower() and c not in mapped][:10]
    base_clf = SentenceClassifier.load(config.MODELS_DIR / "sentence_clf.joblib")
    log(f"{len(wells)} wells, {n_reports} report-days, {sum(len(truth_events(by_well[w])) for w in wells)} coded hazard events")

    zero_shot = _score(by_well, wells, base_clf)
    log(f"zero-shot (synthetic-trained, all wells): F1 {zero_shot['f1']} P {zero_shot['precision']} R {zero_shot['recall']}")

    rng = random.Random(seed)
    order = wells[:]
    rng.shuffle(order)
    fold_of = {w: i % folds for i, w in enumerate(order)}
    synth = training_sentences()
    curve = []
    for k in k_steps:
        tp_f1, per_fold = [], []
        for f in range(min(folds, len(wells))):
            test = [w for w in wells if fold_of[w] == f]
            train_reports = [(w, r) for w in wells if fold_of[w] != f for r in by_well[w]]
            if not test or (k and not train_reports):
                continue
            if k == 0:
                clf = base_clf
            else:
                picked = rng.sample(train_reports, min(k, len(train_reports)))
                local = labelled_sentences([r for _, r in picked])
                clf = SentenceClassifier().fit([s["text"] for s in synth] + [s["text"] for s in local],
                                               [s["label"] for s in synth] + [s["label"] for s in local])
            res = _score(by_well, test, clf)
            per_fold.append({"fold": f, "test_wells": test, "f1": res["f1"], "precision": res["precision"],
                             "recall": res["recall"], "n_truth": res["n_truth"]})
            tp_f1.append(res["f1"])
        mean = round(sum(tp_f1) / len(tp_f1), 3) if tp_f1 else None
        curve.append({"k_report_days": k, "f1_mean": mean, "folds": per_fold})
        log(f"k={k:>3} labelled local report-days: held-out-well F1 {mean}")

    return {"source": "Equinor Volve daily drilling reports (WITSML 1.4 drillReport)", "folder": str(folder),
            "n_wells": len(wells), "n_reports": n_reports, "zero_shot": zero_shot, "adaptation": curve,
            "code_mapping": mapped, "unmapped_interruption_codes": unmapped_int,
            "caveats": ["Labels are the operator's activity codes, not hand-checked truth.",
                        "Volve formations are not in the Assam ontology, so formation accuracy is not scored.",
                        "Depth comes from the activity depth field; hazard and actions must come from the text."]}


def adapt_classifier(folder: Path | str, exclude: set[str] | None = None, log=print):
    """The sentence classifier refitted on real report text: the synthetic training set plus every Volve DDR sentence
    labelled by the operator's own activity code (labelled_sentences), except the reports of wellbores in `exclude`
    (the replayed well, so its incidents are not in the training data). Returns (classifier, summary)."""
    from ..public.volve import wellbore_name
    exclude = {e for e in (exclude or set()) if e}
    reports, held = [], 0
    for w, rs in load_reports(folder).items():
        for r in rs:
            if (wellbore_name(r.get("wellbore") or "") or wellbore_name(w)) in exclude:
                held += 1
            else:
                reports.append(r)
    local = labelled_sentences(reports)
    synth = training_sentences()
    clf = SentenceClassifier().fit([s["text"] for s in synth] + [s["text"] for s in local],
                                   [s["label"] for s in synth] + [s["label"] for s in local])
    info = {"synthetic_sentences": len(synth), "volve_sentences": len(local), "volve_reports": len(reports),
            "held_out_reports": held, "held_out_wells": sorted(exclude),
            "labels": dict(Counter(s["label"] for s in local))}
    log(f"  report reader adapted on {len(local)} real Volve sentences ({len(reports)} report-days; "
        f"{held} held out) + {len(synth)} synthetic")
    return clf, info


def run_and_store(db, folder: Path | str, limit: int | None = None, log=print) -> dict:
    res = evaluate_volve(folder, limit, log=log)
    db.kv_set("public_eval", res)
    return res
