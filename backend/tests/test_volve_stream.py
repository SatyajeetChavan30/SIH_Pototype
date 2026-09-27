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


DEPTH_MNEMS = [("DEPTH", "m"), ("GRM1", "gAPI"), ("ROP", "m/h"), ("SWOB", "kkgf"), ("TQA", "kN.m"), ("RPM", "rpm"),
               ("SPPA", "kPa"), ("TFLO", "L/min"), ("MFOP", "%"), ("HKLD", "kkgf"), ("GASA", "%"), ("MWTI", "g/cm3"),
               ("ECD_ARC_RT", "g/cm3")]


def _log_xml(rows: list[list], name: str = "GenTime 12.25in", well: str = "15/9-F-14", mnems=MNEMS,
             depth: bool = False) -> str:
    curves = "".join(f"<logCurveInfo uid='{m}'><mnemonic>{m}</mnemonic><unit>{u}</unit></logCurveInfo>" for m, u in mnems)
    data = "".join(f"<data>{','.join(str(v) for v in r)}</data>" for r in rows)
    index = (f"<indexType>measured depth</indexType><startIndex uom='m'>{rows[0][0]}</startIndex>"
             f"<endIndex uom='m'>{rows[-1][0]}</endIndex>" if depth else
             f"<indexType>date time</indexType><startDateTimeIndex>{rows[0][0]}</startDateTimeIndex>"
             f"<endDateTimeIndex>{rows[-1][0]}</endDateTimeIndex>")
    return (f"<logs xmlns='http://www.witsml.org/schemas/1series' version='1.4.1.1'><log uidWell='W' uidWellbore='WB' "
            f"uid='L1'><nameWell>NO {well}</nameWell><nameWellbore>NO {well}</nameWellbore><name>{name}</name>"
            f"{index}<nullValue>-999.25</nullValue>{curves}"
            f"<logData><mnemonicList>{','.join(m for m, _ in mnems)}</mnemonicList>"
            f"<unitList>{','.join(u for _, u in mnems)}</unitList>{data}</logData></log></logs>")


def write_sample(root: Path, hours: float = 3.0, well: str = "15/9-F-14", depth_logs: bool = False) -> Path:
    """A Volve-shaped export: one wellbore folder, a drilling time log in 2 chunk files, one trajectory; with
    depth_logs also two depth-indexed section logs ('17 1/2in' 500-1600 m, '12 1/4in' 1600-2700 m) and a wbGeometry
    with 13-3/8in casing to 1600 m and 9-5/8in casing to 2550 m."""
    wb = root / f"Norway-StatoilHydro-{well.replace('/', '_$47$_')}" / "1"
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
        f = wb / "log" / "2" / "1" / "1" / f"{k:05d}.xml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_log_xml(part, well=well), encoding="utf-8")
    if depth_logs:
        for j, (name, a, b) in enumerate((("17 1/2in Section - MD Log", 500, 1600), ("12 1/4in Section - MD Log", 1600, 2700))):
            drows = [[md, 60 if (md // 100) % 2 else 120, 25, 10, 12, 140, 18000, 3200, 30, 150, 0.5, 1.25, 1.30]
                     for md in range(a, b, 1)]
            f = wb / "log" / "1" / str(j + 1) / "1" / "00001.xml"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(_log_xml(drows, name=name, well=well, mnems=DEPTH_MNEMS, depth=True), encoding="utf-8")
        secs = "".join(f"<wbGeometrySection uid='g{i}'><typeHoleCasing>{t}</typeHoleCasing><mdTop uom='m'>{a}</mdTop>"
                       f"<mdBottom uom='m'>{b}</mdBottom><odSection uom='m'>{od}</odSection></wbGeometrySection>"
                       for i, (t, a, b, od) in enumerate((("riser", 0, 20, 0.508), ("casing", 20, 1600, 0.339725),
                                                          ("casing", 1600, 2550, 0.244475))))
        gf = wb / "wbGeometry" / "1.xml"
        gf.parent.mkdir(parents=True, exist_ok=True)
        gf.write_text(f"<wbGeometrys xmlns='http://www.witsml.org/schemas/1series' version='1.4.1.1'><wbGeometry uid='G'>"
                      f"<nameWellbore>NO {well}</nameWellbore>{secs}</wbGeometry></wbGeometrys>", encoding="utf-8")
    stations = "".join(f"<trajectoryStation uid='s{i}'><md uom='m'>{md}</md><incl uom='dega'>{inc}</incl>"
                       f"<azi uom='dega'>120</azi></trajectoryStation>"
                       for i, (md, inc) in enumerate([(0, 0), (500, 5), (1000, 20), (2000, 35), (3000, 40)]))
    tf = wb / "trajectory" / "1" / "00001.xml"
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(f"<trajectorys xmlns='http://www.witsml.org/schemas/1series' version='1.4.1.1'><trajectory uid='T'>"
                  f"<nameWellbore>NO {well}</nameWellbore>{stations}</trajectory></trajectorys>", encoding="utf-8")
    return root


PICK_COLS = [("Well name", 24), ("Surface name", 40), ("Obs#", 5), ("Qlf", 3), ("MD", 8), ("TVD", 8), ("Intrp", 5)]


def write_picks(path: Path) -> Path:
    """A sample in the fixed-width layout of Volve's Well_picks_Volve_v1.dat (not real picks)."""
    rows = {"NO 15/9-F-14": [("NORDLAND GP. Top", "", 100, 100), ("HORDALAND GP. Top", "", 1000, 980),
                             ("SHETLAND GP. Top", "FO", 1500, 1400), ("Ty Fm. Top", "", 1900, 1750),
                             ("Draupne Fm. Top", "", 2530, 2290), ("Hugin Fm. VOLVE Base", "", 2600, 2330)],
            "NO 15/9-F-12": [("NORDLAND GP. Top", "", 110, 110), ("HORDALAND GP. Top", "", 1010, 990),
                             ("Draupne Fm. Top", "", 2530, 2280)]}

    def line(vals):
        return "  " + " ".join(str(v).ljust(w) for v, (_, w) in zip(vals, PICK_COLS))
    out = ["# Qlf column", "# FO: Faulted out", ""]
    for well, picks in rows.items():
        out += ["", f"Well {well}", line([c for c, _ in PICK_COLS]), line(["-" * w for _, w in PICK_COLS])]
        out += [line([well, surf, 1, q, f"{md:.2f}", f"{tvd:.2f}", "STAT"]) for surf, q, md, tvd in picks]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


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


def test_read_picks_parses_the_fixed_width_table(tmp_path):
    picks = volve.read_picks(write_picks(tmp_path / "picks.dat"))
    assert set(picks) == {"15/9-F-14", "15/9-F-12"}
    f14 = {p["surface"]: p for p in picks["15/9-F-14"]}
    assert f14["Draupne Fm. Top"]["md"] == 2530 and f14["Draupne Fm. Top"]["tvd"] == 2290
    assert f14["SHETLAND GP. Top"]["qlf"] == "FO"


def test_casing_geometry_depth_logs_and_sections(tmp_path):
    root = write_sample(tmp_path / "witsml", depth_logs=True)
    rec = volve.scan(root, log=lambda *_: None)["15/9-F-14"]
    geom = volve.read_geometry(rec["geometry"])
    assert [(g["type"], g["md_bottom"], round(g["od_in"], 3)) for g in geom] == \
        [("riser", 20, 20.0), ("casing", 1600, 13.375), ("casing", 2550, 9.625)]
    traj = volve.read_trajectory(rec["trajectories"])
    logs, intervals, cal = volve.depth_logs(rec, traj, log=lambda *_: None)
    from stratasense.data.logs_gen import CHANNELS as LOG_CHANNELS
    assert set(LOG_CHANNELS) - {"fm"} <= set(logs)
    assert np.allclose(np.diff(logs["md"]), 2.0) and logs["md"][0] == 500
    assert np.all(logs["tvd"] <= logs["md"] + 1e-6)
    assert 10.3 < np.median(logs["mw"]) < 10.6 and 10.8 < np.median(logs["ecd"]) < 11.0   # 1.25 / 1.30 g/cm3
    assert sorted(iv["hole"] for iv in intervals) == ['12-1/4"', '17-1/2"']
    assert abs(cal['12-1/4"']["median"] - 0.05 * 8.3454) < 0.01                        # measured ECD - MW
    secs = volve.sections_for(geom, intervals, logs, 3000.0, 10.0, 10.3)
    assert [(s_["hole"], s_["casing"], s_["shoe_md"]) for s_ in secs] == \
        [('17-1/2"', '13-3/8" casing', 1600), ('12-1/4"', '9-5/8" casing', 2550), ('12-1/4"', "open hole", 3000.0)]


def test_log_path_is_safe_for_public_names():
    assert config.log_path("15/9-F-12").name == "15_9-F-12.npz" and config.log_path("NDH-09").name == "NDH-09.npz"


@pytest.mark.skipif(not (config.ROOT / "data" / "models" / "train_sentences.json").exists(),
                    reason="the synthetic training sentences come from the Assam build")
def test_report_reader_adapts_on_real_text_and_holds_out_the_replayed_well(tmp_path):
    from stratasense.validate.volve import adapt_classifier
    ddr = write_ddr(tmp_path / "ddr")
    other = (ddr / "15_9-F-14" / "DDR_2008-03-01.xml").read_text(encoding="utf-8").replace("15/9-F-14", "15/9-F-12")
    (ddr / "15_9-F-12").mkdir()
    (ddr / "15_9-F-12" / "DDR_2008-03-01.xml").write_text(other, encoding="utf-8")
    clf, info = adapt_classifier(ddr, exclude={"15/9-F-14"}, log=lambda *_: None)
    assert info["held_out_reports"] == 1 and info["volve_reports"] == 1 and info["volve_sentences"] == 3
    assert info["labels"].get("LOSS") == 1 and info["synthetic_sentences"] > 1000
    p = clf.predict_proba(["Lost 30 m3/h returns at 2540 m, pumped LCM pill"])[0]
    assert max(p, key=p.get) == "LOSS"


@pytest.mark.skipif(not (config.ROOT / "data" / "models" / "sentence_clf.joblib").exists(),
                    reason="the Assam demo classifier is reused by the public build")
def test_import_all_makes_every_volve_wellbore_a_real_well(tmp_path):
    data = tmp_path / "data"
    root = write_sample(tmp_path / "witsml", hours=3, depth_logs=True)
    write_sample(root, hours=3, well="15/9-F-12", depth_logs=True)
    ddr, picks = write_ddr(tmp_path / "ddr"), write_picks(tmp_path / "picks" / "picks.dat")
    code = f"""
import json
from pathlib import Path
import numpy as np
from stratasense import config
from stratasense.public import sodir, volve
from stratasense.db import DB
from stratasense.kb import KnowledgeBase
from stratasense.correlation import load_log_decimated
from stratasense.realtime.engine import LiveSession
sodir.build(Path(r"{SODIR_FIX}"), {{"15"}}, False, log=lambda *_: None)
res = volve.import_all(r"{root}", r"{ddr}", r"{picks}", "15/9-F-14", hours=2, log=lambda *_: None, download=False)
db = DB(); kb = KnowledgeBase(db)
a, o = kb.wells["15/9-F-14"], kb.wells["15/9-F-12"]
z = np.load(config.LOGS_DIR / "active_stream.npz")
s = LiveSession(kb); s.step(240)
print(json.dumps({{"res": res, "active": kb.active_id, "a_tops": a.tops_md, "o_tops": o.tops_md,
    "sections": [(x["hole"], x["casing"], x["shoe_md"]) for x in a.sections],
    "truth": db.kv_get("active_truth_tops"), "fm": sorted(set(z["fm"].tolist())) if "fm" in z.files else None,
    "picked": s.picked, "offset_log": bool(load_log_decimated("15/9-F-12")),
    "offsets": [w.id for w in kb.offsets()], "docs": db.query("SELECT title FROM documents WHERE title LIKE '%Volve%'"),
    "source": db.kv_get("stream_source")}}, default=str))
"""
    env = {**os.environ, "STRATASENSE_REGION": "norway", "STRATASENSE_DATA_DIR": str(data), "STRATASENSE_AUTH": "off"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600,
                         cwd=Path(__file__).parents[1])
    assert out.returncode == 0, out.stderr[-3000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert sorted(r["res"]["wellbores"]) == ["15/9-F-12", "15/9-F-14"] and r["active"] == "15/9-F-14"
    assert r["a_tops"] == {"NORDLAND": 100, "HORDALAND": 1000, "ROGALAND": 1900, "VIKING": 2530}   # FO + base skipped
    assert r["truth"]["VIKING"] == 2290 and len(r["fm"]) == 2                  # the window crosses the Draupne pick
    assert "VIKING" in r["picked"]                                             # the replay's mud logger picks it
    assert r["sections"][:2] == [['17-1/2"', '13-3/8" casing', 1600], ['12-1/4"', '9-5/8" casing', 2550]]
    assert "15/9-F-12" in r["offsets"] and r["offset_log"] and "VIKING" in r["o_tops"]
    assert r["docs"] and r["source"]["tops"] == "Volve well picks" and len(r["source"]["wells"]) == 2
    assert r["res"]["ecd_margin_ppg"] == pytest.approx(0.42, abs=0.02)
