"""Command line: python -m nwis.cli build-demo | serve | import-volve <dir> | metrics | evaluate-live | simulate-rig |
validate-volve <dir>"""
from __future__ import annotations

import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser(prog="nwis", description="eRTMAC-NWIS prototype")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-demo", help="generate synthetic data, ingest documents, train models")
    s = sub.add_parser("serve", help="run the API + web app")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    v = sub.add_parser("import-volve", help="ingest WITSML drillReport XML files (e.g. Equinor Volve) from a folder")
    v.add_argument("folder")
    sub.add_parser("metrics", help="print stored evaluation metrics")
    sub.add_parser("evaluate-live", help="replay the active well: alarm-budget sweep and DTW top-pick accuracy")
    vv = sub.add_parser("validate-volve", help="score NWIS on the public Equinor Volve DDR XML (download it first)")
    vv.add_argument("folder", help="folder containing Volve drillReport *.xml (searched recursively)")
    vv.add_argument("--limit", type=int, default=None, help="only read this many XML files")
    r = sub.add_parser("simulate-rig", help="send the stored active-well stream as real WITS-0 frames over TCP")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--connect", help="host:port of NWIS's WITS-0 listener (NWIS_STREAM=wits0-listen:PORT)")
    g.add_argument("--listen", type=int, help="act as a rig WITS box on this port (NWIS_STREAM=wits0-connect:host:PORT)")
    r.add_argument("--speed", type=float, default=60.0, help="replay speed factor (0 = as fast as possible)")
    r.add_argument("--from-md", type=float, default=None, help="start at this bit depth (e.g. 2120 for scenario S1)")
    r.add_argument("--limit", type=int, default=None, help="stop after this many frames")
    a = ap.parse_args()
    if a.cmd == "build-demo":
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


if __name__ == "__main__":
    main()
