"""Drilling-report NLP: sentence segmentation, entity & unit extraction, negation /
hypothetical detection, hazard classification (lexicon + ML ensemble), mitigation
and outcome linking. Deterministic and fully offline.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import joblib
import numpy as np

from ..domain.ontology import (FORMATIONS, HAZARDS, HYPOTHETICAL, NEGATION_POST, NEGATION_PRE, OUTCOME_FAIL,
                               OUTCOME_SUCCESS)

FT_TO_M = 0.3048
SG_TO_PPG = 8.345
M3_TO_BBL = 6.2898

# ---------------------------------------------------------------------------
# Quantities & units
# ---------------------------------------------------------------------------
NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
RE_DEPTH = re.compile(NUM + r"\s?(ft|feet|mtrs?|metres|meters|m)(?:\s?(md|tvd))?(?![a-z0-9/³])", re.I)
RE_MW = re.compile(r"(\d{1,2}\.\d{1,3})\s?(ppg|sg|s\.g\.|pcf)\b", re.I)
RE_RATE = re.compile(NUM + r"\s?(bbls?/hr|bbls?/h|bph|m3/hr|m3/h|m³/hr)", re.I)
RE_VOL = re.compile(NUM + r"\s?(bbls?|m3)(?![/a-z])", re.I)
RE_OVERPULL = re.compile(r"(?:overpull|over pull|o/p)\D{0,15}?(\d+(?:\.\d+)?)\s?(klbs?|kips|t|tonnes|tons)\b", re.I)
RE_GAS = re.compile(r"gas[^.%]{0,40}?(\d+(?:\.\d+)?)\s?%|(\d+(?:\.\d+)?)\s?%\s*(?:gas|bgg|cg)", re.I)
RE_NPT = re.compile(r"\bnpt\b[:\s]*(\d+(?:\.\d+)?)\s?(?:hrs?|hours?|h)\b", re.I)
RE_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
RE_WELL = re.compile(r"\bwe[l1i|]{2}\s*(?:name|no\.?)?\s*[:#]\s*([A-Z]{2,5}-\d{1,3}[A-Z]?)", re.I)
RE_TIMELOG = re.compile(r"^\s*(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s+([A-Z][A-Z-]{1,8})\s+(.*)$")
ACTION_START = re.compile(
    r"^\s*(pumped|spotted|reduced|lowered|cut|set|squeezed|placed|pulled|pooh|shut|closed|killed|performed|raised|"
    r"increased|weighted|flow checked|circulated|jarred|worked|backed off|back ?reamed|backreamed|reamed|added|"
    r"treated|controlled|ran|fished|latched|decided|carried out|waited|used|switched|cemented in|plugged|rih|cbu|"
    r"established|string back off|kill mud|cut flow)\b", re.I)


_OCR_NUM = re.compile(r"(?<![A-Za-z])(?=[\dOoIil|,.]*\d[\dOoIil|,.]*\d)[\dOoIil|][\dOoIil|,.]{2,}(?![A-Za-z])")
_OCR_TR = str.maketrans("OoIil|", "001111")


def ocr_fix_numbers(text: str) -> str:
    """Repair typical OCR confusions inside numbers ("3,2i1 m" -> "3,211 m")."""
    return _OCR_NUM.sub(lambda m: m.group(0).translate(_OCR_TR), text)


# ---------------------------------------------------------------------------
# OCR spacing repair (the ONNX OCR model often drops spaces in English text)
# ---------------------------------------------------------------------------
class WordSegmenter:
    """Viterbi word segmentation with a Zipf cost over a domain vocabulary (wordninja-style)."""

    def __init__(self, words: list[str]):
        import math
        n = max(len(words), 2)
        self.cost = {w: math.log((i + 1) * math.log(n)) for i, w in enumerate(words)}
        self.maxlen = max((len(w) for w in words), default=1)

    def split(self, s: str) -> list[str]:
        low = s.lower()
        n = len(low)
        best = [(0.0, 0)] * (n + 1)
        for i in range(1, n + 1):
            cands = []
            for k in range(max(0, i - self.maxlen), i):
                w = low[k:i]
                c = self.cost.get(w, 12.0 + 4.0 * len(w))  # unknown words stay whole rather than shattering
                cands.append((best[k][0] + c, k))
            best[i] = min(cands)
        out, i = [], n
        while i > 0:
            k = best[i][1]
            out.append(s[k:i])
            i = k
        return list(reversed(out))


_SEGMENTER: WordSegmenter | None = None
_RUN = re.compile(r"[A-Za-z]{7,}")


def default_vocabulary() -> list[str]:
    from ..domain.ontology import FORMATIONS, HAZARDS, MITIGATIONS, OUTCOME_FAIL, OUTCOME_SUCCESS
    base = ("the of and to in at with while was were from for on by after before observed losses loss partial total "
            "seepage returns drilling drilled ahead hole pill lcm coarse fine sized pumped spotted pulled shoe allowed "
            "heal continued placed cement plug cure cured full regained npt hrs casing set section mud weight "
            "ecd mw ppg bbl hr stuck pipe string differential sticking pack off jarred jar down up freed came free "
            "tight overpull klbs pooh reamed backreamed back ream wiper trip bit balling balled sticky clay sand "
            "cavings splintery shakers instability unstable fill bottom torque erratic spikes stick slip high kick "
            "influx pit gain shut in well killed kill driller method wait weight gas cut connection background "
            "flow check positive negative circulated circulate sweep hi vis viscosity squeeze remedial top job "
            "cbl bond poor below plan performed carried out cementation lightweight slurry fishing overshot spear "
            "fish recovered backed sidetrack no improvement unsuccessful problem resolved stabilised normalised "
            "increased raised kcl glycol inhibition added gilsonite asphalt lubricant reduced rpm wob controlled rop "
            "encountered experienced suspected unable move rotate could not great during experienced rate "
            "formation tops lessons learned recommendations recommend future wells should keep across pre treating "
            "treat use minimise static time depleted sands overbalance low proved ineffective resolved by lower "
            "sulphonated seams coal contingency ready fractured limestone plan trips anti additive through "
            "report completion structure status producer surface location lat lon profile vertical spud date td "
            "target system program policy liner drill major minor moderate spike complications daily time log "
            "depth size last vis rig remarks progress hours about precautionary anticipated planned risk expect "
            "prognosis kept entering").split()
    words = list(dict.fromkeys(base))
    for f in FORMATIONS:
        words += [w for a in f.aliases for w in a.split()]
    for h in HAZARDS:
        words += [w for t in h.terms for w in re.findall(r"[a-z]+", t)]
    for m in MITIGATIONS:
        words += [w for p in m.phrases for w in re.findall(r"[a-z]+", p)]
    for t in OUTCOME_SUCCESS + OUTCOME_FAIL:
        words += re.findall(r"[a-z]+", t)
    return list(dict.fromkeys(w for w in words if w))


def get_segmenter() -> WordSegmenter:
    global _SEGMENTER
    if _SEGMENTER is None:
        _SEGMENTER = WordSegmenter(default_vocabulary())
    return _SEGMENTER


def repair_ocr_spacing(text: str) -> str:
    """Insert missing spaces in OCR output: split long letter runs, separate numbers from words."""
    seg = get_segmenter()
    text = re.sub(r"(?<=[a-z])1(?=[a-z])|(?<![\w,.])1(?=[a-z]{3,})", "l", text)   # 'I/l' read as '1' inside words
    text = re.sub(r"(?<=[a-z])0(?=[a-z])", "o", text)
    text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Z][a-z])", ". ", text)
    text = re.sub(r"(?<=\d)[lI|](?=\d|m\b|m[A-Za-z]|mMD)", "1", text)
    text = re.sub(r"(?<=[A-Za-z]{2})(?=\d)", " ", text)
    text = re.sub(r"(?<=\d)(m|ft)(?=[a-z]{3,})", r"\1 ", text)
    text = re.sub(r"(?<=\d)(?=[a-z]{4,})", " ", text)
    return _RUN.sub(lambda m: " ".join(seg.split(m.group(0))), text)


def to_float(s: str) -> float:
    return float(s.replace(",", ""))


def parse_depths(text: str) -> list[dict]:
    out = []
    for m in RE_DEPTH.finditer(text):
        v = to_float(m.group(1))
        unit = m.group(2).lower()
        kind = (m.group(3) or "md").lower()
        meters = v * FT_TO_M if unit in ("ft", "feet") else v
        if not (20 <= meters <= 7000):
            continue
        # ignore casing-size like '13-3/8"' and hole sizes (no unit match anyway)
        out.append({"value_m": round(meters, 1), "raw": m.group(0), "kind": kind, "start": m.start(), "end": m.end()})
    return out


def parse_mw(text: str) -> list[float]:
    out = []
    for m in RE_MW.finditer(text):
        v, u = float(m.group(1)), m.group(2).lower()
        ppg = v * SG_TO_PPG if u.startswith("s") else (v / 7.48 if u == "pcf" else v)
        if 7.5 <= ppg <= 20:
            out.append(round(ppg, 2))
    return out


def parse_rate(text: str) -> float | None:
    m = RE_RATE.search(text)
    if not m:
        return None
    v = to_float(m.group(1))
    return round(v * M3_TO_BBL, 1) if m.group(2).lower().startswith("m") else v


def parse_volume(text: str) -> float | None:
    for m in RE_VOL.finditer(text):
        v = to_float(m.group(1))
        return round(v * M3_TO_BBL, 1) if m.group(2).lower() == "m3" else v
    return None


def parse_overpull(text: str) -> float | None:
    m = RE_OVERPULL.search(text)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return round(v * 2.2046, 1) if u in ("t", "tonnes", "tons") else v


def parse_gas(text: str) -> float | None:
    m = RE_GAS.search(text)
    if not m:
        return None
    return float(m.group(1) or m.group(2))


def parse_npt(text: str) -> float | None:
    m = RE_NPT.search(text)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Formations
# ---------------------------------------------------------------------------
_ALIASES = sorted(((a, f.code) for f in FORMATIONS for a in f.aliases), key=lambda t: -len(t[0]))
_ALIAS_RE = [(re.compile(r"\b" + re.escape(a) + r"\b", re.I), code) for a, code in _ALIASES]
_ALIAS_TOKENS = {a: code for a, code in _ALIASES if " " not in a and len(a) >= 5}


def detect_formation(text: str, fuzzy: bool = False) -> str | None:
    for rx, code in _ALIAS_RE:
        if rx.search(text):
            return code
    if fuzzy:  # tolerate OCR errors ("Tlpam", "Barai1")
        for tok in re.findall(r"[A-Za-z0-9]{5,}", text):
            m = difflib.get_close_matches(tok.lower(), list(_ALIAS_TOKENS), n=1, cutoff=0.8)
            if m:
                return _ALIAS_TOKENS[m[0]]
    return None


# ---------------------------------------------------------------------------
# Hazards, negation, hypotheticals
# ---------------------------------------------------------------------------
HAZARD_PRIORITY = ["FISH", "CEMENT", "KICK", "STUCK", "LOSS", "INSTAB", "TIGHT", "TORQUE"]
_EXTRA_TERMS = {
    "LOSS": ("returns dropped", "losing mud", "loss of circulation", "losses"),
    "KICK": ("influx detected", "closed bop", "kick confirmed", "well kicked"),
    "STUCK": ("packing off", "string stuck", "stuck"),
    "TIGHT": ("o/p",),
    "INSTAB": ("hole sloughing", "heavy cavings"),
    "TORQUE": ("torque fluctuation",),
    "CEMENT": ("no cement returns", "poor bond"),
    "FISH": ("fishing assy", "fishing job"),
}
_HAZARD_TERMS: list[tuple[str, str]] = []
for h in HAZARDS:
    for t in set(h.terms) | set(_EXTRA_TERMS.get(h.code, ())):
        _HAZARD_TERMS.append((t.strip(), h.code))
_HAZARD_TERMS.sort(key=lambda t: -len(t[0]))
_HAZARD_RE = [(re.compile(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", re.I), t, code) for t, code in _HAZARD_TERMS]


@dataclass
class HazardHit:
    hazard: str
    term: str
    start: int
    end: int
    negated: bool = False


def find_hazards(text: str) -> list[HazardHit]:
    hits: list[HazardHit] = []
    taken: list[tuple[int, int]] = []
    for rx, term, code in _HAZARD_RE:
        for m in rx.finditer(text):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append(HazardHit(code, term, m.start(), m.end()))
    for h in hits:
        h.negated = is_negated(text, h.start, h.end)
    return hits


def is_negated(text: str, start: int, end: int) -> bool:
    low = text.lower()
    pre = low[max(0, start - 40):start]
    # only look back within the current clause
    pre = re.split(r"[.;:,](?!\d)", pre)[-1] if not re.search(r"(?:no|nil)\s*$", pre) else pre
    for trig in NEGATION_PRE:
        if trig in pre:
            return True
    post = low[end:end + 30]
    post = re.split(r"[.;](?!\d)", post)[0]
    return any(p in post for p in NEGATION_POST)


def is_hypothetical(text: str) -> bool:
    low = text.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(h) + r"(?![a-z])", low) for h in HYPOTHETICAL + ("expect",))


def primary_hazard(hits: list[HazardHit]) -> HazardHit | None:
    pos = [h for h in hits if not h.negated]
    if not pos:
        return None
    return sorted(pos, key=lambda h: (-len(h.term), HAZARD_PRIORITY.index(h.hazard)))[0]


# ---------------------------------------------------------------------------
# Mitigations & outcomes
# ---------------------------------------------------------------------------
MITIGATION_PATTERNS: list[tuple[str, str]] = [
    ("SHUT_IN_DM", r"driller'?s method"),
    ("WAIT_WEIGHT", r"wait\s*(?:and|&)\s*weight"),
    ("RAISE_MW_STAB", r"(?:rais|increas|weight)\w*\s+(?:up\s+)?(?:mud weight|mw|mud)?.{0,25}(?:stabil|caving|hole stability)"),
    ("RAISE_MW", r"(?:rais|increas|weight)\w*\s+(?:up\s+)?(?:mud weight|mw|mud)"),
    ("FLOW_CHECK", r"flow check\w*.{0,30}circulat|gas buster|circulated out gas"),
    ("CEMENT_PLUG", r"cement plug|squeezed cement across|placed cement plug"),
    ("LCM_COARSE", r"coarse|fibr|flake"),
    ("LCM_FINE", r"caco3|calcium carbonate|fine lcm"),
    ("REDUCE_FLOW", r"(?:reduc|lower|cut)\w*\s+(?:the\s+)?(?:flow|pump|spm)"),
    ("POOH_HEAL", r"(?:pull|pooh)\w*.{0,20}shoe|waited on hole|allowed hole to heal"),
    ("JAR_DOWN", r"jar\w*\s+down"),
    ("JAR_UP", r"jar\w*\s+up"),
    ("SPOT_PIPE_LAX", r"pipe[- ]?lax|diesel pill|spotting fluid"),
    ("CIRC_HIVIS", r"hi-?vis|high viscosity sweep"),
    ("BACKOFF", r"back(?:ed)?[- ]?off"),
    ("BACKREAM", r"back ?ream|wiper trip|reamed"),
    ("BIT_CLEAN", r"anti-?balling|detergent|nut plug|clean bit"),
    ("INHIBITION", r"\bkcl\b|glycol|inhibition"),
    ("ASPHALT", r"gilsonite|asphalt"),
    ("CONTROL_ROP", r"control\w*\s+(?:rop|drill)|reduced rop"),
    ("LUBRICANT", r"lubricant"),
    ("REDUCE_PARAMS", r"(?:reduc|lower)\w*\s+(?:rpm|wob|drilling parameters)"),
    ("TOP_JOB", r"top[- ]?(?:up )?(?:cement )?job"),
    ("SQUEEZE", r"squeeze (?:job|cementation)|remedial squeeze|squeezed cement through"),
    ("LIGHT_SLURRY", r"lightweight|two stages|dv tool"),
    ("OVERSHOT", r"overshot"),
    ("SPEAR", r"\bspear\b"),
    ("SIDETRACK", r"side ?track"),
]
_MIT_RE = [(code, re.compile(p, re.I)) for code, p in MITIGATION_PATTERNS]
_SUCCESS = tuple(s.lower() for s in OUTCOME_SUCCESS) + ("ok", "cured", "regained", "came free", "freed", "under control")
_FAIL = tuple(s.lower() for s in OUTCOME_FAIL) + ("no success", "continued", "persisted")


def find_mitigations(text: str) -> list[str]:
    found = []
    for code, rx in _MIT_RE:
        if rx.search(text) and code not in found:
            if code == "RAISE_MW" and "RAISE_MW_STAB" in found:
                continue
            if code in ("LCM_COARSE", "LCM_FINE") and not re.search(r"lcm|pill|caco3|carbonate", text, re.I):
                continue
            found.append(code)
    return found


def find_outcome(text: str) -> str | None:
    low = text.lower().strip().rstrip(".")
    for f in _FAIL:
        if re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", low):
            return "fail"
    for s in _SUCCESS:
        if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", low):
            return "success"
    return None


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------
_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9\"(-])|(?<=[A-Za-z0-9)][.;])(?=[A-Z][a-z]{2,})")


def split_sentences(text: str, base: int = 0) -> list[tuple[int, int, str]]:
    out = []
    pos = 0
    for part in _SPLIT.split(text):
        idx = text.find(part, pos)
        if idx < 0:
            idx = pos
        s = part.strip()
        if s:
            lead = len(part) - len(part.lstrip())
            out.append((base + idx + lead, base + idx + lead + len(s), s))
        pos = idx + len(part)
    return out


# ---------------------------------------------------------------------------
# ML sentence classifier (TF-IDF + logistic regression)
# ---------------------------------------------------------------------------
class SentenceClassifier:
    LABELS = ["LOSS", "KICK", "STUCK", "TIGHT", "INSTAB", "TORQUE", "CEMENT", "FISH", "ACTION", "NONE"]

    def __init__(self):
        self.pipe = None

    def fit(self, texts: list[str], labels: list[str]) -> "SentenceClassifier":
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import FeatureUnion, Pipeline
        norm = [self._norm(t) for t in texts]
        self.pipe = Pipeline([
            ("feats", FeatureUnion([
                ("w", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True)),
            ])),
            ("lr", LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")),
        ])
        self.pipe.fit(norm, labels)
        return self

    @staticmethod
    def _norm(t: str) -> str:
        t = t.lower()
        t = re.sub(r"\d[\d,]*(\.\d+)?", " 0 ", t)
        return t

    def predict_proba(self, texts: list[str]) -> list[dict[str, float]]:
        if self.pipe is None or not texts:
            return [{} for _ in texts]
        P = self.pipe.predict_proba([self._norm(t) for t in texts])
        classes = list(self.pipe.classes_)
        return [{c: float(p[i]) for i, c in enumerate(classes)} for p in P]

    def save(self, path):
        joblib.dump(self.pipe, path)

    @classmethod
    def load(cls, path) -> "SentenceClassifier":
        c = cls()
        try:
            c.pipe = joblib.load(path)
        except Exception:
            c.pipe = None
        return c


# ---------------------------------------------------------------------------
# Sentence analysis
# ---------------------------------------------------------------------------
@dataclass
class SentenceInfo:
    text: str
    page: int
    start: int
    end: int
    record: int
    hazard: str | None = None
    hazard_term: str | None = None
    negated_hazards: list[str] = field(default_factory=list)
    hypothetical: bool = False
    action: bool = False
    mitigations: list[str] = field(default_factory=list)
    outcome: str | None = None
    depths: list[dict] = field(default_factory=list)
    formation: str | None = None
    clf: dict[str, float] = field(default_factory=dict)
    role: str = "other"   # event | action | outcome | negated | hypothetical | lesson | other

    def to_dict(self) -> dict:
        return {"text": self.text, "page": self.page, "start": self.start, "end": self.end, "role": self.role,
                "hazard": self.hazard, "term": self.hazard_term, "negated": self.negated_hazards,
                "hypothetical": self.hypothetical, "mitigations": self.mitigations, "outcome": self.outcome,
                "depths": [d["value_m"] for d in self.depths], "formation": self.formation,
                "clf_top": max(self.clf.items(), key=lambda kv: kv[1])[0] if self.clf else None,
                "clf_p": round(max(self.clf.values()), 3) if self.clf else None}


def analyse_sentence(text: str, page: int, start: int, end: int, record: int, fuzzy: bool = False) -> SentenceInfo:
    si = SentenceInfo(text, page, start, end, record)
    hits = find_hazards(text)
    si.negated_hazards = sorted({h.hazard for h in hits if h.negated})
    ph = primary_hazard(hits)
    si.hypothetical = is_hypothetical(text)
    si.mitigations = find_mitigations(text)
    si.outcome = find_outcome(text)
    si.action = bool(ACTION_START.match(text)) or (bool(si.mitigations) and ph is None)
    si.depths = parse_depths(text)
    si.formation = detect_formation(text, fuzzy=fuzzy)
    if ph:
        si.hazard, si.hazard_term = ph.hazard, ph.term
    return si
