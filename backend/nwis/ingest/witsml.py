"""WITSML 1.4.x drillReport importer (compatible with the public Equinor Volve DDR XML).

Each <drillReport> is rendered into a DDR-like text page and pushed through the
same NLP pipeline as PDFs, so XML and PDF knowledge land in one schema. The
activity <proprietaryCode>/<state> (e.g. "interruption -- lost circulation",
state="fail") are kept in the text because they are strong event signals.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .pdf import PageText

UOM_TO_PPG = {"g/cm3": 8.345, "sg": 8.345, "kg/m3": 0.008345, "ppg": 1.0, "lbm/galus": 1.0}


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _t(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else None


def parse_drill_reports(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    _strip_ns(root)
    reports = [root] if root.tag == "drillReport" else root.findall(".//drillReport")
    out = []
    for r in reports:
        well = _t(r, "nameWell") or r.get("uidWell") or "UNKNOWN"
        date = (_t(r, "dTimStart") or _t(r, "createDate") or "")[:10]
        mw_ppg = None
        dens = r.find("fluid/density")
        if dens is not None and dens.text:
            mw_ppg = round(float(dens.text) * UOM_TO_PPG.get((dens.get("uom") or "g/cm3").lower(), 8.345), 2)
        acts = []
        for a in r.findall("activity"):
            md_el = a.find("md")
            md = float(md_el.text) if md_el is not None and md_el.text else None
            uom = (md_el.get("uom") if md_el is not None else "m") or "m"
            if md is not None and uom.lower() in ("ft", "ft_us"):
                md *= 0.3048
            acts.append({"start": (_t(a, "dTimStart") or "")[11:16], "end": (_t(a, "dTimEnd") or "")[11:16],
                         "md": md, "code": _t(a, "proprietaryCode") or "", "state": _t(a, "state") or "",
                         "comments": _t(a, "comments") or ""})
        out.append({"well": well, "date": date, "mw_ppg": mw_ppg, "md": _t(r, "statusInfo/md"),
                    "summary": _t(r, "statusInfo/sum24Hr"), "activities": acts})
    return out


def reports_to_pages(reports: list[dict]) -> list[PageText]:
    pages = []
    for i, r in enumerate(reports):
        lines = [f"DAILY DRILLING REPORT    Report No: {i + 1}    Date: {r['date']}", f"Well: {r['well']}"]
        if r["mw_ppg"]:
            lines.append(f"MW: {r['mw_ppg']:.2f} ppg")
        lines += ["", "TIME LOG"]
        for a in r["activities"]:
            code = "NPT" if a["state"].lower() == "fail" or "interruption" in a["code"].lower() else "OPS"
            depth = f" @ {a['md']:.0f} m" if a["md"] is not None else ""
            hint = f" [{a['code']}]" if a["code"] else ""
            comment = a["comments"].rstrip(".")
            lines.append(f"{a['start'] or '00:00'}-{a['end'] or '00:00'}  {code:<7} {comment}{depth}.{hint}")
        pages.append(PageText(i + 1, "\n".join(lines), False, None))
    return pages


def load_witsml_pages(path: Path) -> tuple[list[PageText], list[dict]]:
    reports = parse_drill_reports(Path(path).read_bytes())
    return reports_to_pages(reports), reports
