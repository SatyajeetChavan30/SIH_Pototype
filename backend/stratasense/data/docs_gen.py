"""Render SYNTHETIC Daily Drilling Reports (DDR) and Well Completion Reports (WCR) as PDFs.

Two phrasing styles are used:
  * style "A": the knowledge-base corpus (also used to train the sentence classifier)
  * style "B": held-out paraphrases, ALL-CAPS rig shorthand, different units - used
    only to *evaluate* the extraction pipeline honestly.
A few legacy WCRs are rasterised (image-only pages) to exercise the OCR path.
"""
from __future__ import annotations

import io
import random
import textwrap
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

from ..domain.ontology import FORMATION_BY_CODE, MITIGATION_BY_CODE
from .synth import STRUCT_BY_ID, TruthEvent, Well

M_TO_FT = 3.28084
PPG_TO_SG = 0.119826
BBL_TO_M3 = 0.158987

DISCLAIMER = "STRATASENSE DEMO - SYNTHETIC DATA - NOT A REAL OPERATOR RECORD"

# ---------------------------------------------------------------------------
# Unit formatting
# ---------------------------------------------------------------------------


class Units:
    def __init__(self, system: str):
        self.system = system

    def depth(self, m: float, rng: random.Random) -> str:
        if self.system == "field_ft":
            v = m * M_TO_FT
            return rng.choice([f"{v:,.0f} ft", f"{v:.0f}ft", f"{v:,.0f} ft MD"])
        return rng.choice([f"{m:,.0f} m", f"{m:.0f}m", f"{m:,.0f} m MD", f"{m:.0f} mMD"])

    def mw(self, ppg: float) -> str:
        if self.system == "metric_sg":
            return f"{ppg * PPG_TO_SG:.2f} SG"
        return f"{ppg:.2f} ppg"

    def rate(self, bbl_hr: float) -> str:
        if self.system == "metric_sg":
            return f"{bbl_hr * BBL_TO_M3:.1f} m3/hr"
        return f"{bbl_hr:.0f} bbl/hr"

    def vol(self, bbl: float) -> str:
        if self.system == "metric_sg":
            return f"{bbl * BBL_TO_M3:.1f} m3"
        return f"{bbl:.0f} bbl"


# ---------------------------------------------------------------------------
# Phrase banks
# ---------------------------------------------------------------------------

EVENT_TEMPLATES = {
    "A": {
        ("LOSS", "seepage"): ["Observed seepage losses of {rate} at {depth}{fm_in}.",
                              "Seepage losses noticed while drilling at {depth}{fm_in}, rate {rate}."],
        ("LOSS", "partial"): ["Observed partial losses at {depth}{fm_in}, loss rate {rate}.",
                              "Partial loss of returns encountered @ {depth}{fm_in} at {rate}.",
                              "While drilling ahead at {depth}{fm_in} experienced partial losses of {rate}."],
        ("LOSS", "total"): ["Total losses encountered at {depth}{fm_in}; no returns at surface.",
                            "Lost circulation - complete loss of returns at {depth}{fm_in} (>{rate})."],
        ("KICK", "kick"): ["Well kicked at {depth}{fm_in}: pit gain of {vol} observed, shut in the well.",
                           "Drilling break followed by influx at {depth}{fm_in}; pit gain {vol}, well shut-in on BOP.",
                           "Positive flow check at {depth}{fm_in}, pit gain {vol}. Kick confirmed."],
        ("KICK", "gas"): ["High connection gas of {gas}% recorded at {depth}{fm_in}; mud gas cut.",
                          "Gas cut mud observed at {depth}{fm_in} with background gas rising to {gas}%."],
        ("STUCK", "differential"): ["Pipe stuck while making connection at {depth}{fm_in} - differential sticking suspected.",
                                    "String got stuck at {depth}{fm_in} after static period, differentially stuck."],
        ("STUCK", "pack-off"): ["Hole packed off at {depth}{fm_in}; pressure spike and pipe stuck.",
                                "Pack-off while drilling at {depth}{fm_in}, unable to move string."],
        ("STUCK", "mechanical"): ["String stuck at {depth}{fm_in} while back reaming through coal, unable to rotate.",
                                  "Pipe stuck at {depth}{fm_in} due to caving coal, could not pull free."],
        ("TIGHT", "tight hole"): ["Tight hole encountered at {depth}{fm_in} with overpull of {op} klbs while POOH.",
                                  "Observed overpull of {op} klbs at {depth}{fm_in}, tight spot."],
        ("TIGHT", "bit balling"): ["Bit balling suspected at {depth}{fm_in}: ROP dropped and SPP increased.",
                                   "Bit balled up while drilling sticky clay at {depth}{fm_in}."],
        ("INSTAB", "cavings"): ["Splintery cavings observed on shakers at {depth}{fm_in}; hole instability.",
                                "Hole unstable at {depth}{fm_in} with large cavings and fill on bottom."],
        ("TORQUE", "stick-slip"): ["Erratic torque and stick-slip at {depth}{fm_in}, torque spikes up to 24 kft-lbf.",
                                   "High torque with torque spikes observed while drilling at {depth}{fm_in}."],
        ("CEMENT", "losses during cementing"): ["Losses during cementing of 9-5/8\" casing; partial returns during cementing.",
                                                "Observed losses during cementing of 9-5/8\" casing at shoe {depth}."],
        ("CEMENT", "poor CBL"): ["CBL-VDL run: poor cement bond across Tipam, top of cement below plan.",
                                 "Poor CBL observed behind 9-5/8\" casing; low TOC at {depth}."],
        ("FISH", "fishing"): ["Unable to free string at {depth}; backed off string and started fishing operations.",
                              "Fishing job initiated at {depth} after string could not be freed."],
    },
    "B": {
        ("LOSS", "seepage"): ["MINOR SEEPAGE LOSS @ {depth}{fm_in} ({rate})."],
        ("LOSS", "partial"): ["LOSING MUD @ {depth}{fm_in}. DYNAMIC LOSSES {rate}.",
                              "RETURNS DROPPED - PARTIAL LOSSES {rate} AT {depth}{fm_in}.",
                              "PRTL LOSSES {rate} @ {depth}{fm_in}.",
                              "LC ZONE @ {depth}{fm_in}, {rate} GOING AWAY."],
        ("LOSS", "total"): ["NO RETURNS AT SURFACE @ {depth}{fm_in}. TOTAL LOSS OF CIRCULATION."],
        ("KICK", "kick"): ["INFLUX DETECTED @ {depth}{fm_in}, PIT GAIN {vol}. CLOSED BOP.",
                           "WELL FLOWED ON CONNECTION @ {depth}{fm_in}. SIDPP/SICP RECORDED, GAIN {vol}."],
        ("KICK", "gas"): ["CONNECTION GAS {gas}% @ {depth}{fm_in}. GAS-CUT MUD AT FLOWLINE."],
        ("STUCK", "differential"): ["STRING DIFFERENTIALLY STUCK @ {depth}{fm_in} AFTER SURVEY.",
                                    "STRG STUCK @ {depth}{fm_in} (DIFF STKG SUSPECTED)."],
        ("STUCK", "pack-off"): ["PACKING OFF @ {depth}{fm_in}. STRING STUCK."],
        ("STUCK", "mechanical"): ["PIPE STUCK @ {depth}{fm_in} WHILE REAMING COAL."],
        ("TIGHT", "tight hole"): ["O/P {op} KLBS @ {depth}{fm_in} - TIGHT HOLE.", "TITE SPOT @ {depth}{fm_in}, WORKED THRU."],
        ("TIGHT", "bit balling"): ["BIT BALLING @ {depth}{fm_in}. NO PROGRESS."],
        ("INSTAB", "cavings"): ["HEAVY CAVINGS ON SHAKER @ {depth}{fm_in}, HOLE SLOUGHING.",
                                "LARGE SPLINTERY CAVINGS @ {depth}{fm_in}, FILL 6 M ON BTM."],
        ("TORQUE", "stick-slip"): ["TORQUE FLUCTUATION / STICK-SLIP @ {depth}{fm_in}.", "TRQ ERRATIC 18-26 KFT-LBF @ {depth}{fm_in}."],
        ("CEMENT", "losses during cementing"): ["NO CEMENT RETURNS - LOSSES DURING CEMENTING 9-5/8\" CSG."],
        ("CEMENT", "poor CBL"): ["CBL SHOWS POOR BOND, TOP OF CEMENT BELOW PLANNED DEPTH."],
        ("FISH", "fishing"): ["BACKED OFF @ {depth}. RIH WITH FISHING ASSY."],
    },
}

SUCCESS_A = ["Losses cured.", "Full returns regained.", "Pipe came free.", "String freed.", "Well killed successfully.",
             "Hole stabilised.", "Torque normalised.", "Fish recovered.", "Problem resolved."]
FAIL_A = ["Losses continued.", "No improvement.", "Attempt unsuccessful.", "Still stuck."]
SUCCESS_BY_HAZARD = {"LOSS": ["Losses cured.", "Full returns regained."], "KICK": ["Well killed successfully.", "Situation under control."],
                     "STUCK": ["Pipe came free.", "String freed."], "TIGHT": ["Problem resolved.", "Hole in good condition after reaming."],
                     "INSTAB": ["Hole stabilised."], "TORQUE": ["Torque normalised."], "CEMENT": ["Cement returns observed.", "Problem resolved."],
                     "FISH": ["Fish recovered.", "Recovered fish."]}
FAIL_BY_HAZARD = {"LOSS": ["Losses continued.", "No improvement."], "STUCK": ["Still stuck.", "Attempt unsuccessful."],
                  "FISH": ["Attempt unsuccessful."], "KICK": ["No improvement."], "TIGHT": ["No improvement."],
                  "INSTAB": ["No improvement."], "TORQUE": ["No improvement."], "CEMENT": ["Attempt unsuccessful."]}

ROUTINE_A = [
    "Drilled {hole} hole from {d0} to {d1} with parameters as per program.",
    "Circulated bottoms up; shakers clean.",
    "Took survey. Continued drilling ahead.",
    "Performed flow check - negative.",
    "Wiper trip to shoe - hole in good condition.",
    "No losses observed. Hole smooth.",
    "No tight spots observed while POOH.",
    "No gas shows. Background gas nil.",
    "Checked mud properties; maintained MW {mw}.",
    "Serviced top drive and rig equipment.",
]
ROUTINE_B = [
    "DRLD {hole} HOLE F/ {d0} T/ {d1}.",
    "CBU. SHAKERS CLEAN.",
    "FLOW CHECK: NEGATIVE.",
    "LOSSES: NIL. GAS: NIL.",
    "NO O/P OBSERVED ON TRIP.",
    "MW MAINTAINED {mw}.",
]
HYPOTHETICAL_A = [
    "Precautionary LCM pill kept ready before entering {fm}.",
    "As per offset data, anticipated losses in {fm}; watch for returns.",
    "Planned to raise MW before entering lower {fm} to avoid a kick.",
    "Risk of differential sticking in {fm}; keep pipe moving.",
]
HYPOTHETICAL_B = [
    "PRECAUTION: LCM READY BEFORE {fm}.",
    "EXPECT LOSSES IN {fm} AS PER PROGNOSIS.",
]
MITIGATION_B = {
    "LCM_FINE": "PUMPED 40 BBL CACO3 PILL.", "LCM_COARSE": "SPOTTED 60 PPB COARSE LCM PILL.",
    "REDUCE_FLOW": "CUT FLOW RATE TO REDUCE ECD.", "CEMENT_PLUG": "SET CEMENT PLUG ACROSS THIEF ZONE.",
    "POOH_HEAL": "POOH TO SHOE, WAITED ON HOLE.", "SHUT_IN_DM": "SHUT IN, CIRCULATED OUT KICK - DRILLER'S METHOD.",
    "WAIT_WEIGHT": "KILLED WELL - WAIT & WEIGHT.", "RAISE_MW": "WEIGHTED UP MUD.",
    "FLOW_CHECK": "FLOW CHECKED, CIRCULATED OUT GAS THROUGH CHOKE.", "JAR_DOWN": "JARRED DOWN.",
    "JAR_UP": "JARRED UP.", "SPOT_PIPE_LAX": "SPOTTED PIPE-LAX PILL, SOAKED.", "CIRC_HIVIS": "PUMPED HI-VIS SWEEP.",
    "BACKOFF": "BACKED OFF STRING.", "BACKREAM": "BACK REAMED THROUGH TIGHT SPOT.",
    "INHIBITION": "RAISED KCL TO 7% AND ADDED GLYCOL.", "BIT_CLEAN": "PUMPED ANTI-BALLING SWEEP.",
    "RAISE_MW_STAB": "INCREASED MW TO STABILISE HOLE.", "ASPHALT": "ADDED GILSONITE.",
    "CONTROL_ROP": "CONTROLLED ROP.", "LUBRICANT": "ADDED LUBRICANT.", "REDUCE_PARAMS": "REDUCED RPM AND WOB.",
    "TOP_JOB": "PERFORMED TOP JOB.", "SQUEEZE": "CARRIED OUT SQUEEZE CEMENTATION.",
    "OVERSHOT": "FISHED WITH OVERSHOT.", "SPEAR": "RAN SPEAR.", "SIDETRACK": "DECIDED TO SIDETRACK THE WELL.",
}

HOLE_BY_SECTION = ["26\"", "17-1/2\"", "12-1/4\"", "8-1/2\""]


def fm_name(code: str) -> str:
    return FORMATION_BY_CODE[code].name


def render_event(e: TruthEvent, units: Units, style: str, rng: random.Random) -> list[str]:
    """Return sentences describing an event + its mitigations/outcomes."""
    tmpl = rng.choice(EVENT_TEMPLATES[style][(e.hazard, e.subtype)])
    fm_mention = rng.random() < (0.7 if style == "A" else 0.5)
    fm = fm_name(e.formation)
    fm_in = (f" in {fm}" if style == "A" else f" IN {fm.upper()}") if fm_mention else ""
    text = tmpl.format(depth=units.depth(e.md, rng), fm_in=fm_in, rate=units.rate(e.rate_bbl_hr or 30),
                       vol=units.vol(e.volume_bbl or 10), gas=e.extra.get("gas_pct", 12),
                       op=int(e.extra.get("overpull_klbs", 40)))
    out = [text]
    if e.hazard == "KICK" and e.subtype == "kick" and "kill_mw" in e.extra:
        out.append(f"Killed well with {units.mw(e.extra['kill_mw'])} mud." if style == "A"
                   else f"KILL MUD {units.mw(e.extra['kill_mw']).upper()}.")
    for a in e.attempts:
        if style == "A":
            m = MITIGATION_BY_CODE.get(a["code"])
            phr = rng.choice(m.phrases) if m else "Backed off string"
            phr = phr[0].upper() + phr[1:]
            s = f"{phr}."
            s += " " + (rng.choice(SUCCESS_BY_HAZARD[e.hazard]) if a["success"] else rng.choice(FAIL_BY_HAZARD[e.hazard]))
        else:
            s = MITIGATION_B[a["code"]] + " " + ("OK." if a["success"] else "NO SUCCESS.")
        out.append(s)
    out.append(f"NPT: {e.npt_hours:.1f} hrs." if style == "A" else f"NPT {e.npt_hours:.1f} HRS.")
    return out


# ---------------------------------------------------------------------------
# PDF writer
# ---------------------------------------------------------------------------

class PdfWriter:
    def __init__(self, fontsize: float = 8.2):
        self.doc = pymupdf.open()
        self.fs = fontsize
        self.width = 97
        self.page = None
        self.y = 0.0

    def new_page(self):
        self.page = self.doc.new_page(width=595, height=842)
        self.y = 40
        self.page.insert_text((36, 24), DISCLAIMER, fontsize=6.5, fontname="helv", color=(0.6, 0.1, 0.1))

    def line(self, text: str = "", bold: bool = False):
        if self.page is None:
            self.new_page()
        for chunk in (textwrap.wrap(text, self.width) or [""]):
            if self.y > 810:
                self.new_page()
            self.page.insert_text((36, self.y), chunk, fontsize=self.fs, fontname="cobo" if bold else "cour")
            self.y += self.fs * 1.32

    def save(self, path: Path):
        self.doc.save(path, garbage=3, deflate=True)
        self.doc.close()


# scan quality -> (dpi, max skew in degrees, blur radius, noise sigma, darkening)
SCAN_QUALITY = {
    "standard": (200, 0.35, 0.4, 6, 8),    # an office flatbed scan of a clean report
    "poor": (150, 1.0, 0.7, 14, 18),       # a low-resolution scan of an old photocopy
}


def rasterize_pdf(src: Path, dst: Path, seed: int, quality: str = "standard"):
    """Turn a text PDF into an image-only 'scanned' PDF (skew + blur + noise)."""
    dpi, skew, blur, noise, dark = SCAN_QUALITY[quality]
    rng = np.random.default_rng(seed)
    src_doc = pymupdf.open(src)
    out = pymupdf.open()
    for page in src_doc:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(float(rng.uniform(-skew, skew)), expand=False, fillcolor=255)
        img = img.filter(ImageFilter.GaussianBlur(blur))
        arr = np.asarray(img).astype(np.int16)
        arr = np.clip(arr + rng.normal(0, noise, arr.shape) - dark, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG", optimize=True)
        p = out.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=buf.getvalue())
    out.save(dst, garbage=3, deflate=True)
    out.close()
    src_doc.close()


# ---------------------------------------------------------------------------
# DDR / WCR
# ---------------------------------------------------------------------------

def write_ddr(well: Well, path: Path, style: str = "A", seed: int = 0) -> list[dict]:
    """Write one PDF with a page per report day. Returns labelled sentences (for classifier training)."""
    rng = random.Random(f"{well.id}-{style}-{seed}")
    units = Units(well.unit_system if style == "A" else {"metric_ppg": "field_ft", "field_ft": "metric_sg",
                                                           "metric_sg": "metric_ppg"}[well.unit_system])
    struct = STRUCT_BY_ID[well.structure_id]
    w = PdfWriter()
    labelled: list[dict] = []
    for i, day in enumerate(well.days):
        sec = well.sections[day["section"]]
        hole = HOLE_BY_SECTION[day["section"]]
        w.new_page()
        up = str.upper if style == "B" else (lambda s: s)
        w.line(up("DAILY DRILLING REPORT") + f"    Report No: {i + 1}    Date: {day['date']}", bold=True)
        w.line(f"Well: {well.id}    Structure: {struct.name}    Rig: SYN-RIG-{sum(map(ord, well.structure_id)) % 7 + 1}")
        w.line(f"Depth @ 00:00: {units.depth(day['from_md'], rng)}    Depth @ 24:00: {units.depth(day['to_md'], rng)}")
        prev = [s for s in well.sections if s["shoe_md"] <= day["from_md"] + 0.5]
        last_csg = f"{prev[-1]['casing']} @ {units.depth(prev[-1]['shoe_md'], rng)}" if prev else "-"
        w.line(f"Hole size: {hole}    Last casing: {last_csg}")
        w.line(f"Mud: {well.mud_system}    MW: {units.mw(sec['mw_ppg'])}    ECD: {units.mw(sec['ecd_ppg'])}    "
               f"Vis: {rng.randint(42, 60)} s    PV/YP: {rng.randint(12, 24)}/{rng.randint(14, 30)}")
        w.line()
        w.line(up("TIME LOG"), bold=True)
        t = 0.0
        routine = ROUTINE_A if style == "A" else ROUTINE_B
        n_routine = rng.randint(3, 5)
        lines: list[tuple[str, str, bool, str | None]] = []  # (code, text, is_event, hazard)
        if day["kind"] == "casing":
            lines.append(("CSG", f"Ran {sec['casing']} casing to {units.depth(sec['shoe_md'], rng)}.", False, None))
            lines.append(("CMT", f"Cemented {sec['casing']} casing as per program.", False, None))
        else:
            d0, d1 = units.depth(day["from_md"], rng), units.depth(day["to_md"], rng)
            lines.append(("DRLG", routine[0].format(hole=hole, d0=d0, d1=d1, mw=units.mw(sec["mw_ppg"])), False, None))
        ev_codes = {e.hazard for e in day["events"]}
        for r in rng.sample(routine[1:], k=min(n_routine, len(routine) - 1)):
            # never contradict an event of the same day with a negated routine remark
            low = r.lower()
            if ("loss" in low and "LOSS" in ev_codes) or (("tight" in low or "o/p" in low) and "TIGHT" in ev_codes) \
                    or ("gas" in low and "KICK" in ev_codes) or ("flow check" in low and "KICK" in ev_codes):
                continue
            lines.append(("CIRC" if "circ" in low or "cbu" in low else "OTHR",
                          r.format(mw=units.mw(sec["mw_ppg"]), hole=hole, d0="", d1=""), False, None))
        for e in day["events"]:
            sents = render_event(e, units, style, rng)
            code = {"LOSS": "NPT-LC", "KICK": "NPT-WC", "STUCK": "NPT-SP", "TIGHT": "NPT-TH", "INSTAB": "NPT-HI",
                    "TORQUE": "DRLG", "CEMENT": "NPT-CMT", "FISH": "NPT-FSH"}[e.hazard]
            lines.insert(rng.randint(1, len(lines)), (code, " ".join(sents), True, e.hazard))
            labelled.append({"text": sents[0], "label": e.hazard})
            for s in sents[1:]:
                labelled.append({"text": s, "label": "ACTION"})
        # hypothetical / precautionary remarks near formation tops
        for code, top_md in well.tops_md.items():
            if day["from_md"] <= top_md - 50 <= day["to_md"] and code in ("TIPAM", "BARAIL", "SYLHET") and rng.random() < 0.5:
                hyp = rng.choice(HYPOTHETICAL_A if style == "A" else HYPOTHETICAL_B).format(
                    fm=fm_name(code) if style == "A" else fm_name(code).upper())
                lines.append(("OTHR", hyp, False, None))
                labelled.append({"text": hyp, "label": "NONE"})
        for code, text, _, _ in lines:
            dur = rng.choice([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.5])
            t0, t1 = t, min(t + dur, 24.0)
            t = t1
            w.line(f"{int(t0):02d}:{int((t0 % 1) * 60):02d}-{int(t1):02d}:{int((t1 % 1) * 60):02d}  {code:<7} {up(text)}")
        for code, text, is_ev, hz in lines:
            if not is_ev:
                for s in text.split(". "):
                    if s.strip():
                        labelled.append({"text": s.strip().rstrip(".") + ".", "label": "NONE"})
        npt = sum(e.npt_hours for e in day["events"])
        w.line()
        w.line(f"NPT today: {npt:.1f} hrs" if style == "A" else f"TOTAL NPT: {npt:.1f} HRS")
    w.save(path)
    return labelled


def write_wcr(well: Well, path: Path, style: str = "A") -> None:
    rng = random.Random(f"{well.id}-wcr")
    units = Units(well.unit_system)
    struct = STRUCT_BY_ID[well.structure_id]
    w = PdfWriter(fontsize=8.6)
    w.new_page()
    w.line("WELL COMPLETION REPORT", bold=True)
    w.line(f"Well: {well.id}    Structure: {struct.name}    Status: {well.status}")
    w.line(f"Surface location: Lat {well.lat:.5f} N, Lon {well.lon:.5f} E    Profile: {well.traj_type}")
    w.line(f"Spud date: {well.spud_date}    TD: {well.td_md:,.0f} m MD / {well.td_tvd:,.0f} m TVD    Target: {fm_name(well.target)}")
    w.line(f"Mud system: {well.mud_system}")
    w.line()
    w.line("1. FORMATION TOPS", bold=True)
    w.line(f"{'Formation':<18}{'Top MD (m)':>12}{'Top TVD (m)':>13}")
    for code, md in well.tops_md.items():
        w.line(f"{fm_name(code):<18}{md:>12,.1f}{well.tops_tvd[code]:>13,.1f}")
    w.line()
    w.line("2. CASING POLICY", bold=True)
    for s in well.sections:
        w.line(f"{s['hole']} hole: {s['casing']} casing set at {s['shoe_md']:,.0f} m MD")
    w.line()
    w.line("3. MUD PROGRAM", bold=True)
    for s in well.sections:
        w.line(f"{s['hole']} section: MW {s['mw_ppg']:.2f} ppg, ECD {s['ecd_ppg']:.2f} ppg")
    w.line()
    w.line("4. DRILLING COMPLICATIONS", bold=True)
    if not well.events:
        w.line("No major drilling complications were encountered in this well.")
    for e in well.events:
        sents = render_event(e, units, "A", rng)
        w.line("- " + " ".join(sents))
    w.line()
    w.line("5. LESSONS LEARNED AND RECOMMENDATIONS", bold=True)
    if not well.lessons:
        w.line("- Drilling practices as per program were adequate.")
    for l in well.lessons:
        w.line("- " + l["text"])
    w.save(path)
