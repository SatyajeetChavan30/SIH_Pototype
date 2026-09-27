"""Real public well data from the Norwegian Offshore Directorate (Sodir) FactPages.

Stand-ins for OIL's internal sources (PS 26121 "Relevant data"):
  wellbore_exploration_all  -> well master data: location, spud year, TD, field, operator
  wellbore_formation_top    -> geological tops (lithostratigraphic GROUP level)
  wellbore_casing_and_lot   -> casing programme, hole sizes, leak-off / formation-integrity tests
  wellbore_mud              -> mud weight and mud type by depth
  wellbore_history          -> completion-report style narrative; StrataSense extracts kicks, losses, stuck pipe, fishing
The data is licensed under the Norwegian licence for Open Government data (NLOD 2.0): free reuse with attribution.

Honest limits: only near-vertical exploration wellbores are used (maximum inclination <= 15 deg), so MD is used as
TVD and the trajectory is a straight line to TD; ECD is not reported publicly and is set to mud weight plus a
margin; incidents come from history summaries, which under-report compared with daily drilling reports.
Run it through `python -m stratasense.cli build-public` with STRATASENSE_REGION=norway and a separate STRATASENSE_DATA_DIR.
"""
from __future__ import annotations

import csv
import html
import io
import re
import shutil
import urllib.request
from pathlib import Path

import numpy as np

from .. import config
from ..db import DB
from ..domain.ontology import FORMATION_BY_CODE

EXPORT = ("https://factpages.sodir.no/public?/Factpages/external/tableview/{table}&rs:Command=Render&rc:Toolbar=false"
          "&rc:Parameters=f&IpAddress=not_used&CultureCode=en&rs:Format=CSV&Top100=false")
TABLES = ("wellbore_exploration_all", "wellbore_formation_top", "wellbore_casing_and_lot", "wellbore_mud",
          "wellbore_history")
G_CM3_TO_PPG = 8.345
MAX_INC_DEG = 15.0
ECD_MARGIN_PPG = 0.3      # ECD is not published; a typical annular-friction margin, flagged in the UI and docs.
                          # Replaced by the ECD - MW measured while drilling in the Volve logs when those are imported
                          # (public/volve.py calibration.json)
ATTRIBUTION = ("Contains data under the Norwegian licence for Open Government data (NLOD) distributed by the "
               "Norwegian Offshore Directorate")


# ---------------------------------------------------------------------------------------------- fetch / read
def fetch(folder: Path, tables: tuple[str, ...] = TABLES, log=print) -> None:
    """Download the CSV exports once; later builds read the cached files (offline)."""
    folder.mkdir(parents=True, exist_ok=True)
    for t in tables:
        dest = folder / f"{t}.csv"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        log(f"  downloading {t}.csv from factpages.sodir.no")
        req = urllib.request.Request(EXPORT.format(table=t), headers={"User-Agent": "StrataSense/0.1 (SIH research prototype)"})
        with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)


def read_table(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text)))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def col(rows: list[dict], *cands: str) -> str | None:
    """Find a column by any of its known spellings (Sodir code names, some with historic typos, or display headers)."""
    if not rows:
        return None
    keys = {_norm(k): k for k in rows[0].keys()}
    for c in cands:
        if _norm(c) in keys:
            return keys[_norm(c)]
    return None


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- mapping
def group_code(name: str) -> str | None:
    """'CROMER KNOLL GP' -> 'CROMER_KNOLL' when it is a unit of this region's column."""
    code = re.sub(r"\s+(GP|GROUP)\.?$", "", (name or "").strip().upper()).replace(" ", "_")
    return code if code in FORMATION_BY_CODE else None


def quadrant(wellbore: str) -> str:
    return (wellbore or "").split("/")[0].strip()


def select_wells(expl: list[dict], quadrants: set[str] | None, max_inc: float = MAX_INC_DEG) -> list[dict]:
    name = col(expl, "wlbWellboreName", "wlbName", "Wellbore name")
    lat, lon = col(expl, "wlbNsDecDeg", "NS decimal degrees"), col(expl, "wlbEwDesDeg", "wlbEwDecDeg", "EW decimal degrees")
    td, tvd = col(expl, "wlbTotalDepth", "Total depth (MD) [m RKB]"), col(expl, "wlbFinalVerticalDepth", "Final vertical depth (TVD) [m RKB]")
    inc = col(expl, "wlbMaxInclation", "wlbMaxInclination", "Maximum inclination [°]", "Maximum inclination")
    area = col(expl, "wlbMainArea", "Main area")
    out = []
    for r in expl:
        wb = (r.get(name) or "").strip()
        if not wb or (quadrants and quadrant(wb) not in quadrants):
            continue
        if area and r.get(area) and "north sea" not in r[area].lower():
            continue
        la, lo, md = _f(r.get(lat)), _f(r.get(lon)), _f(r.get(td))
        if la is None or lo is None or not md:
            continue
        i = _f(r.get(inc)) if inc else None
        v = _f(r.get(tvd)) if tvd else None
        near_vertical = (i is not None and i <= max_inc) or (i is None and v is not None and v >= 0.97 * md)
        if not near_vertical:
            continue
        out.append(r)
    return out


def _year(v: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", v or "")
    return int(m.group(0)) if m else None


def build_master(db: DB, tables: dict[str, list[dict]], quadrants: set[str] | None, log=print) -> list[str]:
    expl = tables["wellbore_exploration_all"]
    wells = select_wells(expl, quadrants)
    c = {k: col(expl, *v) for k, v in {
        "name": ("wlbWellboreName", "wlbName", "Wellbore name"), "lat": ("wlbNsDecDeg", "NS decimal degrees"),
        "lon": ("wlbEwDesDeg", "wlbEwDecDeg", "EW decimal degrees"), "td": ("wlbTotalDepth", "Total depth (MD) [m RKB]"),
        "tvd": ("wlbFinalVerticalDepth", "Final vertical depth (TVD) [m RKB]"), "entry": ("wlbEntryDate", "Entered date"),
        "field": ("wlbField", "Field"), "disc": ("wlbDiscovery", "Discovery"), "content": ("wlbContent", "Content"),
        "status": ("wlbStatus", "Status"), "purpose": ("wlbPurpose", "Purpose"), "npdid": ("wlbNpdidWellbore", "NPDID wellbore"),
    }.items()}
    ids, by_struct = [], {}
    for r in wells:
        wid = r[c["name"]].strip()
        struct = (r.get(c["field"]) or r.get(c["disc"]) or "").strip() or f"Quadrant {quadrant(wid)}"
        sid = re.sub(r"[^A-Z0-9]+", "-", struct.upper()).strip("-")
        lat, lon, td = _f(r[c["lat"]]), _f(r[c["lon"]]), _f(r[c["td"]])
        by_struct.setdefault(sid, {"name": struct.title(), "pts": [], "years": []})
        by_struct[sid]["pts"].append((lat, lon))
        yr = _year(r.get(c["entry"]))
        if yr:
            by_struct[sid]["years"].append(yr)
        db.insert("wells", {"id": wid, "name": wid, "structure_id": sid, "lat": lat, "lon": lon,
                            "spud_date": (r.get(c["entry"]) or "")[:10], "spud_year": yr, "td_md": td,
                            "td_tvd": _f(r.get(c["tvd"])) or td, "status": (r.get(c["status"]) or "").lower(),
                            "traj_type": "vertical (approx.)", "target": (r.get(c["content"]) or "").title(),
                            "mud_system": None, "is_active": 0, "synthetic": 0, "source": "sodir"})
        # straight-line trajectory to TD: only near-vertical wellbores were selected, so MD ~ TVD
        db.executemany("INSERT INTO surveys (well_id, md, inc, azi, tvd, north, east) VALUES (?,?,?,?,?,?,?)",
                       [(wid, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (wid, td, 0.0, 0.0, td, 0.0, 0.0)])
        ids.append(wid)
    for sid, s in by_struct.items():
        a = np.array(s["pts"], dtype=float)
        db.insert("structures", {"id": sid, "name": s["name"], "lat": float(a[:, 0].mean()), "lon": float(a[:, 1].mean()),
                                 "prod_start": min(s["years"]) if s["years"] else None})
    log(f"  {len(ids)} near-vertical exploration wellbores in quadrants {sorted(quadrants) if quadrants else 'all'} "
        f"({len(by_struct)} fields/discoveries)")
    return ids


def build_tops(db: DB, rows: list[dict], wells: set[str]) -> int:
    name, top = col(rows, "wlbName", "Wellbore name"), col(rows, "lsuTopDepth", "Top depth [m]")
    unit, level = col(rows, "lsuName", "Lithostrat. unit"), col(rows, "lsuLevel", "Level")
    n = 0
    for r in rows:
        wid = (r.get(name) or "").strip()
        if wid not in wells or (level and (r.get(level) or "").upper() != "GROUP"):
            continue
        code, md = group_code(r.get(unit)), _f(r.get(top))
        if code is None or md is None:
            continue
        existing = db.one("SELECT md FROM tops WHERE well_id=? AND formation=?", (wid, code))
        if existing and existing["md"] <= md:
            continue
        db.insert("tops", {"well_id": wid, "formation": code, "md": md, "tvd": md, "source": "sodir"})
        n += 1
    return n


def _inch(v) -> str:
    """Sodir writes sizes as '13 3/8', ' 9 5/8', '30' -> '13-3/8"', '9-5/8"', '30"'."""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", s)
    x = int(m.group(1)) + int(m.group(2)) / int(m.group(3)) if m else _f(s)
    if x is None:
        return "?"
    whole, frac = int(x), x - int(x)
    for num, den in ((1, 8), (1, 4), (3, 8), (1, 2), (5, 8), (3, 4), (7, 8)):
        if abs(frac - num / den) < 0.02:
            return f'{whole}-{num}/{den}"'
    return f'{whole}"'


def build_sections(db: DB, casing: list[dict], mud: list[dict], wells: set[str],
                   ecd_margin: float = ECD_MARGIN_PPG) -> None:
    cn = col(casing, "wlbName", "Wellbore")
    ctype, cdia, cdep = col(casing, "wlbCasingType", "Casing type"), col(casing, "wlbCasingDiameter", "Casing diameter [inch]"), \
        col(casing, "wlbCasingDepth", "Casing depth [m]")
    hdia, hdep = col(casing, "wlbHoleDiameter", "Hole diameter [inch]"), col(casing, "wlbHoleDepth", "Hole depth [m]")
    lot, ltype = col(casing, "wlbLotMudDencity", "wlbLotMudDensity", "LOT/FIT mud equivalent [g/cm3]"), \
        col(casing, "wlbFormationTestType", "Formation test type")
    mn, mmd, mw, mtype = col(mud, "wlbName", "Wellbore"), col(mud, "wlbMD", "Depth MD [m]"), \
        col(mud, "wlbMudWeightAtMD", "Mud weight [g/cm3]", "Mud weight [g/cm³]"), col(mud, "wlbMudType", "Mud type")
    muds: dict[str, list[tuple[float, float, str]]] = {}
    for r in mud:
        wid = (r.get(mn) or "").strip()
        d, w = _f(r.get(mmd)), _f(r.get(mw))
        if wid in wells and d is not None and w and 0.8 < w < 2.6:
            muds.setdefault(wid, []).append((d, w * G_CM3_TO_PPG, (r.get(mtype) or "").strip().upper()))
    cas: dict[str, list[dict]] = {}
    for r in casing:
        wid = (r.get(cn) or "").strip()
        if wid in wells:
            cas.setdefault(wid, []).append(r)
    for wid in wells:
        td = db.one("SELECT td_md FROM wells WHERE id=?", (wid,))["td_md"]
        rows = sorted((r for r in cas.get(wid, []) if _f(r.get(cdep))), key=lambda r: _f(r.get(cdep)))
        m = muds.get(wid, [])
        typed = sorted((d, t) for d, _, t in m if t)
        if typed:   # the mud system of the deepest (reservoir) section characterises the well
            db.execute("UPDATE wells SET mud_system=? WHERE id=?", (typed[-1][1].title(), wid))

        def mw_in(a: float, b: float) -> float:
            sel = [w for d, w, _ in m if a <= d <= b]
            if not sel and m:
                sel = [min(m, key=lambda x: abs(x[0] - b))[1]]
            return round(float(np.median(sel)), 2) if sel else 9.0

        top, idx = 0.0, 0
        for r in rows:
            shoe = _f(r.get(cdep))
            if shoe is None or shoe <= top + 5:
                continue
            w = mw_in(top, shoe)
            db.insert("sections", {"well_id": wid, "idx": idx, "hole": _inch(r.get(hdia)),
                                   "casing": " ".join(x for x in (_inch(r.get(cdia)).replace("?", ""),
                                                                  (r.get(ctype) or "").strip().lower()) if x),
                                   "top_md": top, "shoe_md": shoe, "mw_ppg": w, "ecd_ppg": round(w + ecd_margin, 2)})
            lt = _f(r.get(lot)) if lot else None
            if lt and 0.8 < lt < 2.8:
                db.insert("lot_tests", {"well_id": wid, "depth_md": _f(r.get(hdep)) or shoe, "casing": _inch(r.get(cdia)),
                                        "emw_ppg": round(lt * G_CM3_TO_PPG, 2), "test_type": (r.get(ltype) or "").strip()},
                          replace=False)
            top, idx = shoe, idx + 1
        if td and td > top + 5:   # open hole below the last casing, down to TD
            w = mw_in(top, td)
            hole = _inch(rows[-1].get(hdia)) if rows else "?"
            db.insert("sections", {"well_id": wid, "idx": idx, "hole": hole, "casing": "open hole", "top_md": top,
                                   "shoe_md": td, "mw_ppg": w, "ecd_ppg": round(w + ecd_margin, 2)})


def history_pages(rows: list[dict], wells: set[str]) -> list[tuple[str, str]]:
    name, hist = col(rows, "wlbName", "Wellbore name"), col(rows, "wlbHistory", "History")
    out = []
    for r in rows:
        wid = (r.get(name) or "").strip()
        text = re.sub(r"<[^>]+>", " ", r.get(hist) or "")          # some histories carry simple HTML markup
        text = html.unescape(text)                                   # &quot; -> " (inch marks in hole sizes)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if wid in wells and len(text) > 40:
            out.append((wid, text))
    return out


# ---------------------------------------------------------------------------------------------- build
def build(folder: Path | None = None, quadrants: set[str] | None = None, download: bool = False, log=print) -> dict:
    """Build a real-data knowledge base in config.DATA_DIR (must be run with STRATASENSE_REGION=norway)."""
    from ..ingest.nlp import SentenceClassifier
    from ..ingest.pdf import PageText
    from ..ingest.pipeline import Ingestor
    from ..risk.model import train_and_evaluate

    if config.REGION != "norway":
        raise SystemExit("Run with STRATASENSE_REGION=norway (and a separate STRATASENSE_DATA_DIR, e.g. data_norway).")
    config.ensure_dirs()
    folder = folder or (config.DATA_DIR / "public" / "sodir")
    if download:
        fetch(folder, log=log)
    missing = [t for t in TABLES if not (folder / f"{t}.csv").exists()]
    if missing:
        raise SystemExit(f"Missing Sodir CSV exports in {folder}: {', '.join(missing)}. "
                         f"Download them (or re-run with --download).")
    tables = {t: read_table(folder / f"{t}.csv") for t in TABLES}

    for f in (config.DB_NAME, config.DB_NAME + "-wal", config.DB_NAME + "-shm"):
        (config.DATA_DIR / f).unlink(missing_ok=True)
    db = DB()
    db.init()
    ids = build_master(db, tables, quadrants, log)
    wells = set(ids)
    n_tops = build_tops(db, tables["wellbore_formation_top"], wells)
    from . import volve
    margin = volve.ecd_margin(config.DATA_DIR)
    build_sections(db, tables["wellbore_casing_and_lot"], tables["wellbore_mud"], wells,
                   margin if margin is not None else ECD_MARGIN_PPG)
    log(f"  ECD = MW + {margin if margin is not None else ECD_MARGIN_PPG} ppg "
        f"({'measured while drilling in the Volve logs' if margin is not None else 'assumed'})")
    # wells without any group tops cannot be correlated; keep them on the map but not as offsets
    db.commit()
    log(f"  {n_tops} group tops, sections and LOT/FIT tests loaded")

    # the shipped sentence classifier (trained on synthetic Assam text) - reported honestly as such
    src = config.ROOT / "data" / "models"
    for f in ("sentence_clf.joblib", "train_sentences.json"):
        if (src / f).exists() and not (config.MODELS_DIR / f).exists():
            shutil.copy(src / f, config.MODELS_DIR / f)
    if not (config.MODELS_DIR / "sentence_clf.joblib").exists():
        raise SystemExit("Build the Assam demo first (python -m stratasense.cli build-demo): its sentence classifier is reused.")
    ddr = config.DATA_DIR / "public" / "volve" / "ddr"
    if ddr.exists() and any(ddr.glob("*.xml")):
        # adapt the report reader to real text: synthetic training set + Volve DDR sentences labelled by the operator's
        # own activity codes; the replayed (active) wellbore's reports are held out
        from ..validate.volve import adapt_classifier
        adapted, info = adapt_classifier(ddr, exclude={volve.active_wellbore(config.DATA_DIR)} - {None}, log=log)
        adapted.save(config.MODELS_DIR / "sentence_clf.joblib")
        db.kv_set("classifier_source", info)
    clf = SentenceClassifier.load(config.MODELS_DIR / "sentence_clf.joblib")

    ing = Ingestor(db, clf)
    n_ev = 0
    pages = history_pages(tables["wellbore_history"], wells)
    for wid, text in pages:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", wid)
        # the section heading tells the extractor this is operational narrative (not report header lines)
        body = f"WELLBORE HISTORY (Sodir FactPages)\nWell: {wid}\n\nDRILLING COMPLICATIONS AND OPERATIONS\n{text}"
        res = ing._ingest_pages(Path(f"sodir_{safe}_history.txt"), [PageText(1, body, False, None)], "HISTORY",
                                f"{wid} wellbore history (Sodir FactPages)", wid, "sodir")
        n_ev += len(res["events"])
    db.commit()
    log(f"  {len(pages)} wellbore histories -> {n_ev} extracted events")

    latest = db.one("SELECT id FROM wells w WHERE EXISTS (SELECT 1 FROM tops t WHERE t.well_id=w.id) "
                    "AND EXISTS (SELECT 1 FROM sections s WHERE s.well_id=w.id) ORDER BY spud_year DESC, id DESC LIMIT 1")
    if latest:
        db.execute("UPDATE wells SET is_active=1 WHERE id=?", (latest["id"],))
        db.kv_set("active_well", latest["id"])
    if volve.artifact(config.DATA_DIR):   # real Volve wellbores were imported: wells, logs, reports, the active stream
        volve.apply(db, config.DATA_DIR, log=log, ingestor=ing)
    db.kv_set("public_source", {"source": "Sodir FactPages", "licence": "NLOD 2.0", "attribution": ATTRIBUTION,
                                "quadrants": sorted(quadrants) if quadrants else None, "wells": len(ids),
                                "histories": len(pages), "events": n_ev})
    db.commit()

    log("  training the risk model on the extracted real events (leave-wells-out)")
    try:
        metrics = train_and_evaluate(db)
    except Exception as e:  # noqa: BLE001 - too few events in a small area is a data limit, not a crash
        metrics = {"error": str(e)}
        log(f"  risk model skipped: {e}")
    db.kv_set("build_info", {"source": "sodir", "n_wells": len(ids), "n_docs": len(pages), "ocr_pages": 0,
                             "events": n_ev})
    return {"wells": len(ids), "tops": n_tops, "histories": len(pages), "events": n_ev,
            "risk": metrics.get("pooled") if isinstance(metrics, dict) else None}

