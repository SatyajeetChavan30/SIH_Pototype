"""Real rig-sensor data for Live Ops: Equinor's Volve field (block 15/9, Norwegian North Sea).

Sodir FactPages publish well records but no time series, so the real-data (norway) knowledge base has no stream.
Volve's public WITSML 1.4.1.1 export ("WITSML Realtime drilling data" folder, one sub-folder per wellbore) holds the
real surface-sensor logs of the Volve development wells. This module turns one wellbore of it into the replay file
Live Ops and the rig simulator already use (logs/active_stream.npz, see realtime/engine.py CHANNELS):

  scan(root)       header-only inventory: wellbores, their time- and depth-indexed logs, trajectories, casing geometry
  convert(root)    one wellbore: parse, merge chunk files, convert units, resample to 30 s, pick the most
                   drilling-active window, derive what a rig does not send (TVD from the WITSML survey, rig state,
                   d-exponent); also its depth-indexed offset logs (2 m), casing sections (wbGeometry) and picks.
                   Stored compactly in DATA_DIR/public/volve (stream_*.npz, logs_*.npz, well_*.json)
  import_all(root) every usable wellbore, the formation picks, the daily drilling reports and the ECD calibration
  apply(db)        every imported wellbore becomes a real well (survey, casing, picked tops, logs, report events);
                   the chosen one is the active well whose stream Live Ops replays

The artifacts are re-applied by every North Sea rebuild (public/sodir.py), so the multi-GB export is read only once.
Getting the data needs a (free) Databricks account: https://www.equinor.com/energy/volve-data-sharing ->
Databricks Marketplace "Volve Data Village". Licence: Equinor Open Data Licence (CC BY 4.0 based, no sale of the data);
the attribution below is shown wherever the stream is.

Other public Volve files used: Geophysical_Interpretations/Wells/Well_picks_Volve_v1.dat (formation picks) and
Well_technical_data/Daily Drilling Report - XML Version (WITSML drillReport). Honest limits: incidents are the
operator's activity codes (report time resolution, not hand-checked truth); hole sizes come from the log section
names and casing from wbGeometry, so a wellbore without them keeps a single open-hole section.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from .. import config
from ..data.logs_gen import dxc as dxc_formula
from ..geo import Trajectory, minimum_curvature
from ..ingest.witsml_log import FT_TO_M, _strip_ns, mnemonic_map, parse_log
from ..realtime.engine import _hole_in

ATTRIBUTION = ("Real-time drilling data from the Volve field, © Equinor and the former Volve licence partners "
               "(ExxonMobil Exploration and Production Norway AS, Bayerngas Norge AS), Equinor Open Data Licence")
SOURCE_URL = "https://www.equinor.com/energy/volve-data-sharing"
STEP_S = 30.0                    # resampling step: the detectors count samples; the synthetic stream uses 30-60 s
HEADER_BYTES = 65536             # Volve log headers stay under ~13 kB before <logData>
DRILLING = ("rop", "wob", "spp", "flow_in", "torque", "rpm")
STREAM_KEYS = ("t", "md", "tvd", "gr", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload",
               "gas", "mw", "ecd", "dxc", "state")      # = realtime.engine.CHANNELS
ECD_MARGIN_PPG = 0.3             # same assumption as the Sodir loader when ECD is not measured (public/sodir.py)
DEFAULT_MW_PPG = 9.0             # only when a log carries no mud weight at all (reported in the artifact)
# Volve wellhead template, from Sodir FactPages wellbore_development_all (15/9-F-* wellheads, 58.4416 N 1.8875 E);
# used only when that table is neither cached nor downloadable.
VOLVE_WELLHEAD = (58.4416, 1.8875)
DEV_TABLE = "wellbore_development_all"
PLAUSIBLE = {"mw": (7.0, 22.0), "ecd": (7.0, 22.0), "gr": (0.0, 400.0), "spp": (0.0, 10000.0),
             "hookload": (0.0, 2000.0), "flow_out": (0.0, 200.0)}   # engine units: ppg, API, psi, klbs, %
MAX_STEP_M = 5.0                # a hole-depth jump larger than this within 30 s is a depth reset, not drilling
LOG_STEP_M = 2.0                 # offset-log depth step, as the synthetic offset logs (data/logs_gen.offset_logs)
SKIP_QLF = {"FO", "NR", "ER"}    # well-pick quality flags: faulted out, not reached, eroded -> no top in this well
# hole size drilled for a casing OD (standard clearances), only when no log section names the bit size
HOLE_FOR_CASING = {30.0: '36"', 20.0: '26"', 18.625: '24"', 13.375: '17-1/2"', 10.75: '12-1/4"', 9.625: '12-1/4"',
                   7.0: '8-1/2"', 5.5: '6"'}
SNAP_M = 100.0                   # a casing shoe this close to where the bit size changes is that section's shoe
TYPICAL_CASING = {'36"': '30"', '26"': '20"', '17-1/2"': '13-3/8"', '12-1/4"': '9-5/8"', '8-1/2"': '7"'}
LEAD_S = 1800.0                 # a scenario jump starts 30 min before the operator-coded incident
TAIL_S = 1800.0                  # and the window keeps 30 min after it


# ---------------------------------------------------------------------------------------------- names
def wellbore_name(text: str) -> str | None:
    """'Norway-StatoilHydro-15_$47$_9-F-14' / 'NO 15/9-F-1 C' -> '15/9-F-14' / '15/9-F-1 C' (Sodir spelling)."""
    s = re.sub(r"\s*_?\$47\$_?\s*", "/", text or "")
    m = re.search(r"(\d{1,2}/\d{1,2}-[A-Z0-9]+(?:-\d+)?(?: [A-Z])?)\s*$", s.strip(), re.I)
    return m.group(1).upper() if m else None


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


# ---------------------------------------------------------------------------------------------- scan
def _header(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(HEADER_BYTES).decode("utf-8", errors="ignore")
    return head.split("<logData", 1)[0]


def _tag(text: str, tag: str) -> str | None:
    m = re.search(rf"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def _wellbore_of(path: Path, root: Path, head: str) -> str | None:
    for part in reversed(path.relative_to(root).parts[:-1]):
        if "$47$" in part:
            return wellbore_name(part)
    return wellbore_name(_tag(head, "nameWellbore") or "") or wellbore_name(root.name)


def scan(root: Path | str, log=print) -> dict[str, dict]:
    """Inventory of a Volve WITSML export (or any folder of WITSML 1.4.1 log/trajectory files), headers only."""
    root = Path(root)
    mapping = mnemonic_map()
    out: dict[str, dict] = {}
    logs: dict[tuple[str, Path], dict] = {}
    n = 0
    for f in sorted(root.rglob("*.xml")):
        head = _header(f)
        wb = _wellbore_of(f, root, head)
        if wb is None:
            continue
        rec = out.setdefault(wb, {"wellbore": wb, "logs": [], "trajectories": [], "geometry": []})
        if "<trajectoryStation" in head or re.search(r"<(?:\w+:)?trajectorys?\b", head):
            rec["trajectories"].append(str(f))
            continue
        if re.search(r"<(?:\w+:)?wbGeometry\b", head):
            rec["geometry"].append(str(f))
            continue
        if not re.search(r"<(?:\w+:)?logs?\b", head):
            continue
        n += 1
        key = (wb, f.parent)
        if key not in logs:
            mnems = [m.strip().upper() for m in re.findall(r"<(?:\w+:)?mnemonic\b[^>]*>(.*?)</", head)]
            channels = sorted({mapping[m] for m in mnems if m in mapping})
            logs[key] = {"name": _tag(head, "name") or f.parent.name, "dir": str(f.parent), "files": [],
                         "index": (_tag(head, "indexType") or "").lower(), "channels": channels,
                         "start": _tag(head, "startDateTimeIndex"), "end": _tag(head, "endDateTimeIndex")}
            rec["logs"].append(logs[key])
        lg = logs[key]
        lg["files"].append(str(f))
        for tag, agg in (("startIndex", min), ("endIndex", max)):     # depth range of a depth-indexed log
            v = _num(_tag(head, tag))
            if v is not None:
                lg[tag] = v if lg.get(tag) is None else agg(lg[tag], v)
        s, e = _tag(head, "startDateTimeIndex"), _tag(head, "endDateTimeIndex")
        if s and (not lg["start"] or s < lg["start"]):
            lg["start"] = s
        if e and (not lg["end"] or e > lg["end"]):
            lg["end"] = e
    for rec in out.values():
        usable = [lg for lg in rec["logs"] if _usable(lg)]
        rec["usable_logs"] = len(usable)
        rec["usable_files"] = sum(len(lg["files"]) for lg in usable)
        rec["span_days"] = round(sum(_days(lg) for lg in usable), 1)
    log(f"  scanned {n} log files: {len(out)} wellbores")
    return out


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _usable(lg: dict) -> bool:
    """A time-indexed log that carries depth and at least three drilling channels."""
    ch = set(lg["channels"])
    timed = "time" in lg["index"] or "time" in ch
    return timed and bool(ch & {"md", "hole_depth"}) and len(ch & set(DRILLING)) >= 3


def _span_has(lg: dict, when: dt.datetime) -> bool:
    try:
        a = dt.datetime.fromisoformat(lg["start"].replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(lg["end"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return a <= when <= b


def _days(lg: dict) -> float:
    try:
        a = dt.datetime.fromisoformat(lg["start"].replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(lg["end"].replace("Z", "+00:00"))
        return max((b - a).total_seconds() / 86400, 0.0)
    except (AttributeError, ValueError):
        return 0.0


def rank(inv: dict[str, dict]) -> list[dict]:
    """Wellbores most likely to hold a long drilling record first (header statistics only)."""
    return sorted((r for r in inv.values() if r["usable_logs"]),
                  key=lambda r: (r["usable_files"], r["span_days"]), reverse=True)


# ---------------------------------------------------------------------------------------------- parse
def _binned(files: list[str], log=print, key: str = "t_epoch",
            step: float = STEP_S) -> tuple[float, dict[str, np.ndarray]]:
    """Every channel resampled onto one grid of `step` along `key` (time: 30 s; depth logs: 2 m) as bin means,
    NaN where a channel has no sample. Returns (grid origin, channels).

    Real Volve wellbores hold around a gigabyte of XML (months of 1-10 s samples), so each chunk file is reduced
    to bin sums as soon as it is parsed; only those (a few percent of the raw rows) are kept in memory."""
    acc: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for i, f in enumerate(files):
        try:
            rows = parse_log(Path(f).read_bytes())
        except Exception as e:  # noqa: BLE001 - one malformed chunk must not stop the import
            log(f"  skipped {Path(f).name}: {e}")
            continue
        cols: dict[str, tuple[list[float], list[float]]] = {}
        for r in rows:
            t = r.get(key)
            if t is None or (key != "t_epoch" and t < 0):
                continue
            for k, v in r.items():
                if k not in (key, "t_epoch"):
                    ts, vs = cols.setdefault(k, ([], []))
                    ts.append(t)
                    vs.append(v)
        for k, (ts, vs) in cols.items():
            b = np.floor(np.asarray(ts) / step).astype(np.int64)
            u, inv = np.unique(b, return_inverse=True)
            acc.setdefault(k, []).append((u, np.bincount(inv, weights=np.asarray(vs)), np.bincount(inv)))
        if (i + 1) % 25 == 0:
            log(f"  parsed {i + 1}/{len(files)} files")
    if not acc:
        return 0.0, {}
    b0 = min(int(p[0][0]) for parts in acc.values() for p in parts)
    b1 = max(int(p[0][-1]) for parts in acc.values() for p in parts)
    n = b1 - b0 + 1
    grid = {}
    for k, parts in acc.items():
        idx = np.concatenate([u for u, _, _ in parts]) - b0
        s = np.bincount(idx, weights=np.concatenate([x for _, x, _ in parts]), minlength=n)
        c = np.bincount(idx, weights=np.concatenate([x for _, _, x in parts]), minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            grid[k] = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    return b0 * step, grid


def _pick_window(g: dict[str, np.ndarray], n_win: int, incident_idx: list[int] | None = None) -> tuple[int, int, int]:
    """Start/end of the window that drills the most new hole while ROP, WOB, SPP and flow are all recorded.

    Score = metres of new hole (hole depth rising, steps over MAX_STEP_M ignored as depth resets) plus a small
    credit per drilling sample. Each operator-coded incident that starts inside a window, with LEAD_S of data
    before it and TAIL_S after, is worth far more than any amount of drilling, so a replay with real problems wins."""
    n = len(next(iter(g.values())))
    nan = np.full(n, np.nan)
    depth = g.get("md", g.get("hole_depth", nan))
    rop, wob, spp, flow = (g.get(k, nan) for k in ("rop", "wob", "spp", "flow_in"))
    drilling = np.isfinite(depth) & (np.nan_to_num(rop) > 0.5) & (np.nan_to_num(flow) > 100) & np.isfinite(wob) \
        & np.isfinite(spp)
    if "flow_out" in g:     # returns to the rig: skips riserless top hole (seawater, no returns), where loss alarms are meaningless
        drilling &= np.nan_to_num(g["flow_out"]) > 5
    if n <= n_win:
        return 0, n, int(drilling.sum())
    hole = _fill(g.get("hole_depth", depth), 0.0)
    step = np.diff(hole, prepend=hole[0])
    new_hole = np.where(drilling & (step > 0) & (step < MAX_STEP_M), step, 0.0)
    c = np.concatenate([[0], np.cumsum(drilling)])
    m = np.concatenate([[0.0], np.cumsum(new_hole)])
    count = c[n_win:] - c[:-n_win]
    score = (m[n_win:] - m[:-n_win]) + 0.01 * count
    lead, tail = int(LEAD_S // STEP_S), int(TAIL_S // STEP_S)
    bonus = np.zeros(len(score) + 1)
    big = 10.0 * (float(np.max(score)) + n_win)
    for k in incident_idx or []:
        lo, hi = max(k + tail - n_win + 1, 0), min(k - lead, len(score) - 1)   # window starts that contain k
        if lo <= hi:
            bonus[lo] += big
            bonus[hi + 1] -= big
    # an incident only counts in a window that is mostly drilling (incidents during a trip or P&A would otherwise
    # pull the replay onto hours with no drilling data)
    score += np.where(count >= 0.25 * n_win, np.cumsum(bonus)[:-1], 0.0)
    i = int(np.argmax(score))
    return i, i + n_win, int(count[i])


def _fill(a: np.ndarray, default: float) -> np.ndarray:
    """Forward-fill, then back-fill, then the default: the engine's rule for channels a feed stops sending."""
    a = a.astype(float).copy()
    ok = np.isfinite(a)
    if not ok.any():
        return np.full(len(a), default)
    idx = np.where(ok, np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    a = a[idx]
    first = int(np.argmax(ok))
    a[:first] = a[first]
    return a


# ---------------------------------------------------------------------------------------------- trajectory
_TRAJ_UNITS = {"ft": FT_TO_M, "ft_us": FT_TO_M, "rad": 57.29578}


def _station_value(st, tag: str) -> float | None:
    el = st.find(tag)
    if el is None or not (el.text or "").strip():
        return None
    try:
        x = float(el.text)
    except ValueError:
        return None
    return x * _TRAJ_UNITS.get((el.get("uom") or "").lower(), 1.0)


def read_trajectory(files: list[str]) -> list[tuple[float, float, float]]:
    """The WITSML trajectory with the most stations, as (md m, inclination deg, azimuth deg)."""
    best: list[tuple[float, float, float]] = []
    for f in files:
        try:
            root = ET.fromstring(Path(f).read_bytes())
        except ET.ParseError:
            continue
        _strip_ns(root)
        for tr in ([root] if root.tag == "trajectory" else root.findall(".//trajectory")):
            pts = {}
            for st in tr.findall("trajectoryStation"):
                md, inc, azi = (_station_value(st, k) for k in ("md", "incl", "azi"))
                if md is not None and inc is not None and azi is not None and 0 <= inc < 180:
                    pts[round(md, 2)] = (md, inc, azi % 360)
            stations = [pts[k] for k in sorted(pts)]
            if len(stations) > len(best):
                best = stations
    if best and best[0][0] > 0:
        best.insert(0, (0.0, 0.0, best[0][2]))
    return best


# ---------------------------------------------------------------------------------------------- Sodir facts
def sodir_facts(wellbore: str, data_dir: Path, download: bool = False, log=print) -> dict:
    """Wellhead position, TD and dates of a Volve development wellbore from Sodir FactPages (cached, NLOD)."""
    from . import sodir
    folder = data_dir / "public" / "sodir"
    path = folder / f"{DEV_TABLE}.csv"
    if not path.exists() and download:
        try:
            sodir.fetch(folder, (DEV_TABLE,), log=log)
        except Exception as e:  # noqa: BLE001 - offline: fall back to the Volve template position
            log(f"  Sodir {DEV_TABLE} not available ({e}); using the Volve template position")
    if not path.exists():
        return {}
    for r in sodir.read_table(path):
        if (r.get("wlbWellboreName") or "").strip().upper() == wellbore.upper():
            f = sodir._f
            return {"lat": f(r.get("wlbNsDecDeg")), "lon": f(r.get("wlbEwDecDeg")), "td_md": f(r.get("wlbTotalDepth")),
                    "td_tvd": f(r.get("wlbFinalVerticalDepth")), "entry": (r.get("wlbEntryDate") or "").strip(),
                    "completion": (r.get("wlbCompletionDate") or "").strip(),
                    "purpose": (r.get("wlbPurpose") or "").strip().lower(), "field": (r.get("wlbField") or "").strip()}
    return {}


# ---------------------------------------------------------------------------------------------- DDR incidents
def _epoch(ts: str | None) -> float | None:
    """ISO time of a DDR activity -> epoch seconds (a time without an offset is taken as UTC)."""
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


def incidents(ddr_root: Path | str, wellbore: str | None = None, reports: dict | None = None) -> dict[str, list[dict]]:
    """Operator-coded drilling problems per wellbore from Volve daily drilling reports (WITSML drillReport XML).

    The hazard comes from the operator's activity code (validate/volve.code_hazard: lost circulation, stuck pipe,
    well control, fishing, tight hole); consecutive activities with the same hazard are one incident, as in
    validate/volve.truth_events. These are the operator's codes, not hand-checked truth."""
    from ..validate.volve import code_hazard, load_reports
    by_well = reports if reports is not None else load_reports(ddr_root)
    out: dict[str, list[dict]] = {}
    for rs in by_well.values():
        for r in rs:
            wb = wellbore_name(r.get("wellbore") or "") or wellbore_name(r["well"])
            if wb is None or (wellbore and wb != wellbore):
                continue
            lst = out.setdefault(wb, [])
            for a in r["activities"]:
                hz, t0 = code_hazard(a["code"]), _epoch(a.get("t_start"))
                if hz is None or t0 is None:
                    continue
                t1 = _epoch(a.get("t_end")) or t0
                last = lst[-1] if lst else None
                if last and last["hazard"] == hz and t0 - last["t_end"] <= 3600:
                    last["t_end"] = max(last["t_end"], t1)
                    continue
                lst.append({"hazard": hz, "t_start": t0, "t_end": t1, "md": a["md"], "code": a["code"],
                            "comment": a["comments"], "date": r["date"]})
    for lst in out.values():
        lst.sort(key=lambda x: x["t_start"])
    return out


def _episodes(incs: list[dict], t0: float, i0: int, i1: int, md: np.ndarray) -> list[dict]:
    """Incidents inside the replay window as Live Ops scenarios (jump targets and evaluation truth)."""
    eps = []
    for x in incs:
        k = int((x["t_start"] - t0) // STEP_S) - i0
        if not 0 <= k < i1 - i0:
            continue
        at = float(md[k])
        depth = x["md"] if x["md"] is not None else at
        n = len(eps) + 1
        eps.append({"id": f"V{n}", "hazard": x["hazard"], "md": round(depth, 1), "onset_md": round(at, 1),
                    "t": k * STEP_S, "t_end": round(min(x["t_end"] - t0 - i0 * STEP_S, (i1 - i0 - 1) * STEP_S), 1),
                    "idx": k, "label": f"{x['code']} @ {depth:.0f} m, {x['date']}", "code": x["code"],
                    "comment": x["comment"], "source": "Volve DDR (operator activity code)"})
    return eps


def _hole_from_names(names: list[str]) -> str | None:
    """'12 1/4in Section - Time Log' / '8 1/2 in Section' / '26in' / '12.25in' -> '12-1/4"' (bit size for the d-exponent)."""
    for nm in names:
        m = re.search(r"(?<![\d/])(\d{1,2})(?:[.,](\d+)|\s+(\d)/(\d))?\s*(?:in\b|\")", nm or "", re.I)
        if m:
            x = float(m.group(1)) + (float("0." + m.group(2)) if m.group(2) else
                                     float(m.group(3)) / float(m.group(4)) if m.group(3) else 0.0)
            whole, frac = int(x), x - int(x)
            for num, den in ((1, 8), (1, 4), (3, 8), (1, 2), (5, 8), (3, 4), (7, 8)):
                if abs(frac - num / den) < 0.02:
                    return f'{whole}-{num}/{den}"'
            return f'{whole}"'
    return None


# ---------------------------------------------------------------------------------------------- formation picks
def read_picks(path: Path | str) -> dict[str, list[dict]]:
    """Volve's well-pick table (Geophysical_Interpretations/Wells/Well_picks_Volve_v1.dat): per wellbore, the picked
    surfaces with MD, TVD and quality flag. The file is fixed-width; columns are read from each block's dashed rule."""
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    out: dict[str, list[dict]] = {}
    spans: list[tuple[int, int | None]] | None = None
    names: list[str] = []
    for i, ln in enumerate(lines):
        if re.fullmatch(r"\s*-{3,}(\s+-+)+\s*", ln):
            starts = [m.start() for m in re.finditer(r"-+", ln)]
            spans = [(a, starts[k + 1] if k + 1 < len(starts) else None) for k, a in enumerate(starts)]
            names = [lines[i - 1][a:b].strip().lower() for a, b in spans]
            continue
        if not spans or not ln.strip() or ln.lstrip().startswith(("#", "Well ")) or ln.startswith("Well "):
            continue
        cell = {names[k]: ln[a:b].strip() for k, (a, b) in enumerate(spans)}
        wb = wellbore_name(cell.get("well name", ""))
        md = _num(cell.get("md"))
        if wb is None or md is None:
            continue
        out.setdefault(wb, []).append({"surface": cell.get("surface name", ""), "md": md, "tvd": _num(cell.get("tvd")),
                                       "qlf": cell.get("qlf", "")})
    return out


def picks_to_tops(entries: list[dict]) -> dict[str, dict]:
    """Picks -> tops of this region's units ({code: {md, tvd}}), the shallowest pick per unit. 'NORDLAND GP. Top'
    and formation picks ('Draupne Fm. Top' -> Viking) resolve through the ontology's aliases; base picks, the
    seabed and faulted-out / not-reached / eroded picks are skipped."""
    from ..domain.ontology import FORMATION_BY_CODE
    from ..ingest.nlp import detect_formation
    tops: dict[str, dict] = {}
    for e in entries:
        surf = e.get("surface") or ""
        if (e.get("qlf") or "").upper() in SKIP_QLF or re.search(r"\bbase\b|seabed", surf, re.I):
            continue
        norm = re.sub(r"\bFm\b\.?", "formation", surf, flags=re.I)
        norm = re.sub(r"\bGP\b\.?", "group", norm, flags=re.I)
        code = detect_formation(re.sub(r"\btop\b", "", norm, flags=re.I))
        if code is None or code not in FORMATION_BY_CODE:
            continue
        md = float(e["md"])
        tvd = float(e["tvd"]) if e.get("tvd") is not None else md
        if code not in tops or md < tops[code]["md"]:
            tops[code] = {"md": md, "tvd": tvd}
    return tops


def _fm_index(tops: dict[str, dict], tvd: np.ndarray) -> np.ndarray:
    """Formation (index into FORMATION_ORDER) at each TVD, from picked tops."""
    from ..domain.ontology import FORMATION_ORDER
    order = sorted(((v["tvd"], FORMATION_ORDER.index(c)) for c, v in tops.items() if c in FORMATION_ORDER))
    if not order:
        return np.zeros(len(tvd))
    at = np.array([t for t, _ in order])
    code = np.array([c for _, c in order], dtype=float)
    k = np.clip(np.searchsorted(at, np.asarray(tvd, dtype=float), side="right") - 1, 0, len(order) - 1)
    return code[k]


# ---------------------------------------------------------------------------------------------- casing and depth logs
def _length_in(el) -> float | None:
    if el is None or not (el.text or "").strip():
        return None
    try:
        x = float(el.text)
    except ValueError:
        return None
    return x * {"m": 39.3701, "mm": 1 / 25.4, "cm": 1 / 2.54, "ft": 12.0}.get((el.get("uom") or "in").lower(), 1.0)


def read_geometry(files: list[str]) -> list[dict]:
    """Casing and liner strings from the wellbore's WITSML wbGeometry. A wellbore can carry several geometry reports;
    the most consistent one wins (strings getting smaller with depth), then the one with the most strings."""
    best: list[dict] = []
    best_score = (-1e9, 0)
    for f in files:
        try:
            root = ET.fromstring(Path(f).read_bytes())
        except ET.ParseError:
            continue
        _strip_ns(root)
        for g in ([root] if root.tag == "wbGeometry" else root.iter("wbGeometry")):
            secs = []
            for sec in g.findall("wbGeometrySection"):
                typ = (sec.findtext("typeHoleCasing") or "").strip().lower()
                top, bot = _station_value(sec, "mdTop"), _station_value(sec, "mdBottom")
                od = _length_in(sec.find("odSection"))
                if typ and bot is not None:
                    secs.append({"type": typ, "md_top": top or 0.0, "md_bottom": bot, "od_in": od})
            strings = sorted((x for x in secs if x["type"] in ("casing", "liner") and x["od_in"]),
                             key=lambda x: x["md_bottom"])
            bad = sum(1 for a, b in zip(strings, strings[1:]) if b["od_in"] >= a["od_in"] - 0.01)
            score = (len(strings) - 2 * bad, len(secs))
            if score > best_score:
                best, best_score = secs, score
    return sorted(best, key=lambda x: x["md_bottom"])


def depth_logs(rec: dict, traj: list, log=print) -> tuple[dict | None, list[dict], dict]:
    """The wellbore's depth-indexed drilling logs ('12 1/4in Section - MD Log') on a 2 m grid: the offset-log
    channels of data/logs_gen.CHANNELS except fm (added once tops are known). Also the hole size of each log
    section, and the measured ECD - MW margin while drilling (median and count per hole size)."""
    dl = [lg for lg in rec["logs"] if "depth" in lg["index"] and "time" not in lg["index"]
          and {"gr", "rop"} & set(lg["channels"])]
    if not dl:
        return None, [], {}
    files = [f for lg in dl for f in sorted(lg["files"])]
    x0, g = _binned(files, log=log, key="hole_depth", step=LOG_STEP_M)
    if not g:
        return None, [], {}
    n = len(next(iter(g.values())))
    nan = np.full(n, np.nan)
    has = np.isfinite(g.get("gr", nan)) | np.isfinite(g.get("rop", nan))
    if not has.any():
        return None, [], {}
    lo, hi = int(np.argmax(has)), n - int(np.argmax(has[::-1]))
    g = {k: v[lo:hi] for k, v in g.items()}
    for k, (a, b) in PLAUSIBLE.items():
        if k in g:
            g[k] = np.where((g[k] >= a) & (g[k] <= b), g[k], np.nan)
    m = hi - lo
    nan = np.full(m, np.nan)
    md = x0 + (lo + np.arange(m)) * LOG_STEP_M
    out = {"md": md}
    for k, d in (("gr", 0.0), ("rop", 0.0), ("wob", 0.0), ("rpm", 0.0), ("torque", 0.0), ("spp", 0.0),
                 ("flow_in", 0.0), ("flow_out", 100.0), ("hookload", 0.0), ("gas", 0.0), ("mw", DEFAULT_MW_PPG)):
        out[k] = _fill(g.get(k, nan), d)
    out["rop"], out["wob"], out["torque"] = np.clip(out["rop"], 0, 200), np.clip(out["wob"], 0, 150), \
        np.clip(out["torque"], 0, None)
    measured_ecd = np.isfinite(g.get("ecd", nan))
    out["ecd"] = _fill(g["ecd"], 0.0) if measured_ecd.any() else out["mw"] + ECD_MARGIN_PPG
    intervals = [{"hole": h, "md_from": lg["startIndex"], "md_to": lg["endIndex"]} for lg in dl
                 if (h := _hole_from_names([lg["name"]])) and lg.get("startIndex") is not None
                 and lg.get("endIndex") is not None]
    bit = np.array([_hole_in(_hole_at(intervals, x) or '8-1/2"') for x in md])
    d = dxc_formula(out["rop"], out["rpm"], out["wob"], bit, np.maximum(out["mw"], 7.0))
    calc = np.where((out["rop"] > 0) & (out["wob"] > 0) & (out["rpm"] > 0), d, np.nan)
    out["dxc"] = _fill(np.where(np.isfinite(g.get("dxc", nan)), g.get("dxc", nan), calc), 1.0)
    out["tvd"] = Trajectory(*np.array(traj).T).tvd_at_md(md) if len(traj) >= 2 else md.copy()
    # measured ECD - MW while drilling: calibrates the ECD margin assumed for wells that publish only mud weight
    drilling = measured_ecd & np.isfinite(g.get("mw", nan)) & (out["rop"] > 0.5)
    diff = (g.get("ecd", nan) - g.get("mw", nan))[drilling]
    ok = (diff > 0) & (diff < 2.0)
    cal: dict = {}
    for x, dd in zip(md[drilling][ok], diff[ok]):
        cal.setdefault(_hole_at(intervals, x) or "?", []).append(float(dd))
    cal = {h: {"median": round(float(np.median(v)), 3), "n": len(v)} for h, v in cal.items()}
    log(f"  {rec['wellbore']}: offset logs {md[0]:.0f}-{md[-1]:.0f} m MD from {len(dl)} depth logs")
    return out, intervals, cal


def _hole_at(intervals: list[dict], md: float) -> str | None:
    """Bit size drilling at this depth: the narrowest log section that spans it."""
    hits = [iv for iv in intervals if iv["md_from"] - 1 <= md <= iv["md_to"] + 1]
    return min(hits, key=lambda iv: iv["md_to"] - iv["md_from"])["hole"] if hits else None


def sections_for(geom: list[dict], intervals: list[dict], logs: dict | None, td: float, mw: float, ecd: float,
                 default_hole: str | None = None) -> list[dict]:
    """Hole sections as drilled. Boundaries: where the bit size changes in the depth logs (the end of each log
    section) and the real casing / liner shoes of wbGeometry, merged when within SNAP_M. Bit size from the log
    covering the section (else the previous section's); casing label from the real string at the shoe, else the
    usual casing for that hole marked 'typical'. MW and ECD from the depth logs in the interval (else the stream's)."""
    from .sodir import _inch
    strings = [x for x in geom if x["type"] in ("casing", "liner") and x["md_bottom"] > 30 and x.get("od_in")]
    # bits run largest first: one boundary per bit change, at the deepest depth logged with the larger bit
    # (a log's own depth range can be off, so the drilling order, not the ranges alone, decides)
    reach: dict[str, float] = {}
    for iv in intervals:
        if iv.get("hole"):
            reach[iv["hole"]] = max(reach.get(iv["hole"], 0.0), float(iv["md_to"]))
    bits = sorted(reach, key=_hole_in, reverse=True)
    ends, last = [], 0.0
    for h in bits[:-1]:
        last = max(reach[h], last)
        ends.append(last)

    def bit_at(md: float) -> str | None:
        return bits[sum(1 for e in ends if e < md)] if bits else None
    marks: list[tuple[float, dict | None]] = [(e, None) for e in ends]
    for st in strings:
        near = [k for k, (b, _) in enumerate(marks) if abs(b - st["md_bottom"]) <= SNAP_M]
        if near:
            marks[near[0]] = (st["md_bottom"], st)        # the real shoe replaces the log section end
        else:
            marks.append((st["md_bottom"], st))
    td = float(td or 0.0) or max(reach.values(), default=0.0)
    marks = sorted((b, st) for b, st in marks if 5 < b < td - 5) if td else sorted(marks)
    marks.append((round(td, 1), None))
    secs, top, prev_hole = [], 0.0, None
    for b, st in marks:
        if b <= top + 5:
            continue
        hole = bit_at((top + b) / 2) or prev_hole
        if hole is None and st:
            od = min(HOLE_FOR_CASING, key=lambda k: abs(k - st["od_in"]))
            hole = HOLE_FOR_CASING[od] if abs(od - st["od_in"]) < 0.3 else None
        hole = hole or default_hole or "?"
        mw_s, ecd_s = mw, ecd
        if logs is not None:
            sel = (logs["md"] >= top) & (logs["md"] <= b) & (logs["rop"] > 0.5)
            if sel.sum() >= 5:
                mw_s, ecd_s = float(np.median(logs["mw"][sel])), float(np.median(logs["ecd"][sel]))
        if st:
            casing = f'{_inch(st["od_in"]).replace("?", "")} {st["type"]}'.strip()
        elif b < td - 5 and hole in TYPICAL_CASING:
            casing = f'{TYPICAL_CASING[hole]} casing (typical)'
        else:
            casing = "open hole"
        secs.append({"idx": len(secs), "hole": hole, "casing": casing, "top_md": round(top, 1), "shoe_md": round(b, 1),
                     "mw_ppg": round(mw_s, 2), "ecd_ppg": round(max(ecd_s, mw_s), 2)})
        top, prev_hole = b, hole
    return secs


# ---------------------------------------------------------------------------------------------- convert
def convert(root: Path | str, wellbore: str | None = None, hours: float = 12.0, data_dir: Path | None = None,
            log=print, inventory: dict | None = None, download: bool = False, ddr_root: Path | str | None = None,
            incs: dict[str, list[dict]] | None = None) -> dict:
    """Build the compact stream artifact for one wellbore; returns its summary.

    download=True fetches Sodir's development-wellbore table once (wellhead position, TD, dates) if not cached.
    ddr_root (Volve daily drilling reports) adds the operator-coded incidents: the window is chosen to contain them
    and they become the Live Ops scenarios."""
    data_dir = data_dir or config.DATA_DIR
    inv = inventory if inventory is not None else scan(root, log=log)
    if incs is None:
        incs = incidents(ddr_root) if ddr_root else {}
    ranked = rank(inv)
    if not ranked:
        raise SystemExit(f"No time-indexed WITSML drilling logs found under {root}. Point this at the Volve "
                         f"'WITSML Realtime drilling data' folder (or one wellbore folder inside it).")
    if wellbore:
        rec = inv.get(wellbore_name(wellbore) or wellbore.upper())
        if rec is None or not rec["usable_logs"]:
            raise SystemExit(f"Wellbore {wellbore} has no usable drilling time logs here. "
                             f"Candidates: {', '.join(r['wellbore'] for r in ranked)}")
    else:
        rec = next((r for r in ranked if incs.get(r["wellbore"])), ranked[0])   # prefer a well with real incidents
    wb = rec["wellbore"]
    wb_incs = incs.get(wb, [])
    logs = [lg for lg in rec["logs"] if _usable(lg)]
    files = [f for lg in logs for f in sorted(lg["files"])]
    log(f"  {wb}: reading {len(files)} files from {len(logs)} time logs")
    t0, g = _binned(files, log=log)
    if not g or not ({"md", "hole_depth"} & set(g)):
        raise SystemExit(f"{wb}: the logs carry no bit or hole depth")
    n_win = max(int(hours * 3600 / STEP_S), 60)
    i0, i1, n_drill = _pick_window(g, n_win, [int((x["t_start"] - t0) // STEP_S) for x in wb_incs])
    if n_drill < 30:
        raise SystemExit(f"{wb}: no stretch with ROP, WOB, pump pressure and flow recorded together; try another "
                         f"wellbore ({', '.join(r['wellbore'] for r in ranked[:5])})")
    w = {k: v[i0:i1] for k, v in g.items()}
    for k, (lo, hi) in PLAUSIBLE.items():    # physically impossible readings are dropouts: treat them as missing
        if k in w:
            w[k] = np.where((w[k] >= lo) & (w[k] <= hi), w[k], np.nan)
    n = i1 - i0
    nan = np.full(n, np.nan)
    missing = sorted(k for k in ("md", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload",
                                 "gas", "mw", "gr") if not np.isfinite(w.get(k, nan)).any())
    hole = _fill(w.get("hole_depth", w.get("md", nan)), 0.0)
    md = _fill(w.get("md", w.get("hole_depth", nan)), 0.0)
    s: dict[str, np.ndarray] = {"t": np.arange(n) * STEP_S, "md": md}
    for k, d in (("rop", 0.0), ("wob", 0.0), ("rpm", 0.0), ("torque", 0.0), ("spp", 0.0), ("flow_in", 0.0),
                 ("flow_out", 100.0), ("pit", 0.0), ("hookload", 0.0), ("gas", 0.0), ("gr", 0.0)):
        s[k] = _fill(w.get(k, nan), d)
    s["rop"] = np.clip(s["rop"], 0, 200)          # sensor glitches (negative ROP/torque, WOB spikes) seen in Volve
    s["wob"] = np.clip(s["wob"], 0, 150)
    s["torque"] = np.clip(s["torque"], 0, None)
    s["mw"] = _fill(w.get("mw", nan), DEFAULT_MW_PPG)
    s["ecd"] = _fill(w["ecd"], 0.0) if np.isfinite(w.get("ecd", nan)).any() else s["mw"] + ECD_MARGIN_PPG

    traj = read_trajectory(rec["trajectories"])
    if len(traj) >= 2:
        s["tvd"] = Trajectory(*np.array(traj).T).tvd_at_md(s["md"])
    else:
        log(f"  {wb}: no WITSML trajectory found; TVD = MD (vertical assumption)")
        s["tvd"] = s["md"].copy()

    # bit size: from the section log ("12 1/4in Section - Time Log") whose time span covers the replay window
    mid = dt.datetime.fromtimestamp(t0 + (i0 + i1) / 2 * STEP_S, dt.timezone.utc)
    covering = [lg["name"] for lg in logs if _span_has(lg, mid)]
    hole_size = _hole_from_names(covering) or _hole_from_names([lg["name"] for lg in rec["logs"]]) or '8-1/2"'
    bit_in = _hole_in(hole_size)
    on_bottom = (s["rop"] > 0.2) | (hole - md < 1.0)
    s["state"] = np.where((s["flow_in"] > 50) & on_bottom, 0.0, 1.0)     # same rule as LiveSession.ingest
    d = dxc_formula(s["rop"], s["rpm"], s["wob"], np.full(n, bit_in), np.maximum(s["mw"], 7.0))
    s["dxc"] = np.where((s["rop"] > 0) & (s["wob"] > 0) & (s["rpm"] > 0), d, np.nan)
    s["dxc"] = _fill(s["dxc"], 1.0)

    episodes = _episodes(wb_incs, t0, i0, i1, md)
    if ddr_root or incs:
        log(f"  {wb}: {len(wb_incs)} operator-coded incidents in the reports, {len(episodes)} inside the window"
            + ("" if wb_incs else " (none coded for this wellbore)"))
    facts = sodir_facts(wb, data_dir, download, log=log)
    start = dt.datetime.fromtimestamp(t0 + i0 * STEP_S, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(t0 + (i1 - 1) * STEP_S, dt.timezone.utc)
    mw_med = round(float(np.median(s["mw"])), 2)
    ecd_med = round(float(np.median(s["ecd"])), 2)
    dlog, intervals, cal = depth_logs(rec, traj, log=log)
    geom = read_geometry(rec.get("geometry", []))
    # TD: Sodir's figure, else the deepest depth any record reached (stream, depth logs, casing)
    td = facts.get("td_md") or max([float(np.nanmax(hole))] + ([float(dlog["md"][-1])] if dlog is not None else [])
                                   + [x["md_bottom"] for x in geom])
    sections = sections_for(geom, intervals, dlog, td, mw_med, ecd_med, hole_size) or \
        [{"idx": 0, "hole": hole_size, "casing": "open hole", "top_md": 0.0, "shoe_md": td, "mw_ppg": mw_med,
          "ecd_ppg": max(ecd_med, mw_med)}]
    well = {"id": wb, "name": wb, "field": facts.get("field") or "VOLVE",
            "lat": facts.get("lat") or VOLVE_WELLHEAD[0], "lon": facts.get("lon") or VOLVE_WELLHEAD[1],
            "position_source": "Sodir FactPages" if facts.get("lat") else "Volve template (approx.)",
            "spud_date": _iso(facts.get("entry")) or start.date().isoformat(), "td_md": td,
            "td_tvd": facts.get("td_tvd") or _tvd_at(traj, td),
            "status": facts.get("purpose") or "", "trajectory": traj,
            "sections": sections, "casing_source": "WITSML wbGeometry" if geom else "none (single open-hole section)",
            "log_intervals": intervals, "ecd_minus_mw": cal, "offset_logs": dlog is not None,
            "window": {"start": start.isoformat(), "end": end.isoformat(), "hours": round(n * STEP_S / 3600, 1),
                       "md_from": round(float(md[0]), 1), "md_to": round(float(md[-1]), 1),
                       "drilling_samples": n_drill},
            "logs": sorted({lg["name"] for lg in logs}), "derived": ["tvd", "state", "dxc"] + missing
            + ([] if np.isfinite(w.get("ecd", nan)).any() else ["ecd"]),
            "episodes": episodes, "attribution": ATTRIBUTION, "source_url": SOURCE_URL}
    out = data_dir / "public" / "volve"
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / f"stream_{safe(wb)}.npz", **{k: np.asarray(s[k], dtype=float) for k in STREAM_KEYS})
    if dlog is not None:
        np.savez_compressed(out / f"logs_{safe(wb)}.npz", **{k: np.asarray(v, dtype=np.float32) for k, v in dlog.items()})
    (out / f"well_{safe(wb)}.json").write_text(json.dumps(well, indent=1), encoding="utf-8")
    log(f"  {wb}: {well['window']['hours']} h window {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC, "
        f"bit {well['window']['md_from']:.0f} -> {well['window']['md_to']:.0f} m MD, {n_drill} drilling samples"
        + (f"; not in the logs (filled): {', '.join(missing)}" if missing else ""))
    return well


def _tvd_at(traj: list[tuple[float, float, float]], md: float) -> float:
    if len(traj) < 2:
        return md
    return float(Trajectory(*np.array(traj).T).tvd_at_md(md))


def _iso(d: str | None) -> str | None:
    """Sodir writes dates as DD.MM.YYYY."""
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


# ---------------------------------------------------------------------------------------------- apply
def _folder(data_dir: Path | None = None) -> Path:
    return (data_dir or config.DATA_DIR) / "public" / "volve"


def imported(data_dir: Path | None = None) -> list[dict]:
    """Every imported Volve wellbore (its well_*.json), with a stream artifact next to it."""
    folder = _folder(data_dir)
    out = []
    for wj in sorted(folder.glob("well_*.json")) if folder.exists() else []:
        w = json.loads(wj.read_text(encoding="utf-8"))
        if (folder / f"stream_{safe(w['id'])}.npz").exists():
            out.append(w)
    return out


def active_wellbore(data_dir: Path | None = None) -> str | None:
    """The wellbore Live Ops replays: public/volve/active.txt, else the one with the most coded incidents."""
    wells = imported(data_dir)
    if not wells:
        return None
    f = _folder(data_dir) / "active.txt"
    chosen = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    ids = [w["id"] for w in wells]
    if chosen in ids:
        return chosen
    return max(wells, key=lambda w: (len(w.get("episodes", [])), w["window"]["drilling_samples"]))["id"]


def set_active(wellbore: str, data_dir: Path | None = None) -> None:
    if wellbore not in [w["id"] for w in imported(data_dir)]:
        raise ValueError(f"{wellbore} has not been imported")
    (_folder(data_dir) / "active.txt").write_text(wellbore, encoding="utf-8")


def artifact(data_dir: Path | None = None) -> tuple[Path, Path] | None:
    """(well json, stream) of the active Volve wellbore, or None when nothing has been imported."""
    wid = active_wellbore(data_dir)
    if wid is None:
        return None
    folder = _folder(data_dir)
    return folder / f"well_{safe(wid)}.json", folder / f"stream_{safe(wid)}.npz"


def ecd_margin(data_dir: Path | None = None) -> float | None:
    """ECD - MW measured while drilling in the Volve logs (calibration.json), for wells that publish only MW."""
    f = _folder(data_dir) / "calibration.json"
    try:
        return float(json.loads(f.read_text(encoding="utf-8"))["ecd_margin_ppg"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def _insert_tops(db, wid: str, tops: dict[str, dict], source: str) -> int:
    db.execute("DELETE FROM tops WHERE well_id=?", (wid,))
    for code, v in tops.items():
        db.insert("tops", {"well_id": wid, "formation": code, "md": round(v["md"], 1), "tvd": round(v["tvd"], 1),
                           "source": source})
    return len(tops)


def apply(db, data_dir: Path | None = None, log=print, ingestor=None) -> str | None:
    """Every imported Volve wellbore becomes a real well: survey, casing sections, picked tops, depth-indexed offset
    logs and (with an ingestor) its daily drilling reports as documents. The active one's stream is installed as
    logs/active_stream.npz, with its real picks as the mud-logger truth and its coded incidents as scenarios."""
    data_dir = data_dir or config.DATA_DIR
    wells = imported(data_dir)
    if not wells:
        return None
    folder = _folder(data_dir)
    active = active_wellbore(data_dir)
    picks = read_picks(folder / "picks.dat") if (folder / "picks.dat").exists() else {}
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    db.execute("UPDATE wells SET is_active=0")
    db.insert("structures", {"id": "VOLVE", "name": "Volve", "lat": wells[0]["lat"], "lon": wells[0]["lon"],
                             "prod_start": 2008})
    n_tops = n_logs = 0
    tops_of: dict[str, dict] = {}
    for w in wells:
        wid = w["id"]
        for table, key in (("wells", "id"), ("surveys", "well_id"), ("sections", "well_id"), ("tops", "well_id")):
            db.execute(f"DELETE FROM {table} WHERE {key}=?", (wid,))
        yr = int(w["spud_date"][:4]) if w.get("spud_date") else None
        db.insert("wells", {"id": wid, "name": wid, "structure_id": "VOLVE", "lat": w["lat"], "lon": w["lon"],
                            "spud_date": w.get("spud_date"), "spud_year": yr, "td_md": w["td_md"],
                            "td_tvd": w["td_tvd"], "status": w.get("status") or "",
                            "traj_type": "deviated (WITSML survey)" if w["trajectory"] else "vertical (assumed)",
                            "target": "", "mud_system": None, "is_active": int(wid == active), "synthetic": 0,
                            "source": "volve"})
        traj = w["trajectory"] or [[0.0, 0.0, 0.0], [w["td_md"], 0.0, 0.0]]
        a = np.array(traj, dtype=float)
        tvd, north, east, _ = minimum_curvature(a[:, 0], a[:, 1], a[:, 2])
        db.executemany("INSERT INTO surveys (well_id, md, inc, azi, tvd, north, east) VALUES (?,?,?,?,?,?,?)",
                       [(wid, float(m), float(i), float(z), float(v), float(nn), float(e))
                        for (m, i, z), v, nn, e in zip(a.tolist(), tvd, north, east)])
        for sec in w["sections"]:
            db.insert("sections", {"well_id": wid, **sec})
        tops = picks_to_tops(picks.get(wid, []))
        tops_of[wid] = tops
        n_tops += _insert_tops(db, wid, tops, "volve picks")
        lp = folder / f"logs_{safe(wid)}.npz"
        if lp.exists():
            z = {k: v.astype(float) for k, v in np.load(lp).items()}
            z["fm"] = _fm_index(tops, z["tvd"])
            np.savez_compressed(logs_dir / config.log_path(wid).name, **{k: v.astype(np.float32) for k, v in z.items()})
            n_logs += 1
    # other public wells in the pick table (e.g. Sodir exploration wells 15/9-11, 15/9-17) that have no tops yet
    volve_ids = {w["id"] for w in wells}
    for wb, entries in picks.items():
        if wb not in volve_ids and db.one("SELECT 1 FROM wells WHERE id=?", (wb,)) and \
                not db.one("SELECT 1 FROM tops WHERE well_id=?", (wb,)):
            n_tops += _insert_tops(db, wb, picks_to_tops(entries), "volve picks")

    w = next(x for x in wells if x["id"] == active)
    z = {k: v.astype(float) for k, v in np.load(folder / f"stream_{safe(active)}.npz").items()}
    truth = tops_of.get(active) or {}
    if truth:
        z["fm"] = _fm_index(truth, z["tvd"])       # real picks: the replay's mud-logger picks and the DTW evaluation
    np.savez_compressed(logs_dir / "active_stream.npz", **z)
    db.kv_set("active_well", active)
    db.kv_set("active_episodes", w.get("episodes", []))
    db.kv_set("active_truth_tops", {c: round(v["tvd"], 1) for c, v in truth.items()})
    db.kv_set("stream_source", {"source": "Volve (Equinor)", "wellbore": active, "window": w["window"],
                                "logs": w["logs"], "derived": w["derived"], "attribution": w["attribution"],
                                "url": w["source_url"], "position": w["position_source"],
                                "incidents": len(w.get("episodes", [])), "tops": "Volve well picks" if truth
                                else "predicted from offsets", "casing": w.get("casing_source"),
                                "wells": sorted(volve_ids)})
    n_docs = ingest_reports(db, ingestor, folder / "ddr", log=log) if ingestor is not None else 0
    db.commit()
    log(f"  Volve: {len(wells)} wellbores ({n_logs} with offset logs), {n_tops} picked tops, {n_docs} report "
        f"documents; active well {active}")
    return active


def ingest_reports(db, ingestor, ddr_dir: Path, log=print) -> int:
    """Each known wellbore's daily drilling reports as one cited document (a page per report day), read by the NLP
    pipeline like any DDR. Done once per wellbore (kv 'volve_reports')."""
    if not ddr_dir.exists():
        return 0
    from ..ingest.witsml import reports_to_pages
    from ..validate.volve import load_reports
    done = set(db.kv_get("volve_reports", []) or [])
    by: dict[str, list[dict]] = {}
    for rs in load_reports(ddr_dir).values():
        for r in rs:
            wb = wellbore_name(r.get("wellbore") or "") or wellbore_name(r["well"])
            if wb and wb not in done and db.one("SELECT 1 FROM wells WHERE id=?", (wb,)):
                by.setdefault(wb, []).append(r)
    n_ev = 0
    for wb, rs in sorted(by.items()):
        rs.sort(key=lambda r: r["date"])
        res = ingestor._ingest_pages(Path(f"volve_{safe(wb)}_daily_drilling_reports.xml"), reports_to_pages(rs), "DDR",
                                     f"{wb} daily drilling reports (Equinor Volve)", wb, "volve")
        n_ev += len(res["events"])
        done.add(wb)
    db.kv_set("volve_reports", sorted(done))
    if by:
        log(f"  {sum(len(v) for v in by.values())} Volve daily drilling reports of {len(by)} wellbores -> {n_ev} events")
    return len(by)


def summary(inv: dict[str, dict], incs: dict[str, list[dict]] | None = None) -> list[dict]:
    """Ranked wellbores for the dashboard and --list (header statistics plus coded incidents)."""
    incs = incs or {}
    return [{"wellbore": r["wellbore"], "drilling_logs": r["usable_logs"], "files": r["usable_files"],
             "log_days": r["span_days"], "trajectory": bool(r["trajectories"]),
             "incidents": len(incs.get(r["wellbore"], []))} for r in rank(inv)]


def import_stream(root: Path | str, wellbore: str | None = None, hours: float = 12.0, list_only: bool = False,
                  ddr_root: Path | str | None = None, log=print) -> dict:
    """CLI entry: scan, (list), convert and apply into the current (norway) knowledge base."""
    inv = scan(root, log=log)
    incs = incidents(ddr_root) if ddr_root else {}
    rows = summary(inv, incs)
    for r in rows:
        log(f"  {r['wellbore']:<14} {r['drilling_logs']:>3} drilling time logs, {r['files']:>5} files, "
            f"{r['log_days']:>7.1f} log-days, trajectory {'yes' if r['trajectory'] else 'no '}"
            + (f", {r['incidents']} coded incidents" if ddr_root else ""))
    if list_only:
        return {"wellbores": rows}
    well = convert(root, wellbore, hours, log=log, inventory=inv, download=True, ddr_root=ddr_root, incs=incs)
    from ..db import DB
    db = DB()
    try:
        apply(db, config.DATA_DIR, log=log)
    finally:
        db.close()
    return {"wellbore": well["id"], "window": well["window"], "derived": well["derived"],
            "scenarios": [f"{e['id']} {e['hazard']} {e['label']}" for e in well["episodes"]]}


def import_all(root: Path | str, ddr_root: Path | str | None = None, picks_path: Path | str | None = None,
               active: str | None = None, hours: float = 12.0, data_dir: Path | None = None, log=print,
               download: bool = True) -> dict:
    """Every usable wellbore of the export, plus the formation picks and daily drilling reports (copied into
    DATA_DIR/public/volve so rebuilds work offline) and the measured ECD margin; then applied."""
    data_dir = data_dir or config.DATA_DIR
    folder = _folder(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    inv = scan(root, log=log)
    incs = incidents(ddr_root) if ddr_root else {}
    done: list[dict] = []
    for r in rank(inv):
        try:
            done.append(convert(root, r["wellbore"], hours, data_dir, log=log, inventory=inv, download=download,
                                incs=incs))
        except SystemExit as e:
            log(f"  skipped {r['wellbore']}: {e}")
    if not done:
        raise SystemExit(f"No usable wellbore under {root}")
    if picks_path:
        shutil.copy(picks_path, folder / "picks.dat")
    if ddr_root:
        dst = folder / "ddr"
        dst.mkdir(exist_ok=True)
        for f in Path(ddr_root).rglob("*.xml"):
            shutil.copy(f, dst / f.name)
    pooled: dict[str, list[tuple[float, int]]] = {}
    for w in done:
        for hole, v in (w.get("ecd_minus_mw") or {}).items():
            pooled.setdefault(hole, []).append((v["median"], v["n"]))
    rows = [x for v in pooled.values() for x in v]
    if rows:
        n = sum(k for _, k in rows)
        cal = {"ecd_margin_ppg": round(sum(m * k for m, k in rows) / n, 2), "samples": n,
               "by_hole": {h: round(sum(m * k for m, k in v) / sum(k for _, k in v), 2) for h, v in pooled.items()},
               "source": "median ECD - MW while drilling in the Equinor Volve depth logs (sample-weighted over wells)"}
        (folder / "calibration.json").write_text(json.dumps(cal, indent=1), encoding="utf-8")
        log(f"  measured ECD - MW while drilling: {cal['ecd_margin_ppg']} ppg over {n} samples")
    if active:
        set_active(wellbore_name(active) or active, data_dir)
    elif not (folder / "active.txt").exists():
        set_active(active_wellbore(data_dir), data_dir)
    from ..db import DB
    db = DB(data_dir / config.DB_NAME)
    try:
        ing = None
        clf_path = data_dir / "models" / "sentence_clf.joblib"
        if clf_path.exists():
            from ..ingest.nlp import SentenceClassifier
            from ..ingest.pipeline import Ingestor
            ing = Ingestor(db, SentenceClassifier.load(clf_path))
        wid = apply(db, data_dir, log=log, ingestor=ing)
    finally:
        db.close()
    return {"wellbores": [w["id"] for w in done], "active": wid,
            "scenarios": sum(len(w.get("episodes", [])) for w in done),
            "ecd_margin_ppg": (json.loads((folder / "calibration.json").read_text()) if (folder / "calibration.json").exists()
                               else {}).get("ecd_margin_ppg")}
