"""Command line.

Day-to-day use needs none of this: `start` (what start.bat / run.sh call) runs the server and opens the dashboard,
and everything else - building the knowledge base, switching dataset, imports, evaluations, retraining, the live
rig feed, the rig simulator, settings - is done in the browser. The other commands remain for scripting and CI
(the dashboard's maintenance jobs also run some of them as subprocesses):
build-demo | serve | import-volve <dir> | metrics | evaluate-live | evaluate-ocr | reread-scans | simulate-rig |
validate-volve <dir> | build-public | retrain-risk | retrain-classifier
"""
from __future__ import annotations

import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser(prog="nwis", description="eRTMAC-NWIS prototype")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start", help="run the server under a supervisor (restarts from the dashboard) and open it")
    st.add_argument("--host", default="0.0.0.0")
    st.add_argument("--port", type=int, default=8000)
    st.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    sub.add_parser("build-demo", help="generate synthetic data, ingest documents, train models")
    s = sub.add_parser("serve", help="run the API + web app")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    v = sub.add_parser("import-volve", help="ingest WITSML drillReport XML files (e.g. Equinor Volve) from a folder")
    v.add_argument("folder")
    sub.add_parser("metrics", help="print stored evaluation metrics")
    sub.add_parser("evaluate-live", help="replay the active well: alarm-budget sweep and DTW top-pick accuracy")
    sub.add_parser("reread-scans", help="OCR the scanned documents that were stored unread because no OCR engine "
                                        "was installed at ingest time")
    sub.add_parser("evaluate-ocr", help="re-score scanned-report OCR (e.g. after installing an OCR engine) "
                                        "without rebuilding the demo")
    bp = sub.add_parser("build-public", help="build a real-data knowledge base from public Sodir FactPages exports "
                                              "(run with NWIS_REGION=norway and a separate NWIS_DATA_DIR)")
    bp.add_argument("--source", default="sodir", choices=["sodir"])
    bp.add_argument("--quadrants", default="15,16", help="comma-separated Norwegian quadrants, e.g. 15,16,25 ('all' = no filter)")
    bp.add_argument("--from-folder", default=None, help="folder with the Sodir CSV exports (default: <data dir>/public/sodir)")
    bp.add_argument("--download", action="store_true", help="download the CSV exports from factpages.sodir.no first")
    vv = sub.add_parser("validate-volve", help="score NWIS on the public Equinor Volve DDR XML (download it first)")
    vv.add_argument("folder", help="folder containing Volve drillReport *.xml (searched recursively)")
    vv.add_argument("--limit", type=int, default=None, help="only read this many XML files")
    sub.add_parser("retrain-risk", help="re-train and re-evaluate the risk model on the current knowledge base")
    sub.add_parser("retrain-classifier", help="re-fit the sentence classifier with the review-queue verdicts "
                                              "(approved and rejected sentences)")
    r = sub.add_parser("simulate-rig", help="send the stored active-well stream as real WITS-0 frames over TCP")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--connect", help="host:port of NWIS's WITS-0 listener (NWIS_STREAM=wits0-listen:PORT)")
    g.add_argument("--listen", type=int, help="act as a rig WITS box on this port (NWIS_STREAM=wits0-connect:host:PORT)")
    r.add_argument("--speed", type=float, default=60.0, help="replay speed factor (0 = as fast as possible)")
    r.add_argument("--from-md", type=float, default=None, help="start at this bit depth (e.g. 2120 for scenario S1)")
    r.add_argument("--limit", type=int, default=None, help="stop after this many frames")
    a = ap.parse_args()
    if a.cmd == "start":
        supervise(a.host, a.port, a.open)
    elif a.cmd == "build-demo":
        from .build import build
        build()
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("nwis.api.main:app", host=a.host, port=a.port, log_level="info")
    elif a.cmd == "import-volve":
        from pathlib import Path
        from .db import DB
        from .config import MODELS_DIR
        from .ingest.nlp import SentenceClassifier
        from .ingest.pipeline import Ingestor
        ing = Ingestor(DB(), SentenceClassifier.load(MODELS_DIR / "sentence_clf.joblib"))
        files = sorted(Path(a.folder).rglob("*.xml"))
        n = 0
        for f in files:
            try:
                r = ing.ingest_witsml(f, source="volve")
                n += len(r["events"])
            except Exception as e:  # noqa: BLE001 - keep going over a large public dataset
                print(f"skip {f.name}: {e}")
        print(f"ingested {len(files)} files, {n} events")
    elif a.cmd == "metrics":
        from .db import DB
        db = DB()
        print(json.dumps({"extraction": db.kv_get("extraction_eval"), "risk": db.kv_get("risk_metrics"),
                          "live": db.kv_get("live_eval")}, indent=1))
    elif a.cmd == "build-public":
        from pathlib import Path
        from .public import sodir
        q = None if a.quadrants.strip().lower() == "all" else {x.strip() for x in a.quadrants.split(",") if x.strip()}
        res = sodir.build(Path(a.from_folder) if a.from_folder else None, q, a.download)
        print(json.dumps(res, indent=1, default=str))
        print(sodir.ATTRIBUTION)
    elif a.cmd == "validate-volve":
        from .db import DB
        from .validate.volve import run_and_store
        run_and_store(DB(), a.folder, a.limit)
        print("stored as kv 'public_eval' (shown in Analytics)")
    elif a.cmd == "simulate-rig":
        from .realtime import simulator
        start = simulator.start_index_for_md(a.from_md) if a.from_md is not None else 0
        n = simulator.run(a.connect or ("127.0.0.1:5501" if a.listen is None else None), a.listen, a.speed, start, a.limit)
        print(f"sent {n} WITS-0 frames")
    elif a.cmd == "reread-scans":
        from .config import MODELS_DIR
        from .db import DB
        from .ingest.nlp import SentenceClassifier
        from .ingest.ocr import get_ocr_engine
        from .ingest.pipeline import Ingestor
        if get_ocr_engine() is None:
            raise SystemExit("no OCR engine installed: pip install -e \"backend[ocr]\"")
        res = Ingestor(DB(), SentenceClassifier.load(MODELS_DIR / "sentence_clf.joblib")).reread_skipped_scans()
        for r in res:
            print(f"{r['title']}: " + (r["error"] if "error" in r else
                  f"{r['ocr_pages']} OCR page(s), {r['events']} events, {r['lessons']} lessons, {r['review']} for review"))
        print(f"{len(res)} document(s) re-read")
    elif a.cmd == "evaluate-ocr":
        from . import config
        from .build import evaluate_ocr
        from .data.synth import generate_world
        from .db import DB
        from .ingest.nlp import SentenceClassifier
        eval_dir = config.DATA_DIR / "eval"
        eval_dir.mkdir(exist_ok=True)
        res = evaluate_ocr(DB(), generate_world(config.SEED), SentenceClassifier.load(config.MODELS_DIR / "sentence_clf.joblib"),
                           eval_dir)
        if res is None:
            raise SystemExit("no OCR engine installed: pip install rapidocr onnxruntime (or the [ocr] extra on Python <= 3.12)")
    elif a.cmd == "evaluate-live":
        from .config import MODELS_DIR
        from .kb import KnowledgeBase
        from .realtime.evaluate import evaluate_live
        from .risk.model import RiskModel
        res = evaluate_live(KnowledgeBase(), RiskModel.load(MODELS_DIR / "risk_model.joblib"))
        for r in res["budget"]["rows"]:
            print(f"nuisance={r['nuisance']} budget={r['budget_per_hour']}: detected {r['detected']}/"
                  f"{len(r['episodes'])}, false alarms {r['false_alarms']} ({r['false_per_hour']}/h)")
        for mode, v in res["top_picks"].items():
            print(f"top picks [{mode}]: MAE {v['mae_m']} m over {v['n']} tops; episodes {v['episodes_detected']}/{v['n_episodes']}")

    elif a.cmd == "retrain-risk":
        from .db import DB
        from .risk.model import train_and_evaluate
        print("[retrain] training the risk model (leave-wells-out evaluation)", flush=True)
        metrics = train_and_evaluate(DB())
        print(f"[retrain] pooled AUC: {json.dumps({k: v.get('auc') for k, v in metrics['pooled'].items()})}")
    elif a.cmd == "retrain-classifier":
        from .db import DB
        from .ingest.nlp import retrain_from_reviews
        try:
            info = retrain_from_reviews(DB(), log=lambda m: print(f"[retrain] {m}", flush=True))
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"[retrain] reviewed labels used: {info['labels']}")


def supervise(host: str, port: int, open_browser: bool) -> None:
    """Keep the server running; the dashboard restarts it (dataset switch, rebuild) by exiting with RESTART_EXIT."""
    import os
    import subprocess
    import sys
    import threading
    import time
    import urllib.request
    import webbrowser
    from .ops import RESTART_EXIT
    url = f"http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}"

    def open_when_up():
        for _ in range(120):
            try:
                urllib.request.urlopen(f"{url}/api/health", timeout=2)
                print(f"[nwis] dashboard: {url}", flush=True)
                webbrowser.open(url)
                return
            except OSError:
                time.sleep(1)

    env = {**os.environ, "NWIS_SUPERVISED": "1"}
    first = True
    while True:
        proc = subprocess.Popen([sys.executable, "-m", "nwis.cli", "serve", "--host", host, "--port", str(port)], env=env)
        if first:
            print(f"[nwis] starting on {url}  (Ctrl+C to stop)", flush=True)
            if open_browser:
                threading.Thread(target=open_when_up, daemon=True).start()
            first = False
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return
        if rc != RESTART_EXIT:
            sys.exit(rc)
        print("[nwis] restarting (requested from the dashboard)", flush=True)


if __name__ == "__main__":
    main()
