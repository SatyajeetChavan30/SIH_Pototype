"""Real rig-sensor data for Live Ops: Equinor's Volve field (block 15/9, Norwegian North Sea).

Sodir FactPages publish well records but no time series, so the real-data (norway) knowledge base has no stream.
Volve's public WITSML 1.4.1.1 export ("WITSML Realtime drilling data" folder, one sub-folder per wellbore) holds the
real surface-sensor logs of the Volve development wells. This module turns one wellbore of it into the replay file
Live Ops and the rig simulator already use (logs/active_stream.npz, see realtime/engine.py CHANNELS):

  scan(root)     header-only inventory: wellbores, their time-indexed drilling logs and trajectories
  convert(root)  parse, merge chunk files, convert units, resample to 30 s, pick the most drilling-active window,
                 derive the channels a rig does not send (TVD from the WITSML survey, rig state, d-exponent)
                 and store a compact artifact in DATA_DIR/public/volve (stream_*.npz + well_*.json)
  apply(db)      add that wellbore to the knowledge base as the active well and install the stream

The artifact is re-applied by every North Sea rebuild (public/sodir.py), so the multi-GB export is read only once.
Getting the data needs a (free) Databricks account: https://www.equinor.com/energy/volve-data-sharing ->
Databricks Marketplace "Volve Data Village". Licence: Equinor Open Data Licence (CC BY 4.0 based, no sale of the data);
the attribution below is shown wherever the stream is.

Honest limits: the logs carry no labels. With the daily drilling reports (ddr_root), the operator's coded incidents
become timed scenarios V1, V2... (operator codes, not hand-checked truth); without them there is nothing to jump to
or score against. Formation tops of the Volve wellbore are predicted from nearby Sodir offsets, not picked.
Tested on generated Volve-format samples; the real export may still need mnemonic mappings.
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
        rec = out.setdefault(wb, {"wellbore": wb, "logs": [], "trajectories": []})
        if "<trajectoryStation" in head or re.search(r"<(?:\w+:)?trajectorys?\b", head):
            rec["trajectories"].append(str(f))
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
def _binned(files: list[str], log=print) -> tuple[float, dict[str, np.ndarray]]:
    """Every channel resampled onto one STEP_S grid (bin means; NaN where a channel has no sample).

    Real Volve wellbores hold around a gigabyte of XML (months of 1-10 s samples), so each chunk file is reduced
    to 30 s bin sums as soon as it is parsed; only those (a few percent of the raw rows) are kept in memory."""
    acc: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for i, f in enumerate(files):
        try:
            rows = parse_log(Path(f).read_bytes())
        except Exception as e:  # noqa: BLE001 - one malformed chunk must not stop the import
            log(f"  skipped {Path(f).name}: {e}")
            continue
        cols: dict[str, tuple[list[float], list[float]]] = {}
        for r in rows:
            t = r.get("t_epoch")
            if t is None:
                continue
            for k, v in r.items():
                if k != "t_epoch":
                    ts, vs = cols.setdefault(k, ([], []))
                    ts.append(t)
                    vs.append(v)
        for k, (ts, vs) in cols.items():
            b = np.floor(np.asarray(ts) / STEP_S).astype(np.int64)
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
    return b0 * STEP_S, grid


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
    hole =_fill(g.get("hole_depth", depth), 0.0)
    step = np.diff(hole, prepend=hole[0])
    new_hole = np.where(drilling & (step > 0) & (step < MAX_STEP_M), step, 0.0)
    c = np.concatenate([[0], np.cumsum(drilling)])
    m = np.concatenate([[0.0], np.cumsum(new_hole)])
    score = (m[n_win:] - m[:-n_win]) + 0.01 * (c[n_win:] - c[:-n_win])
    lead, tail = int(LEAD_S // STEP_S), int(TAIL_S // STEP_S)
    bonus = np.zeros(len(score) + 1)
    big = 10.0 * (float(np.max(score)) + n_win)
    for k in incident_idx or []:
        lo, hi = max(k + tail - n_win + 1, 0), min(k - lead, len(score) - 1)   # window starts that contain k
        if lo <= hi:
            bonus[lo] += big
            bonus[hi + 1] -= big
    score += np.cumsum(bonus)[:-1]
    i = int(np.argmax(score))
    return i, i + n_win, int(c[i + n_win] - c[i])


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
    td = facts.get("td_md") or float(np.nanmax(hole))
    well = {"id": wb, "name": wb, "field": facts.get("field") or "VOLVE",
            "lat": facts.get("lat") or VOLVE_WELLHEAD[0], "lon": facts.get("lon") or VOLVE_WELLHEAD[1],
            "position_source": "Sodir FactPages" if facts.get("lat") else "Volve template (approx.)",
            "spud_date": _iso(facts.get("entry")) or start.date().isoformat(), "td_md": td,
            "td_tvd": facts.get("td_tvd") or _tvd_at(traj, td),
            "status": facts.get("purpose") or "", "trajectory": traj,
            "sections": [{"idx": 0, "hole": hole_size, "casing": "open hole (Volve stream window)", "top_md": 0.0,
                          "shoe_md": td, "mw_ppg": mw_med, "ecd_ppg": max(ecd_med, mw_med)}],
            "window": {"start": start.isoformat(), "end": end.isoformat(), "hours": round(n * STEP_S / 3600, 1),
                       "md_from": round(float(md[0]), 1), "md_to": round(float(md[-1]), 1),
                       "drilling_samples": n_drill},
            "logs": sorted({lg["name"] for lg in logs}), "derived": ["tvd", "state", "dxc"] + missing
            + ([] if np.isfinite(w.get("ecd", nan)).any() else ["ecd"]),
            "episodes": episodes, "attribution": ATTRIBUTION, "source_url": SOURCE_URL}
    out = data_dir / "public" / "volve"
    if out.exists():
        for p in list(out.glob("stream_*.npz")) + list(out.glob("well_*.json")):
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / f"stream_{safe(wb)}.npz", **{k: np.asarray(s[k], dtype=float) for k in STREAM_KEYS})
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
def artifact(data_dir: Path | None = None) -> tuple[Path, Path] | None:
    folder = (data_dir or config.DATA_DIR) / "public" / "volve"
    wells = sorted(folder.glob("well_*.json")) if folder.exists() else []
    if not wells:
        return None
    stream = folder / wells[0].name.replace("well_", "stream_", 1).replace(".json", ".npz")
    return (wells[0], stream) if stream.exists() else None


def apply(db, data_dir: Path | None = None, log=print) -> str | None:
    """Make the stored Volve wellbore the active well and install its stream as logs/active_stream.npz."""
    data_dir = data_dir or config.DATA_DIR
    art = artifact(data_dir)
    if art is None:
        return None
    wj, stream = art
    w = json.loads(wj.read_text(encoding="utf-8"))
    wid = w["id"]
    for table, key in (("wells", "id"), ("surveys", "well_id"), ("sections", "well_id"), ("tops", "well_id")):
        db.execute(f"DELETE FROM {table} WHERE {key}=?", (wid,))
    db.execute("UPDATE wells SET is_active=0")
    db.insert("structures", {"id": "VOLVE", "name": "Volve", "lat": w["lat"], "lon": w["lon"], "prod_start": 2008})
    yr = int(w["spud_date"][:4]) if w.get("spud_date") else None
    db.insert("wells", {"id": wid, "name": wid, "structure_id": "VOLVE", "lat": w["lat"], "lon": w["lon"],
                        "spud_date": w.get("spud_date"), "spud_year": yr, "td_md": w["td_md"], "td_tvd": w["td_tvd"],
                        "status": w.get("status") or "", "traj_type": "deviated (WITSML survey)" if w["trajectory"]
                        else "vertical (assumed)", "target": "", "mud_system": None, "is_active": 1, "synthetic": 0,
                        "source": "volve"})
    traj = w["trajectory"] or [[0.0, 0.0, 0.0], [w["td_md"], 0.0, 0.0]]
    a = np.array(traj, dtype=float)
    tvd, north, east, _ = minimum_curvature(a[:, 0], a[:, 1], a[:, 2])
    db.executemany("INSERT INTO surveys (well_id, md, inc, azi, tvd, north, east) VALUES (?,?,?,?,?,?,?)",
                   [(wid, float(m), float(i), float(z), float(v), float(nn), float(e))
                    for (m, i, z), v, nn, e in zip(a.tolist(), tvd, north, east)])
    for sec in w["sections"]:
        db.insert("sections", {"well_id": wid, **sec})
    db.kv_set("active_well", wid)
    db.kv_set("active_episodes", w.get("episodes", []))
    db.kv_set("active_truth_tops", {})
    db.kv_set("stream_source", {"source": "Volve (Equinor)", "wellbore": wid, "window": w["window"],
                                "logs": w["logs"], "derived": w["derived"], "attribution": w["attribution"],
                                "url": w["source_url"], "position": w["position_source"],
                                "incidents": len(w.get("episodes", []))})
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(stream, logs_dir / "active_stream.npz")
    db.commit()
    log(f"  Volve stream installed: active well {wid}")
    return wid


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
