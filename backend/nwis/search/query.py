"""Rule-based query understanding (no LLM needed): turns natural language into filters.

"losses in Tipam within 5 km below 2000 m after 2015"  ->
    {hazards:[LOSS], formation:TIPAM, radius_km:5, md_min:2000, year_min:2015}
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..domain.ontology import FORMATIONS, HAZARDS, SEARCH_SYNONYMS
from ..ingest.nlp import detect_formation, find_hazards

HAZARD_WORDS = {
    "LOSS": ("loss", "losses", "lost circulation", "lc", "returns", "seepage", "thief"),
    "KICK": ("kick", "kicks", "influx", "well control", "overpressure", "over pressure", "pit gain", "gas cut"),
    "STUCK": ("stuck", "sticking", "pack-off", "pack off", "differential"),
    "TIGHT": ("tight", "overpull", "balling", "drag", "reaming"),
    "INSTAB": ("instability", "cavings", "caving", "collapse", "sloughing", "unstable"),
    "TORQUE": ("torque", "stick-slip", "stick slip"),
    "CEMENT": ("cement", "cementing", "cbl", "squeeze", "bond"),
    "FISH": ("fishing", "fish", "overshot", "sidetrack", "back off"),
}


@dataclass
class ParsedQuery:
    text: str
    free_text: str
    hazards: list[str] = field(default_factory=list)
    formation: str | None = None
    radius_km: float | None = None
    near_well: str | None = None
    wells: list[str] = field(default_factory=list)
    md_min: float | None = None
    md_max: float | None = None
    year_min: int | None = None
    year_max: int | None = None
    chips: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_query(q: str) -> ParsedQuery:
    pq = ParsedQuery(text=q, free_text=q)
    low = q.lower()
    consumed: list[tuple[int, int]] = []

    def take(m):
        consumed.append((m.start(), m.end()))

    for m in re.finditer(r"(?:within|inside|in a|radius(?: of)?)\s*(\d+(?:\.\d+)?)\s*km(?:\s*radius)?|(\d+(?:\.\d+)?)\s*km\s*(?:radius|around|of)", low):
        pq.radius_km = float(m.group(1) or m.group(2))
        take(m)
    m = re.search(r"between\s*([\d,]+)\s*(?:m|ft)?\s*(?:and|-|to)\s*([\d,]+)\s*(m|ft)?", low)
    if m:
        a, b = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        f = 0.3048 if (m.group(3) == "ft") else 1.0
        pq.md_min, pq.md_max = min(a, b) * f, max(a, b) * f
        take(m)
    for m in re.finditer(r"(below|deeper than|beyond|after|>)\s*([\d,]{3,})\s*(m|ft|meters|metres)\b", low):
        f = 0.3048 if m.group(3) == "ft" else 1.0
        pq.md_min = float(m.group(2).replace(",", "")) * f
        take(m)
    for m in re.finditer(r"(above|shallower than|before|<)\s*([\d,]{3,})\s*(m|ft|meters|metres)\b", low):
        f = 0.3048 if m.group(3) == "ft" else 1.0
        pq.md_max = float(m.group(2).replace(",", "")) * f
        take(m)
    m = re.search(r"(?:at|around|near)\s*([\d,]{3,})\s*(m|ft)\b", low)
    if m and pq.md_min is None and pq.md_max is None:
        f = 0.3048 if m.group(2) == "ft" else 1.0
        v = float(m.group(1).replace(",", "")) * f
        pq.md_min, pq.md_max = v - 100, v + 100
        take(m)
    for m in re.finditer(r"(after|since|from)\s*((?:19|20)\d{2})\b", low):
        pq.year_min = int(m.group(2))
        take(m)
    for m in re.finditer(r"(before|until|prior to)\s*((?:19|20)\d{2})\b", low):
        pq.year_max = int(m.group(2))
        take(m)
    m = re.search(r"\bin\s*((?:19|20)\d{2})\b", low)
    if m and pq.year_min is None and pq.year_max is None:
        pq.year_min = pq.year_max = int(m.group(1))
        take(m)
    for m in re.finditer(r"\b([A-Z]{3}-\d{2})\b", q.upper()):
        pq.wells.append(m.group(1))
        take(m)
    m = re.search(r"(?:near|around|offsets? of|close to)\s+([A-Z]{3}-\d{2})", q.upper(), re.I)
    if m:
        pq.near_well = m.group(1)
        pq.wells = [w for w in pq.wells if w != pq.near_well]
    pq.formation = detect_formation(q)
    for code, words in HAZARD_WORDS.items():
        for w in words:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
                if code not in pq.hazards:
                    pq.hazards.append(code)
                break
    # free text = query without consumed numeric filters
    keep = []
    for i, ch in enumerate(q):
        if not any(a <= i < b for a, b in consumed):
            keep.append(ch)
    pq.free_text = re.sub(r"\s+", " ", "".join(keep)).strip()
    from ..domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE
    for h in pq.hazards:
        pq.chips.append({"kind": "hazard", "value": h, "label": HAZARD_BY_CODE[h].label})
    if pq.formation:
        pq.chips.append({"kind": "formation", "value": pq.formation, "label": FORMATION_BY_CODE[pq.formation].name})
    if pq.radius_km:
        pq.chips.append({"kind": "radius_km", "value": pq.radius_km, "label": f"within {pq.radius_km:g} km"})
    if pq.near_well:
        pq.chips.append({"kind": "near_well", "value": pq.near_well, "label": f"near {pq.near_well}"})
    for w in pq.wells:
        pq.chips.append({"kind": "well", "value": w, "label": w})
    if pq.md_min is not None or pq.md_max is not None:
        lab = (f"{pq.md_min:,.0f}" if pq.md_min is not None else "0") + "–" + (f"{pq.md_max:,.0f}" if pq.md_max is not None else "TD") + " m"
        pq.chips.append({"kind": "depth", "value": [pq.md_min, pq.md_max], "label": lab})
    if pq.year_min or pq.year_max:
        pq.chips.append({"kind": "year", "value": [pq.year_min, pq.year_max],
                         "label": f"{pq.year_min or '…'}–{pq.year_max or '…'}"})
    return pq
