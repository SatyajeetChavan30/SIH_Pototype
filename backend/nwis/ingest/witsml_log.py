"""WITSML 1.4.1 time-indexed <log> parsing and a store poller (WMLS_GetFromStore over SOAP).

Many RTOCs expose rig data through a WITSML server rather than a raw WITS-0 feed. A log object carries
curve definitions (<logCurveInfo> with mnemonics and units) and rows (<logData><data>) as comma-separated
values. Mnemonics differ between service companies, so the mapping is configurable (NWIS_WITSML_MAP JSON).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from html import escape

# Common real-time mnemonics (several vendor aliases per channel)
DEFAULT_MNEMONICS = {
    "TIME": "time", "DATETIME": "time",
    "DMEA": "hole_depth", "DEPT": "hole_depth",
    "DBTM": "md", "BITDEP": "md", "DBTV": "tvd",
    "ROPA": "rop", "ROP": "rop",
    "HKLA": "hookload", "HKLD": "hookload",
    "SWOB": "wob", "WOBA": "wob",
    "TQA": "torque", "TORQ": "torque",
    "RPMA": "rpm", "RPM": "rpm",
    "SPPA": "spp", "SPP": "spp",
    "TVA": "pit", "TVT": "pit", "PVT": "pit",
    "MFOP": "flow_out", "FLOWOUTP": "flow_out",
    "TFLO": "flow_in", "MFIA": "flow_in",
    "MDIA": "mw", "MWIN": "mw",
    "ECDT": "ecd", "ECD": "ecd",
    "GASA": "gas", "TGAS": "gas",
    "GRA": "gr", "GR": "gr",
}
FT_TO_M = 0.3048


def mnemonic_map() -> dict[str, str]:
    m = dict(DEFAULT_MNEMONICS)
    path = os.environ.get("NWIS_WITSML_MAP")
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


def parse_log(xml: str | bytes, mapping: dict[str, str] | None = None) -> list[dict]:
    """Rows of a WITSML 1.4.1 <log> as channel dicts (depths converted to metres, time to epoch seconds)."""
    mapping = mapping or mnemonic_map()
    root = ET.fromstring(xml)
    _strip_ns(root)
    rows: list[dict] = []
    for log in ([root] if root.tag == "log" else root.findall(".//log")):
        curves = log.findall("logCurveInfo")
        units = {(c.findtext("mnemonic") or "").strip().upper(): (c.findtext("unit") or "").strip().lower() for c in curves}
        ld = log.find("logData")
        if ld is None:
            continue
        mnems = [m.strip().upper() for m in (ld.findtext("mnemonicList") or ",".join(units)).split(",")]
        for d in ld.findall("data"):
            vals = [v.strip() for v in (d.text or "").split(",")]
            row: dict = {}
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
                if key in ("md", "tvd", "hole_depth") and units.get(m) in ("ft", "ft_us"):
                    x *= FT_TO_M
                row[key] = x
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
