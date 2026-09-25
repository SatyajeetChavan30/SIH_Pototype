"""Real public-data region (NWIS_REGION=norway): the region switch and the Sodir importer.

The region is chosen when modules are imported, so these tests run the Norway code in a subprocess with its own
data folder; the Assam demo in this process is untouched. The fixture rows are SAMPLES in the Sodir export column
layout (tests/fixtures/sodir), not real wells.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nwis import config

FIX = Path(__file__).parent / "fixtures" / "sodir"
needs_models = pytest.mark.skipif(not (config.ROOT / "data" / "models" / "sentence_clf.joblib").exists(),
                                  reason="the Assam demo classifier is reused by the public build")


def _run(code: str, data_dir: Path) -> dict:
    env = {**os.environ, "NWIS_REGION": "norway", "NWIS_DATA_DIR": str(data_dir), "NWIS_AUTH": "off"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600,
                         cwd=Path(__file__).parents[1])
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_norway_ontology_and_absent_units_pinch_out(tmp_path):
    res = _run("""
import json
from nwis.domain import ontology as o
from nwis.ingest.nlp import detect_formation
print(json.dumps({"order": o.FORMATION_ORDER, "td": o.DEFAULT_TD, "surface": o.SURFACE,
                  "lista": detect_formation("gas kick in the Lista Formation"),
                  "chalk": detect_formation("losses in the Shetland Group chalk")}))
""", tmp_path)
    assert res["order"][0] == "NORDLAND" and res["surface"] == "NORDLAND" and res["td"] == "VESTLAND"
    assert "TIPAM" not in res["order"]
    assert res["lista"] == "ROGALAND" and res["chalk"] == "SHETLAND"


@needs_models
def test_sodir_build_from_fixture(tmp_path):
    res = _run(f"""
import json
from pathlib import Path
from nwis.public import sodir
from nwis.db import DB
from nwis.kb import KnowledgeBase
from nwis.correlation import default_plan, predict_tops, target_from_well
r = sodir.build(Path(r"{FIX}"), {{"15"}}, False, log=lambda *_: None)
db = DB(); kb = KnowledgeBase(db)
w = kb.wells["15/9-X1"]
t = target_from_well(kb, "15/9-X2")
tops = predict_tops(kb, t, kb.nearby(t.lat, t.lon, 25.0, t.exclude))
plan = default_plan(kb, 58.45, 1.89)
print(json.dumps({{"build": r, "wells": sorted(kb.wells), "active": kb.active_id,
                  "tops": w.tops_md, "sections": [(s["casing"], s["shoe_md"], s["mw_ppg"]) for s in w.sections],
                  "mud": w.mud_system, "lot": db.query("SELECT emw_ppg, test_type FROM lot_tests WHERE well_id='15/9-X1'"),
                  "events": [(e["well_id"], e["hazard"], e["md"], e["formation"]) for e in kb.events],
                  "absent_brent": tops["BRENT"].get("absent"), "brent_eq_next": tops["BRENT"]["tvd"] == tops["VESTLAND"]["tvd"],
                  "plan_sections": len(plan.sections), "structures": sorted(kb.structures)}}, default=str))
""", tmp_path)
    assert res["wells"] == ["15/9-X1", "15/9-X2", "15/9-X3"]          # other quadrant and the deviated wellbore excluded
    assert res["active"] == "15/9-X3"                                   # most recent well with tops and sections
    assert res["tops"]["ROGALAND"] == 1900 and "BRENT" not in res["tops"]
    assert [round(s[1]) for s in res["sections"]] == [200, 1000, 2200, 3050, 3400]
    assert res["sections"][-1][0] == "open hole"
    assert abs(res["sections"][3][2] - round(1.35 * 8.345, 2)) < 0.01   # median mud weight in the interval, in ppg
    assert res["mud"] == "Oil Based" and {x["test_type"] for x in res["lot"]} == {"LOT", "FIT"}
    ev = {(w, h) for w, h, _, _ in res["events"]}
    assert ("15/9-X1", "LOSS") in ev and ("15/9-X1", "KICK") in ev and ("15/9-X3", "STUCK") in ev
    assert not any(w == "15/9-X2" for w, *_ in res["events"])           # "without significant problems"
    assert res["absent_brent"] and res["brent_eq_next"]                 # Brent absent here: pinched out, not invented
    assert res["plan_sections"] == 5 and "SAMPLEFIELD" in res["structures"]
