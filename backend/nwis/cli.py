"""Command line: python -m nwis.cli build-demo | serve | import-volve <dir> | eval"""
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
        print(json.dumps({"extraction": db.kv_get("extraction_eval"), "risk": db.kv_get("risk_metrics")}, indent=1))


if __name__ == "__main__":
    main()
