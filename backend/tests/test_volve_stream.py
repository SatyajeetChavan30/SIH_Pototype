"""Volve real-time WITSML -> Live Ops stream (public/volve.py).

The WITSML files here are generated SAMPLES in the layout and units of Equinor's Volve export (1series namespace,
-999.25 nulls, kkgf / kN.m / kPa / L/min / m3 / g/cm3, chunked log files, a wellbore folder named with $47$),
not real Volve data. Import and apply run in a subprocess with the norway region, like test_region_norway.py.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from stratasense import config
from stratasense.ingest.witsml_log import parse_log
from stratasense.public import volve
from stratasense.realtime.engine import CHANNELS

SODIR_FIX = Path(__file__).parent / "fixtures" / "sodir"
MNEMS = [("TIME", "s"), ("BDEP", "m"), ("DMEA", "m"), ("ROP", "m/h"), ("SWOB", "kkgf"), ("TQA", "kN.m"), ("RPM", "rpm"),
         ("SPPA", "kPa"), ("TFLO", "L/min"), ("MFOP", "%"), ("TVA", "m3"), ("HKLD", "kkgf"), ("GASA", "%"),
         ("MWTI", "g/cm3")]


def _log_xml(rows: list[list], name: str = "GenTime 12.25in") -> str:
    curves = "".join(f"<logCurveInfo uid='{m}'><mnemonic>{m}</mnemonic><unit>{u}</unit></logCurveInfo>" for m, u in MNEMS)
    data = "".join(f"<data>{','.join(str(v) for v in r)}</data>" for r in rows)
    return (f"<logs xmlns='http://www.witsml.org/schemas/1series' version='1.4.1.1'><log uidWell='W' uidWellbore='WB' "
            f"uid='L1'><nameWell>NO 15/9-F-14</nameWell><nameWellbore>NO 15/9-F-14</nameWellbore><name>{name}</name>"
            f"<indexType>date time</indexType><startDateTimeIndex>{rows[0][0]}</startDateTimeIndex>"
            f"<endDateTimeIndex>{rows[-1][0]}</endDateTimeIndex><nullValue>-999.25</nullValue>{curves}"
            f"<logData><mnemonicList>{','.join(m for m, _ in MNEMS)}</mnemonicList>"
            f"<unitList>{','.join(u for _, u in MNEMS)}</unitList>{data}</logData></log></logs>")


def write_sample(root: Path, hours: float = 3.0) -> Path:
    """A Volve-shaped export: one wellbore folder, a drilling time log in 2 chunk files, one trajectory."""
    wb = root / "Norway-StatoilHydro-15_$47$_9-F-14" / "1"
    t0 = dt.datetime(2008, 3, 1, 6, 0, tzinfo=dt.timezone.utc)
    rows, depth = [], 2500.0
    for i in range(int(hours * 3600 / 5)):
        t = t0 + dt.timedelta(seconds=5 * i)
        connection = 40 <= (i % 400) < 70                   # pumps off, bit off bottom: a connection every ~33 min
        rop = 0.0 if connection else 20.0
        depth += rop * 5 / 3600
        bit = depth - (3.0 if connection else 0.0)
        rows.append([t.isoformat().replace("+00:00", "Z"), round(bit, 2), round(depth, 2), rop,
                     -999.25 if i % 97 == 0 else (0 if connection else 10.0), 0 if connection else 15.0,
                     0 if connection else 120, 800 if connection else 20000, 0 if connection else 3000, 0 if connection else 98,
                     80.0 + 0.001 * i, 150.0, 1.2, 1.30])
    half = len(rows) // 2
    for k, part in enumerate((rows[:half], rows[half:]), 1):
        f = wb / "log" / "1" / "1" / "1" / f"{k:05d}.xml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_log_xml(part), encoding="utf-8")
    stations = "".join(f"<trajectoryStation uid='s{i}'><md uom='m'>{md}</md><incl uom='dega'>{inc}</incl>"
                       f"<azi uom='dega'>120</azi></trajectoryStation>"
                       for i, (md, inc) in enumerate([(0, 0), (500, 5), (1000, 20), (2000, 35), (3000, 40)]))
    tf = wb / "trajectory" / "1" / "00001.xml"
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(f"<trajectorys xmlns='http://www.witsml.org/schemas/1series' version='1.4.1.1'><trajectory uid='T'>"
                  f"<nameWellbore>NO 15/9-F-14</nameWellbore>{stations}</trajectory></trajectorys>", encoding="utf-8")
    return root


def write_ddr(root: Path) -> Path:
    """A Volve-shaped daily drilling report: drilling, then an operator-coded lost-circulation interruption at
    08:10-08:40 UTC (written in local time, +01:00), inside the sample log."""
    acts = [("2008-03-01T07:00:00+01:00", "2008-03-01T09:10:00+01:00", 2530, "drilling -- drill", "ok",
             "Drilled 12 1/4in hole to 2530 m"),
            ("2008-03-01T09:10:00+01:00", "2008-03-01T09:40:00+01:00", 2540, "interruption -- lost circulation", "fail",
             "Lost 30 m3/h returns at 2540 m. Pumped LCM pill, losses cured"),
            ("2008-03-01T09:40:00+01:00", "2008-03-02T00:00:00+01:00", 2580, "drilling -- drill", "ok",
             "Drilled ahead to 2580 m")]
    body = "".join(f"<activity><dTimStart>{a}</dTimStart><dTimEnd>{b}</dTimEnd><md uom='m'>{md}</md>"
                   f"<proprietaryCode>{code}</proprietaryCode><state>{st}</state><comments>{c}</comments></activity>"
                   for a, b, md, code, st, c in acts)
    f = root / "15_9-F-14" / "DDR_2008-03-01.xml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("<drillReports xmlns='http://www.witsml.org/schemas/1series' version='1.4.0.0'><drillReport uid='D1'>"
                 "<nameWell>NO 15/9-F-14</nameWell><nameWellbore>NO 15/9-F-14</nameWellbore>"
                 f"<dTimStart>2008-03-01T00:00:00+01:00</dTimStart>{body}</drillReport></drillReports>", encoding="utf-8")
    return root


def test_parse_log_nulls_units_and_alias_precedence():
    xml = _log_xml([["2008-03-01T06:00:00Z", 2500, 2500, 20, -999.25, 15, 120, 20000, 3000, 98, 80, 150, 1.2, 1.3]])
    r = parse_log(xml)[0]
    assert "wob" not in r                                              # -999.25 is the declared null
    assert r["torque"] == pytest.approx(15 * 0.737562)                 # kN.m -> kft.lbf
    assert r["spp"] == pytest.approx(20000 * 0.145038)                 # kPa -> psi
    assert r["flow_in"] == pytest.approx(3000 * 0.264172)              # L/min -> gpm
    assert r["pit"] == pytest.approx(80 * 6.28981)                     # m3 -> bbl
    assert r["hookload"] == pytest.approx(150 * 2.20462)               # kkgf -> klbs
    assert r["mw"] == pytest.approx(1.3 * 8.3454)                      # g/cm3 -> ppg
    assert r["md"] == 2500 and r["hole_depth"] == 2500
    assert r["t_epoch"] == dt.datetime(2008, 3, 1, 6, tzinfo=dt.timezone.utc).timestamp()


def test_wellbore_names_follow_sodir():
    assert volve.wellbore_name("Norway-StatoilHydro-15_$47$_9-F-14") == "15/9-F-14"
    assert volve.wellbore_name("Norway-Statoil-NO 15_$47$_9-F-1 C") == "15/9-F-1 C"
    assert volve.wellbore_name("NO 15/9-F-9 A") == "15/9-F-9 A"


def test_convert_gives_every_engine_channel(tmp_path):
    root = write_sample(tmp_path / "witsml")
    inv = volve.scan(root, log=lambda *_: None)
    assert list(inv) == ["15/9-F-14"] and inv["15/9-F-14"]["usable_files"] == 2
    well = volve.convert(root, hours=2, data_dir=tmp_path / "data", log=lambda *_: None)
    art = volve.artifact(tmp_path / "data")
    z = np.load(art[1])
    assert sorted(z.files) == sorted(CHANNELS)
    t = z["t"]
    assert np.all(np.diff(t) == volve.STEP_S) and len(t) == 240          # 2 h at 30 s
    assert np.all(z["tvd"] < z["md"])                                    # deviated survey (40 deg at TD)
    assert 10.5 < float(np.median(z["mw"])) < 11.2                       # 1.30 g/cm3
    assert 2800 < float(np.median(z["spp"][z["state"] == 0])) < 3000     # 20 000 kPa in psi
    assert set(np.unique(z["state"])) == {0.0, 1.0}                      # drilling and connections
    assert well["id"] == "15/9-F-14" and well["sections"][0]["hole"] == '12-1/4"'
    assert "tvd" in well["derived"] and "gr" in well["derived"]          # no gamma ray in these logs: filled, and said so
    assert "Equinor" in well["attribution"]


def test_ddr_incident_becomes_a_timed_scenario(tmp_path):
    root, ddr = write_sample(tmp_path / "witsml"), write_ddr(tmp_path / "ddr")
    inc = volve.incidents(ddr)
    assert list(inc) == ["15/9-F-14"] and len(inc["15/9-F-14"]) == 1
    x = inc["15/9-F-14"][0]
    assert x["hazard"] == "LOSS" and x["md"] == 2540
    assert x["t_start"] == dt.datetime(2008, 3, 1, 8, 10, tzinfo=dt.timezone.utc).timestamp()   # +01:00 honoured
    well = volve.convert(root, hours=2, data_dir=tmp_path / "data", log=lambda *_: None, ddr_root=ddr)
    ep, = well["episodes"]
    assert ep["id"] == "V1" and ep["hazard"] == "LOSS" and ep["md"] == 2540
    assert 60 <= ep["idx"] <= 240 - 60 and ep["t"] == ep["idx"] * volve.STEP_S           # lead-in and tail kept
    start = dt.datetime.fromisoformat(well["window"]["start"]).timestamp()
    assert start + ep["t"] == pytest.approx(x["t_start"], abs=volve.STEP_S)


def test_timed_episodes_are_scored_by_time():
    from stratasense.realtime.evaluate import near
    ep = {"id": "V1", "hazard": "LOSS", "md": 2540, "onset_md": 2538, "t": 7200.0, "t_end": 9000.0}
    assert near(ep, {"t": 7200 - 1700, "md": 1.0})          # depth is ignored for a timed incident
    assert not near(ep, {"t": 7200 - 1900, "md": 2540})
    assert near(ep, {"t": 9000 + 500, "md": 0}) and not near(ep, {"t": 9000 + 700, "md": 0})
    assert near(ep, {"t": 9000 + 700, "md": 0}, related=True)
    scripted = {"id": "S1", "hazard": "LOSS", "md": 2200, "onset_md": 2150}
    assert near(scripted, {"t": 0, "md": 2040}) and not near(scripted, {"t": 0, "md": 2020})


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")
def test_dashboard_scan_lists_wellbores_and_incidents(tmp_path):
    from fastapi.testclient import TestClient
    from stratasense.api.main import app
    root, ddr = write_sample(tmp_path / "witsml"), write_ddr(tmp_path / "ddr")
    with TestClient(app) as c:
        assert c.post("/api/admin/volve/scan", json={"folder": str(tmp_path / "nowhere")}).status_code == 400
        job = c.post("/api/admin/volve/scan", json={"folder": str(root), "ddr_folder": str(ddr)}).json()
        for _ in range(100):
            j = c.get(f"/api/jobs/{job['id']}").json()
            if j["status"] != "running":
                break
            time.sleep(0.1)
        assert j["status"] == "done", j.get("error")
        w, = j["result"]["wellbores"]
        assert w["wellbore"] == "15/9-F-14" and w["incidents"] == 1 and w["trajectory"] and w["files"] == 2


@pytest.mark.skipif(not (config.ROOT / "data" / "models" / "sentence_clf.joblib").exists(),
                    reason="the Assam demo classifier is reused by the public build")
def test_north_sea_build_installs_the_volve_stream(tmp_path):
    data = tmp_path / "data"
    volve.convert(write_sample(tmp_path / "witsml"), hours=2, data_dir=data, log=lambda *_: None,
                  ddr_root=write_ddr(tmp_path / "ddr"))
    code = f"""
import json
from pathlib import Path
from stratasense import config
from stratasense.public import sodir
from stratasense.db import DB
from stratasense.kb import KnowledgeBase
from stratasense.realtime.engine import LiveSession
sodir.build(Path(r"{SODIR_FIX}"), {{"15"}}, False, log=lambda *_: None)
db = DB(); kb = KnowledgeBase(db)
s = LiveSession(kb)
r = s.step(120)
ep = db.kv_get("active_episodes")[0]
s.jump_to_episode("V1")
jumped = s.i
print(json.dumps({{"active": kb.active_id, "stream": (config.LOGS_DIR / "active_stream.npz").exists(),
                  "episodes": [e["id"] for e in db.kv_get("active_episodes")], "idx": ep["idx"], "jumped": jumped,
                  "incidents": db.kv_get("stream_source")["incidents"], "source": db.kv_get("stream_source")["source"],
                  "n": s.n, "stepped": len(r["samples"]), "md": r["status"]["md"], "tvd": r["status"]["tvd"],
                  "offsets": len(kb.offsets())}}))
"""
    env = {**os.environ, "STRATASENSE_REGION": "norway", "STRATASENSE_DATA_DIR": str(data), "STRATASENSE_AUTH": "off"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600,
                         cwd=Path(__file__).parents[1])
    assert out.returncode == 0, out.stderr[-3000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["active"] == "15/9-F-14" and res["stream"] and res["episodes"] == ["V1"] and res["incidents"] == 1
    assert res["jumped"] == res["idx"] - 60                              # 30 min (60 samples) before the incident
    assert res["source"] == "Volve (Equinor)" and res["n"] == 240 and res["stepped"] == 120
    assert 2500 < res["md"] < 2600 and res["tvd"] < res["md"]
    assert res["offsets"] >= 1                                           # the Sodir wells stay offsets
