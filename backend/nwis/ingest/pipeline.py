"""End-to-end document ingestion: PDF -> pages (text/OCR) -> records -> sentences ->
structured events / lessons / formation tops with span-level citations ->
consolidation into the knowledge base (+ human review queue for low confidence).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import config
from ..db import DB
from ..domain.ontology import FORMATION_ORDER, MITIGATION_BY_CODE
from ..geo import Trajectory
from .nlp import (RE_DATE, RE_TIMELOG, RE_WELL, SentenceClassifier, SentenceInfo, analyse_sentence, detect_formation,
                  find_hazards, ocr_fix_numbers, parse_depths, parse_gas, parse_mw, parse_npt, parse_overpull,
                  parse_rate, parse_volume, repair_ocr_spacing, split_sentences)
from .pdf import PageText, extract_pages

RIBBON_EVENT_HAZARDS = ("LOSS", "KICK", "STUCK", "TIGHT", "INSTAB", "TORQUE", "CEMENT", "FISH")
MERGE_TOL_M = 40.0


# ---------------------------------------------------------------------------
# Well context used to fill depth/formation gaps
# ---------------------------------------------------------------------------
@dataclass
class WellCtx:
    id: str
    tops_md: dict[str, float]
    tops_tvd: dict[str, float]
    traj: Trajectory | None
    sections: list[dict]

    def formation_at_md(self, md: float) -> str | None:
        cur = None
        for code in FORMATION_ORDER:
            if code in self.tops_md and self.tops_md[code] <= md + 1e-6:
                cur = code
        return cur

    def tvd_at(self, md: float) -> float:
        return float(self.traj.tvd_at_md(md)) if self.traj is not None else md

    def section_mw(self, md: float) -> tuple[float | None, float | None]:
        for s in self.sections:
            if s["top_md"] <= md <= s["shoe_md"] + 1e-6:
                return s["mw_ppg"], s["ecd_ppg"]
        return (self.sections[-1]["mw_ppg"], self.sections[-1]["ecd_ppg"]) if self.sections else (None, None)


def load_well_ctx(db: DB, well_id: str) -> WellCtx | None:
    w = db.one("SELECT id, lat, lon FROM wells WHERE id=?", (well_id,))
    if not w:
        return None
    tops = db.query("SELECT formation, md, tvd FROM tops WHERE well_id=?", (well_id,))
    sv = db.query("SELECT md, inc, azi FROM surveys WHERE well_id=? ORDER BY md", (well_id,))
    secs = db.query("SELECT * FROM sections WHERE well_id=? ORDER BY idx", (well_id,))
    traj = Trajectory([s["md"] for s in sv], [s["inc"] for s in sv], [s["azi"] for s in sv]) if len(sv) > 1 else None
    tops_md = {t["formation"]: t["md"] for t in tops}
    tops_tvd = {t["formation"]: t["tvd"] for t in tops}
    if not tops and w["lat"] is not None:
        # well still drilling: use offset-interpolated (IDW) tops so depth -> formation works for its new DDRs
        from ..geo import haversine_km
        rows = db.query("SELECT t.formation, t.tvd, w.lat, w.lon FROM tops t JOIN wells w ON w.id=t.well_id "
                        "WHERE w.lat IS NOT NULL AND w.id<>?", (well_id,))
        by_fm: dict[str, list] = {}
        for r in rows:
            d = float(haversine_km(w["lat"], w["lon"], r["lat"], r["lon"]))
            if d <= 10:
                by_fm.setdefault(r["formation"], []).append((r["tvd"], 1 / (d * d + 0.25)))
        for code, v in by_fm.items():
            z = sum(a * b for a, b in v) / sum(b for _, b in v)
            tops_tvd[code] = z
            tops_md[code] = float(traj.md_at_tvd(z)) if traj is not None else z
    return WellCtx(well_id, tops_md, tops_tvd, traj, secs)


def typical_thickness(db: DB) -> dict[str, float]:
    cached = getattr(typical_thickness, "_cache", None)
    if cached and cached[0] == db.path:
        return cached[1]
    rows = db.query("SELECT well_id, formation, tvd FROM tops")
    by_well: dict[str, dict[str, float]] = {}
    for r in rows:
        by_well.setdefault(r["well_id"], {})[r["formation"]] = r["tvd"]
    th: dict[str, list[float]] = {}
    for tops in by_well.values():
        for i, c in enumerate(FORMATION_ORDER[:-1]):
            n = FORMATION_ORDER[i + 1]
            if c in tops and n in tops:
                th.setdefault(c, []).append(tops[n] - tops[c])
    out = {c: float(np.median(v)) for c, v in th.items()}
    for c in FORMATION_ORDER:
        out.setdefault(c, 350.0)
    typical_thickness._cache = (db.path, out)
    return out


def rel_in_formation(tops_tvd: dict[str, float], code: str, tvd: float, thick: dict[str, float]) -> float | None:
    if code not in tops_tvd:
        return None
    i = FORMATION_ORDER.index(code)
    nxt = FORMATION_ORDER[i + 1] if i + 1 < len(FORMATION_ORDER) else None
    base = tops_tvd.get(nxt) if nxt else None
    if base is None:
        base = tops_tvd[code] + thick.get(code, 350.0)
    return float(np.clip((tvd - tops_tvd[code]) / max(base - tops_tvd[code], 1.0), 0, 1))


# ---------------------------------------------------------------------------
# Extraction data model
# ---------------------------------------------------------------------------
@dataclass
class XEvent:
    well_id: str | None
    hazard: str
    subtype: str
    md: float | None
    depth_source: str
    formation: str | None
    formation_source: str
    severity: str
    rate_bbl_hr: float | None = None
    volume_bbl: float | None = None
    npt_hours: float | None = None
    mw_ppg: float | None = None
    ecd_ppg: float | None = None
    date: str | None = None
    confidence: float = 0.0
    actions: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    summary: str = ""
    extra: dict = field(default_factory=dict)
    tvd: float | None = None
    rel: float | None = None
    cause: str | None = None

    @property
    def resolved(self) -> bool | None:
        if not self.actions:
            return None
        return any(a.get("success") for a in self.actions)


@dataclass
class DocExtraction:
    kind: str
    well_id: str | None
    pages: list[dict]
    sentences: list[SentenceInfo]
    events: list[XEvent]
    lessons: list[dict]
    tops: list[dict]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Record segmentation
# ---------------------------------------------------------------------------
# \s* between words: OCR often drops the spaces in bold headings ('2.CASINGPOLICY', '3.MUDPROGRAM')
HEADINGS = {
    "tops": re.compile(r"formation\s*tops", re.I),
    "complications": re.compile(r"(drilling\s*)?complications|problems\s*encountered|npt\s*events", re.I),
    "lessons": re.compile(r"lessons\s*learn|recommendations", re.I),
    "casing": re.compile(r"casing\s*policy|casing\s*details", re.I),
    "mud": re.compile(r"mud\s*program", re.I),
    "timelog": re.compile(r"^\s*time\s*log", re.I),
}


def segment_records(page_text: str) -> list[dict]:
    """Group lines into records (time-log entries, bullets, paragraphs) with page offsets."""
    records: list[dict] = []
    section = "header"
    offset = 0
    cur = None
    for line in page_text.splitlines(keepends=True):
        start = offset
        offset += len(line)
        s = line.strip()
        if not s:
            cur = None
            continue
        heading = None
        if re.match(r"^\d+\.\s+[A-Z]", s) or s.isupper() and len(s) < 60 and not RE_TIMELOG.match(s):
            for name, rx in HEADINGS.items():
                if rx.search(s):
                    heading = name
                    break
        if heading:
            section = heading
            cur = None
            continue
        m = RE_TIMELOG.match(s)
        is_bullet = s.startswith(("- ", "* ", "•"))
        if m or is_bullet or cur is None or section in ("header", "tops", "casing", "mud"):
            body_start = start + (line.find(m.group(4)) if m else (line.find(s) + (2 if is_bullet else 0)))
            cur = {"section": "timelog" if m else section, "start": body_start, "end": start + len(line.rstrip("\n")),
                   "code": m.group(3) if m else None}
            records.append(cur)
        else:
            cur["end"] = start + len(line.rstrip("\n"))
    for r in records:
        r["text"] = page_text[r["start"]:r["end"]]
    return records


# ---------------------------------------------------------------------------
# Rules for subtype / severity / cause
# ---------------------------------------------------------------------------
def classify_detail(hazard: str, text: str, rate: float | None) -> tuple[str, str]:
    low = text.lower()
    if hazard == "LOSS":
        if re.search(r"total|complete|no returns", low) or (rate and rate > 100):
            return "total", "total"
        if "seepage" in low or (rate is not None and rate < 10):
            return "seepage", "seepage"
        return "partial", "partial"
    if hazard == "KICK":
        if re.search(r"kick|influx|pit gain|shut", low):
            return "kick", "major"
        return "gas", "minor"
    if hazard == "STUCK":
        if "differential" in low:
            return "differential", "major"
        if re.search(r"pack", low):
            return "pack-off", "major"
        if re.search(r"coal|caving|ream|mechanical", low):
            return "mechanical", "major"
        return "unspecified", "major"
    if hazard == "TIGHT":
        return ("bit balling", "moderate") if "ball" in low else ("tight hole", "moderate")
    if hazard == "INSTAB":
        return "cavings", "moderate"
    if hazard == "TORQUE":
        return "stick-slip", "minor"
    if hazard == "CEMENT":
        return ("poor CBL", "moderate") if re.search(r"cbl|bond|toc|top of cement", low) else ("losses during cementing", "moderate")
    if hazard == "FISH":
        return "fishing", "major"
    return "unspecified", "moderate"


def infer_cause(hazard: str, subtype: str, formation: str | None) -> str | None:
    if hazard == "LOSS":
        return {"TIPAM": "depleted sand", "SYLHET": "natural fractures", "NAMSANG": "unconsolidated formation"}.get(
            formation or "", "induced fracture (high ECD)")
    if hazard == "KICK":
        return "underbalance in overpressured zone" if subtype == "kick" else "gas-bearing sand"
    if hazard == "STUCK":
        return {"differential": "differential sticking", "pack-off": "pack-off / poor hole cleaning",
                "mechanical": "mechanical (coal/ledges)"}.get(subtype)
    if hazard == "TIGHT":
        return "bit balling" if subtype == "bit balling" else "reactive clay swelling"
    if hazard == "INSTAB":
        return "shear failure (MW below collapse)"
    if hazard == "TORQUE":
        return "coal stringers"
    if hazard == "CEMENT":
        return "losses into depleted zone"
    if hazard == "FISH":
        return "stuck pipe not freed"
    return None


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------
def detect_kind(first_text: str) -> str:
    up = first_text.upper()
    flat = "".join(up.split())   # OCR often drops the spaces in bold headings ('WELL COMPLETIONREPORT')
    if "WELLCOMPLETIONREPORT" in flat:
        return "WCR"
    if "DAILYDRILLINGREPORT" in flat or "DDR" in up[:200]:
        return "DDR"
    return "OTHER"


def extract_document(pages: list[PageText], clf: SentenceClassifier | None, ctx_lookup,
                     doc_id: str = "doc", well_hint: str | None = None) -> DocExtraction:
    warnings: list[str] = []
    kind = detect_kind(pages[0].text if pages else "")
    well_id = well_hint
    if not well_id:
        for p in pages[:2]:
            m = RE_WELL.search(p.text)
            if m:
                well_id = m.group(1).upper()
                break
    ctx: WellCtx | None = ctx_lookup(well_id) if well_id else None
    if well_id and ctx is None:
        warnings.append(f"Well {well_id} not found in master data; depth->formation inference limited.")
    if not well_id:
        warnings.append("No well identifier found in document.")

    all_sents: list[SentenceInfo] = []
    record_meta: list[dict] = []
    page_meta: list[dict] = []
    for p in pages:
        page_meta.append({"page": p.page_no, "chars": len(p.text), "ocr": p.ocr, "ocr_conf": p.ocr_conf,
                          "needs_ocr": p.needs_ocr})
        if p.needs_ocr:
            warnings.append(f"Page {p.page_no} is scanned and no OCR engine is installed (pip install rapidocr-onnxruntime).")
            continue
        text = repair_ocr_spacing(ocr_fix_numbers(p.text)) if p.ocr else p.text
        if p.ocr:
            p.text = text  # store the repaired text so citations/offsets refer to it
        date = (RE_DATE.search(text) or [None, None])[1] if RE_DATE.search(text) else None
        mw_hdr = ecd_hdr = None
        m_mw = re.search(r"\bMW:?\s*([\d.]+\s?(?:ppg|sg|pcf))", text, re.I)
        m_ecd = re.search(r"\bECD:?\s*([\d.]+\s?(?:ppg|sg|pcf))", text, re.I)
        if m_mw:
            mw_hdr = (parse_mw(m_mw.group(1)) or [None])[0]
        if m_ecd:
            ecd_hdr = (parse_mw(m_ecd.group(1)) or [None])[0]
        m_d24 = re.search(r"Depth @ 24:00:?\s*([^\n]+?)(?:\s{2,}|\n|$)", text, re.I)
        depth_hdr = (parse_depths(m_d24.group(1)) or [{}])[0].get("value_m") if m_d24 else None
        for r in segment_records(text):
            ridx = len(record_meta)
            record_meta.append({"page": p.page_no, "section": r["section"], "date": date, "mw": mw_hdr, "ecd": ecd_hdr,
                                "depth_hdr": depth_hdr, "text": r["text"], "start": r["start"], "end": r["end"],
                                "ocr_conf": p.ocr_conf})
            for s0, s1, s in split_sentences(r["text"], base=r["start"]):
                all_sents.append(analyse_sentence(s.replace("\n", " "), p.page_no, s0, s1, ridx, fuzzy=p.ocr))

    probs = clf.predict_proba([s.text for s in all_sents]) if clf else [{} for _ in all_sents]
    for s, pr in zip(all_sents, probs):
        s.clf = pr

    events: list[XEvent] = []
    lessons: list[dict] = []
    tops: list[dict] = []
    open_ev: XEvent | None = None
    open_rec = -1
    for s in all_sents:
        rm = record_meta[s.record]
        if s.record != open_rec:
            open_ev, open_rec = None, s.record
        if rm["section"] == "tops":
            m = re.match(r"^\s*([A-Za-z][A-Za-z .]+?)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s*$", rm["text"])
            code = detect_formation(m.group(1), fuzzy=True) if m else None
            if m and code:
                tops.append({"formation": code, "md": float(m.group(2).replace(",", "")),
                             "tvd": float(m.group(3).replace(",", "")), "page": s.page})
            s.role = "top"
            continue
        if rm["section"] == "lessons":
            s.role = "lesson"
            if lessons and lessons[-1]["record"] == s.record:
                l = lessons[-1]
                l["text"] += " " + s.text
                l["end"] = s.end
                l["formation"] = l["formation"] or s.formation
                if l["hazard"] is None:
                    hz = [h.hazard for h in find_hazards(s.text)]
                    l["hazard"] = hz[0] if hz else None
            else:
                hz = [h.hazard for h in find_hazards(s.text)]
                lessons.append({"text": s.text.lstrip("-* "), "hazard": hz[0] if hz else None, "record": s.record,
                                "formation": s.formation, "page": s.page, "start": s.start, "end": s.end})
            continue
        if rm["section"] in ("casing", "mud", "header"):
            continue

        npt = parse_npt(s.text)
        new_depth = s.depths[0]["value_m"] if s.depths else None
        continuation = (open_ev is not None and s.hazard == open_ev.hazard and
                        (new_depth is None or open_ev.md is None or abs(new_depth - open_ev.md) <= MERGE_TOL_M))
        clf_top = max(s.clf.items(), key=lambda kv: kv[1]) if s.clf else ("NONE", 0.0)

        if s.hazard and not s.hypothetical and not s.action and not continuation:
            open_ev = _make_event(s, rm, ctx, well_id, doc_id, clf_top)
            events.append(open_ev)
            s.role = "event"
        elif s.hazard is None and not s.hypothetical and not s.action and not s.negated_hazards and \
                clf_top[0] in RIBBON_EVENT_HAZARDS and clf_top[1] >= 0.85 and open_ev is None:
            s.hazard = clf_top[0]
            open_ev = _make_event(s, rm, ctx, well_id, doc_id, clf_top, lexicon=False)
            events.append(open_ev)
            s.role = "event"
        elif s.mitigations and open_ev is not None:
            for code in s.mitigations:
                if code in MITIGATION_BY_CODE or code == "BACKOFF":
                    open_ev.actions.append({"code": code, "success": None, "text": s.text})
            if s.outcome and open_ev.actions:
                open_ev.actions[-1]["success"] = s.outcome == "success"
            s.role = "action"
            _cite(open_ev, s, doc_id)
        elif s.outcome and open_ev is not None:
            for a in reversed(open_ev.actions):
                if a["success"] is None:
                    a["success"] = s.outcome == "success"
                    break
            s.role = "outcome"
            _cite(open_ev, s, doc_id)
        elif continuation:
            s.role = "outcome" if s.outcome else "continuation"
            if s.outcome and open_ev.actions and open_ev.actions[-1]["success"] is None:
                open_ev.actions[-1]["success"] = s.outcome == "success"
            _cite(open_ev, s, doc_id)
        elif s.negated_hazards and not s.hazard:
            s.role = "negated"
        elif s.hazard and s.hypothetical:
            s.role = "hypothetical"
        elif s.action:
            s.role = "action"
        if npt is not None and open_ev is not None:
            open_ev.npt_hours = npt
        if open_ev is not None and open_ev.hazard == "KICK" and re.search(r"\bkill", s.text, re.I):
            kmw = parse_mw(s.text)
            if kmw:
                open_ev.extra["kill_mw"] = max(kmw)
        if s.role == "event" and open_ev is not None:
            txt = rm["text"].replace("\n", " ")
            if open_ev.volume_bbl is None and open_ev.hazard == "KICK":
                open_ev.volume_bbl = parse_volume(s.text)
    for e in events:
        e.actions = [a for a in e.actions]
        for a in e.actions:
            if a["success"] is None:
                a["success"] = False if a is not e.actions[-1] else (e.extra.get("record_outcome") == "success")
        e.summary = _summary(e)
    return DocExtraction(kind, well_id, page_meta, all_sents, events, lessons, tops, warnings)


def _cite(ev: XEvent, s: SentenceInfo, doc_id: str) -> None:
    ev.citations.append({"doc_id": doc_id, "page_no": s.page, "start": s.start, "end": s.end, "text": s.text})


_CASING_SIZE = re.compile(r"\b(30|20|13[- ]3/8|9[- ]5/8|7)\s*(?:\"|''|in\b|inch)?\s*(?:casing|liner|csg)", re.I)


def _named_shoe(text: str, ctx: WellCtx | None) -> float | None:
    """Shoe depth (master data) of the casing string a sentence names, e.g. 'cementing of 9-5/8" casing'."""
    if ctx is None or not ctx.sections:
        return None
    m = _CASING_SIZE.search(text)
    if not m:
        return None
    size = m.group(1).replace(" ", "-")
    for sec in ctx.sections:
        cs = (sec.get("casing") or "").replace(" ", "-")
        if cs.startswith(size + '"') or cs.startswith(size + " ") or cs == size:
            return sec["shoe_md"]
    return None


def _make_event(s: SentenceInfo, rm: dict, ctx: WellCtx | None, well_id, doc_id, clf_top, lexicon=True) -> XEvent:
    text = s.text
    rate = parse_rate(text)
    subtype, severity = classify_detail(s.hazard, text, rate)
    md = None
    depth_src = "none"
    if s.depths:
        d = s.depths[0]
        if d["kind"] == "tvd" and ctx and ctx.traj is not None:
            md = float(ctx.traj.md_at_tvd(d["value_m"]))
        else:
            md = d["value_m"]
        depth_src = "sentence"
    else:
        rec_depths = parse_depths(rm["text"])
        # A cementing sentence that names its casing string sits at that string's shoe. Prefer that over a
        # depth borrowed from elsewhere in the record: on poor scans the bullet dash is often lost and two
        # complications merge into one record.
        own_shoe = _named_shoe(text, ctx) if s.hazard == "CEMENT" else None
        if own_shoe is not None:
            md, depth_src = own_shoe, "casing shoe (master data)"
        elif rec_depths:
            md, depth_src = rec_depths[-1]["value_m"], "record"
        elif s.hazard == "CEMENT" and ctx is not None and ctx.sections:
            want = "9-5/8" if "9-5/8" in rm["text"] or "9 5/8" in rm["text"] or "tipam" in rm["text"].lower() else None
            secs = [x for x in ctx.sections if want and want in (x.get("casing") or "")] or ctx.sections[2:3]
            if secs:
                md, depth_src = secs[0]["shoe_md"], "casing shoe (master data)"
        if md is None and rm.get("depth_hdr"):
            md, depth_src = rm["depth_hdr"], "report header"
    formation, fm_src = s.formation, "text"
    if formation is None:
        formation = detect_formation(rm["text"])
        fm_src = "record" if formation else fm_src
    if formation is None and ctx and md is not None:
        formation, fm_src = ctx.formation_at_md(md), "depth->tops"
    if formation is None:
        fm_src = "unknown"
    mw, ecd = rm.get("mw"), rm.get("ecd")
    if (mw is None or ecd is None) and ctx and md is not None:
        mw2, ecd2 = ctx.section_mw(md)
        mw, ecd = mw or mw2, ecd or ecd2
    p_clf = s.clf.get(s.hazard, 0.0) if s.clf else 0.5
    if lexicon:
        conf = 0.55 + 0.35 * p_clf
    else:
        conf = 0.6 * clf_top[1]
    if md is None:
        conf -= 0.2
    elif depth_src != "sentence":
        conf -= 0.05
    if formation is None:
        conf -= 0.1
    if rm.get("ocr_conf") is not None:
        conf *= 0.7 + 0.3 * rm["ocr_conf"]
    ev = XEvent(well_id=well_id, hazard=s.hazard, subtype=subtype, md=md, depth_source=depth_src, formation=formation,
                formation_source=fm_src, severity=severity, rate_bbl_hr=rate,
                volume_bbl=parse_volume(text) if s.hazard == "KICK" else None, mw_ppg=mw, ecd_ppg=ecd,
                date=rm.get("date"), confidence=round(float(np.clip(conf, 0.05, 0.99)), 3))
    if s.hazard == "TIGHT":
        op = parse_overpull(text)
        if op:
            ev.extra["overpull_klbs"] = op
    if s.hazard == "KICK":
        g = parse_gas(text)
        if g:
            ev.extra["gas_pct"] = g
    if s.outcome:
        ev.extra["record_outcome"] = s.outcome
    ev.cause = infer_cause(ev.hazard, ev.subtype, ev.formation)
    for code in s.mitigations:  # e.g. "... losses at 2,100 m, pumped LCM pill" in the same sentence
        if code in MITIGATION_BY_CODE or code == "BACKOFF":
            ev.actions.append({"code": code, "success": None, "text": text})
    if ctx and md is not None:
        ev.tvd = round(ctx.tvd_at(md), 1)
    _cite(ev, s, doc_id)
    return ev


def _summary(e: XEvent) -> str:
    from ..domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE
    fm = FORMATION_BY_CODE[e.formation].name if e.formation in FORMATION_BY_CODE else "unknown formation"
    parts = [f"{HAZARD_BY_CODE[e.hazard].label} ({e.subtype}) in {fm}"]
    if e.md is not None:
        parts.append(f"at {e.md:,.0f} m MD")
    if e.rate_bbl_hr:
        parts.append(f"{e.rate_bbl_hr:.0f} bbl/hr")
    if e.actions:
        acts = [MITIGATION_BY_CODE[a["code"]].label if a["code"] in MITIGATION_BY_CODE else "Back-off"
                for a in e.actions]
        ok = [a for a in e.actions if a["success"]]
        parts.append("actions: " + " -> ".join(acts) + (" (resolved)" if ok else " (unresolved)"))
    if e.npt_hours:
        parts.append(f"NPT {e.npt_hours:.1f} h")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Persistence & consolidation
# ---------------------------------------------------------------------------
def doc_id_for(path: Path) -> str:
    return "D-" + hashlib.sha1(path.name.encode()).hexdigest()[:10]


class Ingestor:
    def __init__(self, db: DB, clf: SentenceClassifier | None = None):
        self.db = db
        self.clf = clf
        self._ctx_cache: dict[str, WellCtx | None] = {}
        self.thick = typical_thickness(db)

    def ctx(self, well_id: str | None) -> WellCtx | None:
        if well_id is None:
            return None
        if well_id not in self._ctx_cache:
            self._ctx_cache[well_id] = load_well_ctx(self.db, well_id)
        return self._ctx_cache[well_id]

    def ingest_pdf(self, path: Path, kind_hint: str | None = None, title: str | None = None,
                   well_hint: str | None = None, source: str = "corpus") -> dict:
        return self._ingest_pages(path, extract_pages(path), kind_hint, title, well_hint, source)

    def ingest_witsml(self, path: Path, title: str | None = None, source: str = "upload") -> dict:
        from .witsml import load_witsml_pages
        pages, reports = load_witsml_pages(path)
        for wname in {r["well"] for r in reports}:
            if not self.db.one("SELECT id FROM wells WHERE id=?", (wname,)):
                self.db.insert("wells", {"id": wname, "name": wname, "structure_id": "IMPORTED", "source": "witsml",
                                         "synthetic": 0, "is_active": 0})
        self._ctx_cache.clear()
        # the XML names its well; public names like "15/9-F-11" do not match the DDR header pattern in the text
        hint = reports[0]["well"] if reports and reports[0]["well"] != "UNKNOWN" else None
        return self._ingest_pages(path, pages, "DDR", title or path.name, hint, source)

    def _ingest_pages(self, path: Path, pages: list[PageText], kind_hint, title, well_hint, source) -> dict:
        doc_id = doc_id_for(path)
        ex = extract_document(pages, self.clf, self.ctx, doc_id=doc_id, well_hint=well_hint)
        kind = kind_hint or ex.kind
        self.db.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        self.db.insert("documents", {"id": doc_id, "well_id": ex.well_id, "kind": kind,
                                     "title": title or path.name, "path": str(path), "pages": len(pages),
                                     "ocr_pages": sum(1 for p in pages if p.ocr), "status": "ingested",
                                     "created": dt.datetime.now().isoformat(timespec="seconds"),
                                     "meta": {"source": source, "warnings": ex.warnings}})
        for p in pages:
            self.db.insert("pages", {"doc_id": doc_id, "page_no": p.page_no, "text": p.text, "ocr": int(p.ocr),
                                     "ocr_conf": p.ocr_conf})
        stored = self.store_extraction(ex, doc_id)
        self.db.commit()
        return {"doc_id": doc_id, "kind": kind, "well_id": ex.well_id, "pages": ex.pages, "warnings": ex.warnings,
                "sentences": [s.to_dict() for s in ex.sentences if s.role != "other" or s.hazard],
                "events": stored["events"], "lessons": stored["lessons"], "tops": ex.tops,
                "review": stored["review"]}

    def ingest_text(self, text: str, title: str, author: str, well_hint: str | None = None, lang: str = "en",
                    original: str | None = None, source: str = "expert-memo") -> dict:
        """Ingest an expert memo (typed, or transcribed from a voice note). Everything extracted from it goes to
        peer review before it can influence alerts or rankings: expert recollection is valuable but unverified."""
        memo_hash = hashlib.sha1(f"{author}|{title}|{text}".encode()).hexdigest()[:10]
        path = Path(f"memo_{memo_hash}.txt")
        doc_id = doc_id_for(path)
        pages = [PageText(1, text, False, None)]
        ex = extract_document(pages, self.clf, self.ctx, doc_id=doc_id, well_hint=well_hint)
        ex.well_id = ex.well_id or well_hint
        self.db.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        self.db.insert("documents", {"id": doc_id, "well_id": ex.well_id, "kind": "MEMO", "title": title,
                                     "path": str(path), "pages": 1, "ocr_pages": 0, "status": "in review",
                                     "created": dt.datetime.now().isoformat(timespec="seconds"),
                                     "meta": {"source": source, "author": author, "lang": lang,
                                              "original_transcript": original, "warnings": ex.warnings}})
        self.db.insert("pages", {"doc_id": doc_id, "page_no": 1, "text": text, "ocr": 0, "ocr_conf": None})
        if not ex.lessons:   # a memo is itself a lesson candidate even without explicit lesson wording
            hz = ex.events[0].hazard if ex.events else None
            fm = ex.events[0].formation if ex.events else None
            ex.lessons.append({"hazard": hz, "formation": fm, "text": text.strip()[:600], "page": 1, "start": 0,
                               "end": min(len(text), 600)})
        stored = self.store_extraction(ex, doc_id, force_review=True, author=author)
        self.db.commit()
        return {"doc_id": doc_id, "kind": "MEMO", "well_id": ex.well_id, "author": author, "warnings": ex.warnings,
                "pages": ex.pages, "tops": [],
                "sentences": [s.to_dict() for s in ex.sentences if s.role != "other" or s.hazard],
                "events": stored["events"], "lessons": stored["lessons"], "review": stored["review"]}

    def store_extraction(self, ex: DocExtraction, doc_id: str, force_review: bool = False, author: str | None = None) -> dict:
        ctx = self.ctx(ex.well_id)
        out_events, review = [], []
        for e in ex.events:
            if ctx is not None and e.formation and e.md is not None:
                e.tvd = e.tvd if e.tvd is not None else round(ctx.tvd_at(e.md), 1)
                e.rel = rel_in_formation(ctx.tops_tvd, e.formation, e.tvd, self.thick)
            status = "auto" if e.confidence >= config.REVIEW_THRESHOLD and not force_review else "pending"
            merged_id = self._merge_or_insert(e, status)
            out_events.append({"id": merged_id, "hazard": e.hazard, "subtype": e.subtype, "md": e.md,
                               "formation": e.formation, "confidence": e.confidence, "status": status,
                               "summary": e.summary, "depth_source": e.depth_source,
                               "formation_source": e.formation_source,
                               "actions": [{"code": a["code"], "success": a["success"]} for a in e.actions],
                               "citations": e.citations})
            if status == "pending":
                rid = f"R-{merged_id}"
                self.db.insert("review_queue", {"id": rid, "doc_id": doc_id, "kind": "event", "payload": out_events[-1],
                                                "confidence": e.confidence,
                                                "reason": f"expert memo by {author}: peer review required" if force_review
                                                else _review_reason(e), "status": "open",
                                                "created": dt.datetime.now().isoformat(timespec="seconds")})
                review.append(rid)
        stored_lessons = []
        for i, l in enumerate(ex.lessons):
            lid = f"L-{doc_id}-{i}"
            if force_review:
                payload = {"id": lid, "well_id": ex.well_id, "hazard": l["hazard"], "formation": l["formation"],
                           "text": l["text"], "doc_id": doc_id, "page_no": l["page"], "start": l["start"],
                           "end": l["end"], "author": author}
                self.db.insert("review_queue", {"id": f"R-{lid}", "doc_id": doc_id, "kind": "lesson", "payload": payload,
                                                "confidence": 1.0, "reason": f"expert memo by {author}: peer review required",
                                                "status": "open", "created": dt.datetime.now().isoformat(timespec="seconds")})
                review.append(f"R-{lid}")
                stored_lessons.append({"id": lid, **l, "status": "pending"})
                continue
            self.db.insert("lessons", {"id": lid, "well_id": ex.well_id, "hazard": l["hazard"], "formation": l["formation"],
                                       "text": l["text"], "doc_id": doc_id, "page_no": l["page"], "start": l["start"],
                                       "end": l["end"]})
            stored_lessons.append({"id": lid, **l})
        if ex.well_id and ex.tops and ctx is not None and not ctx.tops_md:
            for t in ex.tops:
                self.db.insert("tops", {"well_id": ex.well_id, "formation": t["formation"], "md": t["md"], "tvd": t["tvd"],
                                        "source": "wcr"})
        return {"events": out_events, "lessons": stored_lessons, "review": review}

    def _merge_or_insert(self, e: XEvent, status: str) -> str:
        existing = None
        if e.well_id and e.md is not None:
            cands = self.db.query("SELECT * FROM events WHERE well_id=? AND hazard=? AND md BETWEEN ? AND ?",
                                  (e.well_id, e.hazard, e.md - MERGE_TOL_M, e.md + MERGE_TOL_M))
            existing = cands[0] if cands else None
        if existing:
            eid = existing["id"]
            n_old = self.db.one("SELECT COUNT(*) AS n FROM event_actions WHERE event_id=?", (eid,))["n"]
            if len(e.actions) > n_old:
                self.db.execute("DELETE FROM event_actions WHERE event_id=?", (eid,))
                self._insert_actions(eid, e)
            upd = {"confidence": max(existing["confidence"] or 0, e.confidence),
                   "npt_hours": existing["npt_hours"] or e.npt_hours,
                   "rate_bbl_hr": existing["rate_bbl_hr"] or e.rate_bbl_hr,
                   "date": existing["date"] or e.date,
                   "status": "auto" if max(existing["confidence"] or 0, e.confidence) >= config.REVIEW_THRESHOLD
                   else existing["status"],
                   "summary": e.summary if len(e.actions) > n_old else existing["summary"],
                   "resolved": int(bool(e.resolved)) if len(e.actions) > n_old else existing["resolved"]}
            self.db.execute("UPDATE events SET " + ",".join(f"{k}=?" for k in upd) + " WHERE id=?", [*upd.values(), eid])
        else:
            n = self.db.one("SELECT COUNT(*) AS n FROM events WHERE well_id IS ?", (e.well_id,))["n"]
            eid = f"{e.well_id or 'UNK'}-X{n + 1:02d}"
            while self.db.one("SELECT id FROM events WHERE id=?", (eid,)):
                n += 1
                eid = f"{e.well_id or 'UNK'}-X{n + 1:02d}"
            self.db.insert("events", {"id": eid, "well_id": e.well_id, "hazard": e.hazard, "subtype": e.subtype,
                                      "md": e.md, "tvd": e.tvd, "formation": e.formation, "rel": e.rel,
                                      "severity": e.severity, "rate_bbl_hr": e.rate_bbl_hr, "volume_bbl": e.volume_bbl,
                                      "npt_hours": e.npt_hours, "mw_ppg": e.mw_ppg, "ecd_ppg": e.ecd_ppg,
                                      "cause": e.cause, "date": e.date, "confidence": e.confidence, "status": status,
                                      "summary": e.summary, "resolved": None if e.resolved is None else int(e.resolved),
                                      "extra": e.extra})
            self._insert_actions(eid, e)
        for c in e.citations:
            self.db.insert("citations", {"event_id": eid, **c}, replace=False)
        return eid

    def _insert_actions(self, eid: str, e: XEvent) -> None:
        for i, a in enumerate(e.actions):
            self.db.insert("event_actions", {"event_id": eid, "seq": i, "code": a["code"], "text": a.get("text", ""),
                                             "success": int(bool(a["success"]))}, replace=False)


def _review_reason(e: XEvent) -> str:
    r = []
    if e.depth_source == "none":
        r.append("no depth found")
    elif e.depth_source != "sentence":
        r.append(f"depth taken from {e.depth_source}")
    if e.formation is None:
        r.append("formation unresolved")
    if e.confidence < 0.6:
        r.append("low classifier agreement")
    return "; ".join(r) or "below confidence threshold"
