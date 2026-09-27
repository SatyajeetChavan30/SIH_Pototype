"""WITSML 1.4.1 time-indexed <log> parsing and a store poller (WMLS_GetFromStore over SOAP).

Many RTOCs expose rig data through a WITSML server rather than a raw WITS-0 feed. A log object carries
curve definitions (<logCurveInfo> with mnemonics and units) and rows (<logData><data>) as comma-separated
values. Mnemonics differ between service companies, so the mapping is configurable (STRATASENSE_WITSML_MAP JSON).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from html import escape

# Common real-time mnemonics (several vendor aliases per channel). When a log carries two aliases of the same
# channel, the one listed first here wins (e.g. mud weight in, MWTI, before mud density out, MDOA).
# The Volve (Equinor) aliases follow the curve lists of its WITSML 1.4.1.1 real-time export.
DEFAULT_MNEMONICS = {
    "TIME": "time", "DATETIME": "time",
    "DMEA": "hole_depth", "DEPT": "hole_depth",
    "DBTM": "md", "BITDEP": "md", "BDEP": "md", "DBTV": "tvd",
    "ROPA": "rop", "ROP": "rop",
    "HKLA": "hookload", "HKLD": "hookload",
    "SWOB": "wob", "WOBA": "wob",
    "TQA": "torque", "TORQ": "torque",
    "RPMA": "rpm", "RPM": "rpm",
    "SPPA": "spp", "SPP": "spp",
    "TVA": "pit", "TVT": "pit", "PVT": "pit",
    "MFOP": "flow_out", "FLOWOUTP": "flow_out",
    "TFLO": "flow_in", "MFIA": "flow_in",
    "MDIA": "mw", "MWIN": "mw", "MWTI": "mw", "MDOA": "mw",
    "ECDT": "ecd", "ECD": "ecd", "ECD_MWD": "ecd", "ECD_ARC_RT": "ecd", "ECD_ECO_RT": "ecd",
    "GASA": "gas", "TGAS": "gas",
    "GRA": "gr", "GR": "gr", "GRM1": "gr", "ARC_GR_RT": "gr", "GRMA_ECO_RT": "gr",
}
FT_TO_M = 0.3048
NULL_VALUES = (-999.25, -9999.0)   # WITSML's customary absent-value markers, used when a log declares none

# Unit conversion into the engine's units (m, m/hr, klbf, klbs, kft.lbf, rpm, psi, gpm, bbl, ppg, %).
# Factors: 1 kkgf (metric tonne-force) = 2.20462 klbf; 1 kN.m = 0.737562 kft.lbf; 1 kPa = 0.145038 psi;
# 1 bar = 14.5038 psi; 1 L/min = 0.264172 US gpm; 1 m3 = 6.28981 bbl; 1 g/cm3 = 8.3454 ppg (NIST SP 811).
_KLB = {"kkgf": 2.20462, "tonf": 2.20462, "t": 2.20462, "kn": 0.224809, "n": 0.000224809, "klbf": 1.0, "klb": 1.0}
UNIT_FACTORS: dict[str, dict[str, float]] = {
    "md": {"ft": FT_TO_M, "ft_us": FT_TO_M}, "tvd": {"ft": FT_TO_M, "ft_us": FT_TO_M},
    "hole_depth": {"ft": FT_TO_M, "ft_us": FT_TO_M},
    "rop": {"m/s": 3600.0, "m/min": 60.0, "ft/h": FT_TO_M, "ft/hr": FT_TO_M},
    "wob": _KLB, "hookload": _KLB,
    "torque": {"kn.m": 0.737562, "knm": 0.737562, "n.m": 0.000737562, "kft.lbf": 1.0},
    "rpm": {"c/s": 60.0, "rev/s": 60.0, "rad/s": 9.54930},
    "spp": {"kpa": 0.145038, "bar": 14.5038, "mpa": 145.038, "pa": 0.000145038},
    "flow_in": {"l/min": 0.264172, "m3/min": 264.172, "m3/s": 15850.3, "l/s": 15.8503},
    "pit": {"m3": 6.28981},
    "mw": {"g/cm3": 8.3454, "kg/m3": 0.0083454, "sg": 8.3454},
    "ecd": {"g/cm3": 8.3454, "kg/m3": 0.0083454, "sg": 8.3454},
}


def to_engine_units(channel: str, value: float, unit: str | None) -> float:
    """Convert one value into the unit the detectors expect; unknown or already-matching units pass through."""
    f = UNIT_FACTORS.get(channel, {}).get((unit or "").strip().lower())
    return value * f if f is not None else value


def mnemonic_map() -> dict[str, str]:
    m = dict(DEFAULT_MNEMONICS)
    path = os.environ.get("STRATASENSE_WITSML_MAP")
    if path:
        with open(path, encoding="utf-8") as f:
            m.update({str(k).upper(): str(v) for k, v in json.load(f).items()})
    return m


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _time(v: str) -> float | None:
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse_log(xml: str | bytes, mapping: dict[str, str] | None = None) -> list[dict]:
    """Rows of a WITSML 1.4.1 <log> as channel dicts in engine units (depths in metres, time as epoch seconds).

    Declared <nullValue>s (log-wide or per curve, e.g. Volve's -999.25) and empty fields are treated as missing.
    """
    mapping = mapping or mnemonic_map()
    rank = {m: i for i, m in enumerate(mapping)}
    root = ET.fromstring(xml)
    _strip_ns(root)
    rows: list[dict] = []
    for log in ([root] if root.tag == "log" else root.findall(".//log")):
        curves = log.findall("logCurveInfo")
        units = {(c.findtext("mnemonic") or "").strip().upper(): (c.findtext("unit") or "").strip().lower() for c in curves}
        log_null = _num(log.findtext("nullValue"))
        nulls = {(c.findtext("mnemonic") or "").strip().upper(): _num(c.findtext("nullValue")) for c in curves}
        ld = log.find("logData")
        if ld is None:
            continue
        mnems = [m.strip().upper() for m in (ld.findtext("mnemonicList") or ",".join(units)).split(",")]
        for d in ld.findall("data"):
            vals = [v.strip() for v in (d.text or "").split(",")]
            row: dict = {}
            src: dict[str, str] = {}      # channel -> mnemonic it came from (alias precedence)
            for m, v in zip(mnems, vals):
                key = mapping.get(m)
                if key is None or v == "":
                    continue
                if key == "time":
                    t = _time(v)
                    if t is not None:
                        row["t_epoch"] = t
                    continue
                try:
                    x = float(v)
                except ValueError:
                    continue
                null = nulls.get(m) if nulls.get(m) is not None else log_null
                if (null is not None and x == null) or x in NULL_VALUES:
                    continue
                if key in src and rank.get(src[key], 0) < rank.get(m, 0):
                    continue
                row[key] = to_engine_units(key, x, units.get(m))
                src[key] = m
            if row:
                rows.append(row)
    return rows


QUERY = """<logs xmlns="http://www.witsml.org/schemas/1series" version="1.4.1.1">
<log uidWell="{uw}" uidWellbore="{uwb}" uid="{ul}"><startDateTimeIndex>{start}</startDateTimeIndex>
<logCurveInfo><mnemonic/><unit/></logCurveInfo><logData><mnemonicList/><data/></logData></log></logs>"""

ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<WMLS_GetFromStore xmlns="http://www.witsml.org/message/120"><WMLtypeIn>log</WMLtypeIn>
<QueryIn>{query}</QueryIn><OptionsIn>returnElements=data-only</OptionsIn><CapabilitiesIn/></WMLS_GetFromStore>
</soap:Body></soap:Envelope>"""


class WitsmlLogPoller:
    """Asks a WITSML 1.4.1 store for log rows newer than the last one seen."""

    def __init__(self, url: str, uid_well: str, uid_wellbore: str, uid_log: str, user: str | None = None,
                 password: str | None = None, timeout: float = 20.0):
        self.url, self.uids, self.timeout = url, (uid_well, uid_wellbore, uid_log), timeout
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode() if user else None
        self.last = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)

    def poll(self) -> list[dict]:
        q = QUERY.format(uw=self.uids[0], uwb=self.uids[1], ul=self.uids[2], start=self.last.isoformat())
        body = ENVELOPE.format(query=escape(q)).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "text/xml; charset=utf-8", "SOAPAction": "http://www.witsml.org/action/120/Store.WMLS_GetFromStore"})
        if self.auth:
            req.add_header("Authorization", f"Basic {self.auth}")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            env = ET.fromstring(r.read())
        _strip_ns(env)
        xml_out = env.findtext(".//XMLout") or ""
        rows = parse_log(xml_out) if xml_out.strip() else []
        ts = [r["t_epoch"] for r in rows if "t_epoch" in r]
        if ts:
            self.last = dt.datetime.fromtimestamp(max(ts), dt.timezone.utc) + dt.timedelta(milliseconds=1)
        return rows
