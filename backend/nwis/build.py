"""Build the demo knowledge base end to end (deterministic).

1. generate the synthetic Upper-Assam world (master data + ground truth)
2. load master data (wells, tops, surveys, casing/mud) into SQLite
3. render DDR/WCR PDFs (some scanned) + train the sentence classifier
4. ingest every PDF through the NLP/OCR pipeline -> events, lessons, citations
5. evaluate extraction on held-out phrasing
6. write drilling-parameter logs + the active-well real-time stream
7. train/evaluate the risk model
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict

import numpy as np

from . import config
from .db import DB
from .data import docs_gen, logs_gen
from .data.synth import STRUCTURES, World, generate_world
from .ingest.evaluate import evaluate_extraction
from .ingest.nlp import SentenceClassifier
from .ingest.pipeline import Ingestor, load_well_ctx


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def load_master_data(db: DB, world: World) -> None:
    for s in STRUCTURES:
        db.insert("structures", {"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon, "prod_start": s.prod_start})
    for w in world.wells + [world.active]:
        db.insert("wells", {"id": w.id, "name": w.name, "structure_id": w.structure_id, "lat": w.lat, "lon": w.lon,
                            "spud_date": w.spud_date, "spud_year": w.spud_year, "td_md": w.td_md, "td_tvd": w.td_tvd,
                            "status": w.status, "traj_type": w.traj_type, "target": w.target,
                            "mud_system": w.mud_system, "is_active": int(w.is_active)})
        traj = w.trajectory()
        db.executemany("INSERT INTO surveys (well_id, md, inc, azi, tvd, north, east) VALUES (?,?,?,?,?,?,?)",
                       [(w.id, float(traj.md[i]), float(traj.inc[i]), float(traj.azi[i]), round(float(traj.tvd[i]), 2),
                         round(float(traj.north[i]), 2), round(float(traj.east[i]), 2)) for i in range(len(traj.md))])
        for i, s in enumerate(w.sections):
            db.insert("sections", {"well_id": w.id, "idx": i, "hole": s["hole"], "casing": s["casing"],
                                   "top_md": s["top_md"], "shoe_md": s["shoe_md"], "mw_ppg": s["mw_ppg"],
                                   "ecd_ppg": s["ecd_ppg"]})
        for code, md in w.tops_md.items():
            db.insert("tops", {"well_id": w.id, "formation": code, "md": md, "tvd": round(w.tops_tvd[code], 1),
                               "source": "mudlog"})
        for e in w.events:
            db.insert("truth_events", {"id": e.id, "well_id": w.id, "hazard": e.hazard, "subtype": e.subtype,
                                       "md": e.md, "tvd": e.tvd, "formation": e.formation, "rel": e.rel,
                                       "severity": e.severity, "cause": e.cause, "npt_hours": e.npt_hours,
                                       "mw_ppg": e.mw_ppg, "ecd_ppg": e.ecd_ppg, "attempts": e.attempts,
                                       "extra": e.extra})
    db.commit()


def make_samples(world: World) -> None:
    """Files for the live ingestion demo: today's DDR of the active well, a scanned legacy WCR, WITSML XML."""
    from pathlib import Path
    sd = config.DATA_DIR / "samples"
    sd.mkdir(exist_ok=True)
    w = docs_gen.PdfWriter()
    days = [
        ("2026-09-07", 1540, 1655, [
            "06:00-08:30  DRLG    Drilled 12-1/4\" hole from 1,540 m to 1,610 m in Girujan Clay. MW: 10.10 ppg.",
            "08:30-11:00  NPT-TH  Observed overpull of 35 klbs at 1,612 m in Girujan Clay while POOH for bit change. "
            "Backreamed through tight spot. Problem resolved. NPT: 2.5 hrs.",
            "11:00-18:00  DRLG    Drilled ahead to 1,655 m. No losses observed. Flow check negative.",
            "18:00-24:00  OTHR    Precautionary LCM pill kept ready before entering Tipam."]),
        ("2026-09-08", 1655, 1750, [
            "00:00-09:00  DRLG    Drilled 12-1/4\" hole from 1,655 m to 1,700 m.",
            "09:00-10:30  NPT-TH  Bit balling suspected at 1,701 m: ROP dropped and SPP increased. Pumped anti-balling sweep. "
            "Problem resolved. NPT: 1.5 hrs.",
            "10:30-24:00  DRLG    Drilled ahead to 1,750 m. No gas shows. Hole in good condition."]),
    ]
    for i, (date, d0, d1, lines) in enumerate(days):
        w.new_page()
        w.line(f"DAILY DRILLING REPORT    Report No: {12 + i}    Date: {date}", bold=True)
        w.line(f"Well: {world.active.id}    Structure: Namdang High    Rig: SYN-RIG-4")
        w.line(f"Depth @ 00:00: {d0:,} m    Depth @ 24:00: {d1:,} m")
        w.line("Hole size: 12-1/4\"    Mud: KCl-PHPA-Glycol    MW: 10.10 ppg    ECD: 10.55 ppg")
        w.line()
        w.line("TIME LOG", bold=True)
        for l in lines:
            w.line(l)
    w.save(sd / "NDH-21_DDR_latest.pdf")
    # a legacy WCR that exists only as a scan
    legacy = next(x for x in world.wells if x.structure_id == "SSN" and x.events)
    tmp = sd / "tmp_wcr.pdf"
    docs_gen.write_wcr(legacy, tmp)
    docs_gen.rasterize_pdf(tmp, sd / "legacy_scanned_WCR.pdf", seed=11)
    tmp.unlink()
    shutil.copy(Path(__file__).parent / "data" / "samples" / "drillreport_sample.xml", sd / "drillreport_sample.xml")


def build(fresh: bool = True, eval_wells: int = 14) -> dict:
    t0 = time.time()
    if fresh and config.DATA_DIR.exists():
        for sub in ("documents", "logs", "models", "eval", "samples", "uploads"):
            shutil.rmtree(config.DATA_DIR / sub, ignore_errors=True)
        for f in ("nwis.db", "nwis.db-wal", "nwis.db-shm"):
            (config.DATA_DIR / f).unlink(missing_ok=True)
    config.ensure_dirs()
    db = DB()
    db.init()

    log("generating synthetic Upper-Assam world")
    world = generate_world(config.SEED)
    load_master_data(db, world)
    log(f"  {len(world.wells)} offset wells, {sum(len(w.events) for w in world.wells)} true events, active={world.active.id}")

    log("rendering DDR / WCR PDFs")
    labelled = []
    docs = []
    for w in world.wells:
        ddr = config.DOCS_DIR / f"{w.id}_DDR.pdf"
        labelled += docs_gen.write_ddr(w, ddr, "A")
        docs.append((ddr, "DDR", f"{w.id} Daily Drilling Reports"))
        wcr = config.DOCS_DIR / f"{w.id}_WCR.pdf"
        if w.scanned_wcr:
            tmp = config.DOCS_DIR / f"{w.id}_WCR_text.pdf"
            docs_gen.write_wcr(w, tmp)
            docs_gen.rasterize_pdf(tmp, wcr, seed=len(w.id))
            tmp.unlink()
            docs.append((wcr, "WCR", f"{w.id} Well Completion Report (scanned legacy copy)"))
        else:
            docs_gen.write_wcr(w, wcr)
            docs.append((wcr, "WCR", f"{w.id} Well Completion Report"))

    log(f"training sentence classifier on {len(labelled)} labelled sentences")
    clf = SentenceClassifier().fit([l["text"] for l in labelled], [l["label"] for l in labelled])
    clf.save(config.MODELS_DIR / "sentence_clf.joblib")
    # kept so the classifier can be re-fitted with local labels (real-data adaptation, validate/volve.py)
    (config.MODELS_DIR / "train_sentences.json").write_text(json.dumps(labelled), encoding="utf-8")

    log("ingesting documents through NLP/OCR pipeline")
    ing = Ingestor(db, clf)
    ocr_pages = 0
    for path, kind, title in docs:
        res = ing.ingest_pdf(path, kind_hint=kind, title=title)
        ocr_pages += sum(1 for p in res["pages"] if p["ocr"])
    n_ev = db.one("SELECT COUNT(*) n FROM events")["n"]
    log(f"  {len(docs)} docs, {ocr_pages} OCR pages -> {n_ev} events, "
        f"{db.one('SELECT COUNT(*) n FROM lessons')['n']} lessons, "
        f"{db.one('SELECT COUNT(*) n FROM review_queue')['n']} items for review")

    log("evaluating extraction on held-out phrasing (style B)")
    eval_dir = config.DATA_DIR / "eval"
    eval_dir.mkdir(exist_ok=True)
    rng = np.random.default_rng(5)
    picks = rng.choice(len(world.wells), size=min(eval_wells, len(world.wells)), replace=False)
    eval_docs = []
    for i in picks:
        w = world.wells[int(i)]
        p = eval_dir / f"{w.id}_DDR_styleB.pdf"
        docs_gen.write_ddr(w, p, "B")
        truth = [{"hazard": e.hazard, "md": e.md, "formation": e.formation, "attempts": e.attempts} for e in w.events]
        eval_docs.append((p, w.id, truth))
    ex_eval = evaluate_extraction(eval_docs, clf, lambda wid: load_well_ctx(db, wid))
    # same-style (in-distribution) reference numbers
    in_docs = [(config.DOCS_DIR / f"{world.wells[int(i)].id}_DDR.pdf", world.wells[int(i)].id,
                [{"hazard": e.hazard, "md": e.md, "formation": e.formation, "attempts": e.attempts}
                 for e in world.wells[int(i)].events]) for i in picks]
    ex_in = evaluate_extraction(in_docs, clf, lambda wid: load_well_ctx(db, wid))
    db.kv_set("extraction_eval", {"held_out": ex_eval, "in_distribution": ex_in})
    log(f"  held-out F1={ex_eval['f1']} (P={ex_eval['precision']} R={ex_eval['recall']}); in-dist F1={ex_in['f1']}")

    log("writing drilling-parameter logs + active-well stream")
    lrng = np.random.default_rng(config.SEED + 1)
    for w in world.wells:
        np.savez_compressed(config.LOGS_DIR / f"{w.id}.npz", **logs_gen.offset_logs(lrng, w))
    stream, episodes = logs_gen.active_stream(np.random.default_rng(config.SEED + 2), world.active)
    np.savez_compressed(config.LOGS_DIR / "active_stream.npz", **stream)
    db.kv_set("active_episodes", episodes)
    db.kv_set("active_truth_tops", {k: round(v, 1) for k, v in world.active.tops_tvd_full.items()})
    db.kv_set("active_well", world.active.id)

    log("preparing ingestion demo samples")
    make_samples(world)

    log("training risk model (leave-one-well-out evaluation)")
    from .risk.model import train_and_evaluate
    metrics = train_and_evaluate(db)
    log(f"  pooled AUC: {json.dumps({k: v.get('auc') for k, v in metrics['pooled'].items()})}")

    log("replaying the active well: alarm-budget sweep and DTW top-pick accuracy")
    from .kb import KnowledgeBase
    from .realtime.evaluate import evaluate_live
    from .risk.model import RiskModel
    live = evaluate_live(KnowledgeBase(db), RiskModel.load(config.MODELS_DIR / "risk_model.joblib"))
    log(f"  DTW top-pick MAE {live['top_picks']['dtw']['mae_m']} m")

    db.kv_set("build_info", {"seed": config.SEED, "seconds": round(time.time() - t0, 1), "n_wells": len(world.wells),
                             "n_docs": len(docs), "ocr_pages": ocr_pages})
    log(f"done in {time.time() - t0:.1f}s")
    return {"extraction": ex_eval, "risk": metrics}
