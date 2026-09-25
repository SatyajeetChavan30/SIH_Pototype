"""In-memory view of the knowledge base (fast spatial / formation queries)."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

import numpy as np

from . import config
from .data.synth import thrust_distance_km
from .domain.ontology import IS_ASSAM
from .db import DB
from .domain.ontology import FORMATION_ORDER
from .geo import Trajectory, haversine_km, offset_latlon


@dataclass
class WellRec:
    id: str
    name: str
    structure_id: str
    lat: float | None
    lon: float | None
    spud_year: int | None
    spud_date: str | None
    td_md: float | None
    td_tvd: float | None
    status: str | None
    traj_type: str | None
    target: str | None
    mud_system: str | None
    is_active: bool
    tops_md: dict[str, float] = field(default_factory=dict)
    tops_tvd: dict[str, float] = field(default_factory=dict)
    traj: Trajectory | None = None
    sections: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    source: str = "synthetic"

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    def section_at(self, md: float) -> dict | None:
        for s in self.sections:
            if s["top_md"] <= md <= s["shoe_md"] + 1e-6:
                return s
        return self.sections[-1] if self.sections else None

    def formation_span(self, code: str, thick: dict[str, float]) -> float:
        """Relative fraction of `code` penetrated (0..1)."""
        if code not in self.tops_tvd or self.td_tvd is None:
            return 0.0
        i = FORMATION_ORDER.index(code)
        nxt = FORMATION_ORDER[i + 1] if i + 1 < len(FORMATION_ORDER) else None
        top = self.tops_tvd[code]
        base = self.tops_tvd.get(nxt) if nxt else None
        if base is None:
            base = top + thick.get(code, 350.0)
        return float(np.clip((self.td_tvd - top) / max(base - top, 1), 0, 1))

    def trajectory_latlon(self, step: int = 5) -> list[list[float]]:
        if self.traj is None or not self.located:
            return []
        pts = []
        for i in range(0, len(self.traj.md), step):
            la, lo = offset_latlon(self.lat, self.lon, float(self.traj.north[i]), float(self.traj.east[i]))
            pts.append([round(la, 6), round(lo, 6)])
        return pts


class KnowledgeBase:
    def __init__(self, db: DB | None = None):
        self.db = db or DB()
        self.lock = threading.RLock()
        self.refresh()

    def refresh(self) -> None:
        with self.lock:
            db = self.db
            self.structures = {s["id"]: s for s in db.query("SELECT * FROM structures")}
            wells = {}
            for w in db.query("SELECT * FROM wells"):
                wells[w["id"]] = WellRec(
                    id=w["id"], name=w["name"], structure_id=w["structure_id"], lat=w["lat"], lon=w["lon"],
                    spud_year=w["spud_year"], spud_date=w["spud_date"], td_md=w["td_md"], td_tvd=w["td_tvd"],
                    status=w["status"], traj_type=w["traj_type"], target=w["target"], mud_system=w["mud_system"],
                    is_active=bool(w["is_active"]), source=w.get("source") or "synthetic")
            for t in db.query("SELECT * FROM tops"):
                if t["well_id"] in wells:
                    wells[t["well_id"]].tops_md[t["formation"]] = t["md"]
                    wells[t["well_id"]].tops_tvd[t["formation"]] = t["tvd"]
            sv: dict[str, list] = {}
            for s in db.query("SELECT well_id, md, inc, azi FROM surveys ORDER BY well_id, md"):
                sv.setdefault(s["well_id"], []).append((s["md"], s["inc"], s["azi"]))
            for wid, rows in sv.items():
                if wid in wells and len(rows) > 1:
                    a = np.array(rows)
                    wells[wid].traj = Trajectory(a[:, 0], a[:, 1], a[:, 2])
            for s in db.query("SELECT * FROM sections ORDER BY well_id, idx"):
                if s["well_id"] in wells:
                    wells[s["well_id"]].sections.append(s)
            actions: dict[str, list] = {}
            for a in db.query("SELECT * FROM event_actions ORDER BY event_id, seq"):
                actions.setdefault(a["event_id"], []).append({"code": a["code"], "success": bool(a["success"]),
                                                              "text": a["text"]})
            cits: dict[str, list] = {}
            for c in db.query("SELECT c.*, d.title, d.kind FROM citations c LEFT JOIN documents d ON d.id=c.doc_id"):
                cits.setdefault(c["event_id"], []).append(c)
            self.events = []
            for e in db.query("SELECT * FROM events WHERE status IN ('auto','verified') ORDER BY well_id, md"):
                e["extra"] = json.loads(e["extra"]) if e.get("extra") else {}
                e["actions"] = actions.get(e["id"], [])
                e["citations"] = cits.get(e["id"], [])
                self.events.append(e)
                if e["well_id"] in wells:
                    wells[e["well_id"]].events.append(e)
            self.wells = wells
            self.lessons = db.query("SELECT l.*, d.title AS doc_title FROM lessons l LEFT JOIN documents d ON d.id=l.doc_id")
            self.active_id = db.kv_get("active_well")
            # typical formation thickness (TVD) across the basin
            th: dict[str, list[float]] = {}
            for w in wells.values():
                for i, c in enumerate(FORMATION_ORDER[:-1]):
                    n = FORMATION_ORDER[i + 1]
                    if c in w.tops_tvd and n in w.tops_tvd:
                        th.setdefault(c, []).append(w.tops_tvd[n] - w.tops_tvd[c])
            self.thick = {c: float(np.median(v)) for c, v in th.items()}
            for c in FORMATION_ORDER:
                self.thick.setdefault(c, 350.0)

    # ------------------------------------------------------------------ queries
    @property
    def active(self) -> WellRec | None:
        return self.wells.get(self.active_id) if self.active_id else None

    def offsets(self, exclude: set[str] | None = None, before_year: int | None = None) -> list[WellRec]:
        ex = exclude or set()
        out = []
        for w in self.wells.values():
            if w.is_active or w.id in ex or not w.located or not w.tops_tvd:
                continue
            if before_year is not None and (w.spud_year or 0) >= before_year:
                continue
            out.append(w)
        return out

    def nearby(self, lat: float, lon: float, radius_km: float, exclude: set[str] | None = None,
               before_year: int | None = None) -> list[tuple[WellRec, float]]:
        offs = self.offsets(exclude, before_year)
        if not offs:
            return []
        d = haversine_km(lat, lon, np.array([w.lat for w in offs]), np.array([w.lon for w in offs]))
        pairs = [(w, float(x)) for w, x in zip(offs, d) if x <= radius_km]
        return sorted(pairs, key=lambda p: p[1])

    def structure_of(self, lat: float, lon: float) -> str | None:
        if not self.structures:
            return None
        best = min(self.structures.values(), key=lambda s: float(haversine_km(lat, lon, s["lat"], s["lon"])))
        return best["id"] if float(haversine_km(lat, lon, best["lat"], best["lon"])) < 8 else None

    @staticmethod
    def thrust_km(lat: float, lon: float) -> float:
        # distance to the Naga thrust front is an Upper-Assam feature; other regions get a neutral constant
        return thrust_distance_km(lat, lon) if IS_ASSAM else 0.0


_KB: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


def reset_kb() -> None:
    global _KB
    _KB = None
