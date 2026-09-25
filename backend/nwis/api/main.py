"""FastAPI application: REST + WebSocket for the NWIS web app (also serves the built frontend)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
import threading
from contextlib import asynccontextmanager
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..correlation import correlation_panel, default_plan, target_from_well
from ..db import DB
from ..domain.ontology import FORMATION_ORDER, HAZARD_BY_CODE, RIBBON_HAZARDS, ontology_payload
from ..geo import haversine_km
from ..ingest.nlp import SentenceClassifier
from ..ingest.ocr import ocr_status
from ..ingest.pipeline import Ingestor
from ..kb import KnowledgeBase
from ..kg import summary_subgraph
from ..llm import llm_status
from ..realtime.analogs import AnalogIndex
from ..realtime.engine import LiveSession
from ..report import hazard_brief
from ..risk.evidence import risk_profile
from ..risk.model import RiskModel
from ..risk.mw_window import mw_window
from ..risk.recommend import recommend
from ..search.index import SearchIndex
from ..search.qa import answer


def _json_default(o):
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if np.isnan(v) else v
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def _clean(o):
    """Replace NaN/inf (not valid JSON) recursively."""
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def J(data, status: int = 200) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(_clean(data), default=_json_default)), status_code=status)


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.db = DB()
        self.kb: KnowledgeBase | None = None
        self.model: RiskModel | None = None
        self._index: SearchIndex | None = None
        self._analogs: AnalogIndex | None = None
        self.clf: SentenceClassifier | None = None
        self.ready = False

    def load(self):
        if not config.DB_PATH.exists():
            return
        self.kb = KnowledgeBase(self.db)
        self.model = RiskModel.load(config.MODELS_DIR / "risk_model.joblib")
        self.clf = SentenceClassifier.load(config.MODELS_DIR / "sentence_clf.joblib")
        self.ready = True

    @property
    def index(self) -> SearchIndex:
        with self.lock:
            if self._index is None:
                self._index = SearchIndex(self.kb)
            return self._index

    @property
    def analogs(self) -> AnalogIndex:
        with self.lock:
            if self._analogs is None:
                self._analogs = AnalogIndex(self.kb)
            return self._analogs

    def refresh(self):
        with self.lock:
            self.kb.refresh()
            self._index = None


S = State()


@asynccontextmanager
async def lifespan(_app):
    S.load()
    if S.ready:  # warm the search + analog indexes in the background
        threading.Thread(target=lambda: (S.index, S.analogs), daemon=True).start()
    yield


app = FastAPI(title="eRTMAC-NWIS", version="0.1.0", lifespan=lifespan,
              description="Nearby Wells Intelligence System - offset-well knowledge & decision support (SIH PS 26121)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def kb() -> KnowledgeBase:
    if not S.ready:
        raise HTTPException(503, "Knowledge base not built. Run: python -m nwis.cli build-demo")
    return S.kb


def _target(well_id: str | None, lat: float | None, lon: float | None, td_formation: str = "SYLHET"):
    k = kb()
    if well_id:
        if well_id not in k.wells:
            raise HTTPException(404, f"unknown well {well_id}")
        return target_from_well(k, well_id)
    if lat is None or lon is None:
        return target_from_well(k, k.active_id)
    return default_plan(k, lat, lon, td_formation, name=f"Planned well @ {lat:.4f}, {lon:.4f}")


# ---------------------------------------------------------------------------- meta
@app.get("/api/health")
def health():
    return {"ok": True, "ready": S.ready}


@app.get("/api/meta")
def meta():
    k = kb()
    return J({"ontology": ontology_payload(), "structures": list(k.structures.values()), "active_well": k.active_id,
              "ocr": ocr_status(), "llm": llm_status(), "build": S.db.kv_get("build_info"), "synthetic": True,
              "formation_order": FORMATION_ORDER, "ribbon_hazards": RIBBON_HAZARDS})


# ---------------------------------------------------------------------------- wells & map
def _well_summary(w, extra: dict | None = None) -> dict:
    cnt = Counter(e["hazard"] for e in w.events)
    return {"id": w.id, "name": w.name, "structure_id": w.structure_id, "lat": w.lat, "lon": w.lon,
            "spud_year": w.spud_year, "td_md": w.td_md, "td_tvd": w.td_tvd, "status": w.status, "traj_type": w.traj_type,
            "target": w.target, "mud_system": w.mud_system, "is_active": w.is_active, "source": w.source,
            "hazard_counts": dict(cnt), "n_events": len(w.events),
            "npt_hours": round(sum(e["npt_hours"] or 0 for e in w.events), 1), **(extra or {})}


@app.get("/api/wells")
def wells():
    k = kb()
    out = []
    for w in k.wells.values():
        if not w.located:
            continue
        out.append(_well_summary(w, {"trajectory": w.trajectory_latlon(step=10)}))
    return J(out)


@app.get("/api/wells/{well_id}")
def well_detail(well_id: str):
    k = kb()
    w = k.wells.get(well_id)
    if not w:
        raise HTTPException(404, "unknown well")
    docs = S.db.query("SELECT id, kind, title, pages, ocr_pages FROM documents WHERE well_id=?", (well_id,))
    return J({**_well_summary(w), "tops": [{"formation": c, "md": w.tops_md[c], "tvd": w.tops_tvd[c]} for c in FORMATION_ORDER if c in w.tops_md],
              "sections": w.sections, "events": w.events, "documents": docs,
              "lessons": [l for l in k.lessons if l["well_id"] == well_id],
              "trajectory": w.trajectory_latlon(step=3)})


@app.get("/api/nearby")
def nearby(lat: float | None = None, lon: float | None = None, well_id: str | None = None, radius_km: float = 8.0):
    k = kb()
    wid = well_id or (k.active_id if lat is None else None)
    if wid:
        w = k.wells[wid]
        lat, lon = w.lat, w.lon
    struct = k.wells[wid].structure_id if wid else k.structure_of(lat, lon)
    rows = []
    for w, d in k.nearby(lat, lon, radius_km, {wid} if wid else None):
        rows.append(_well_summary(w, {"distance_km": round(d, 2), "same_structure": w.structure_id == struct}))
    return J({"center": {"lat": lat, "lon": lon, "well_id": wid, "structure_id": struct}, "radius_km": radius_km,
              "wells": rows})


@app.get("/api/correlation")
def correlation(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 8.0,
                max_wells: int = 8):
    t = _target(well_id, lat, lon)
    return J(correlation_panel(kb(), t, radius_km, max_wells))


# ---------------------------------------------------------------------------- risk
@app.get("/api/risk/profile")
def profile(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 8.0,
            td_formation: str = "SYLHET", bin_m: float = 25.0):
    t = _target(well_id, lat, lon, td_formation)
    p = risk_profile(kb(), t, radius_km, bin_m=bin_m, model=S.model)
    for b in p["bins"]:
        for h in b["hazards"].values():
            h["evidence"] = h["evidence"][:3]
    return J(p)


@app.get("/api/risk/mw-window")
def window(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 10.0,
           td_formation: str = "SYLHET"):
    t = _target(well_id, lat, lon, td_formation)
    w = mw_window(kb(), t, radius_km)
    p = risk_profile(kb(), t, 8.0)
    plan = []
    for code, top in p["tops"].items():
        if top["md"] <= t.td_md and code in w["formations"]:
            sec = t.section_at(top["md"] + 5)
            plan.append({"formation": code, "top_md": top["md"], "top_tvd": top["tvd"], "mw": sec["mw_ppg"], "ecd": sec["ecd_ppg"]})
    w["plan"] = plan
    w["tops"] = p["tops"]
    return J(w)


@app.get("/api/brief", response_class=HTMLResponse)
def brief(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 8.0,
          td_formation: str = "SYLHET"):
    t = _target(well_id, lat, lon, td_formation)
    return HTMLResponse(hazard_brief(kb(), t, radius_km, S.model))


@app.get("/api/recommend")
def recommend_api(hazard: str, formation: str | None = None, well_id: str | None = None, radius_km: float = 8.0):
    k = kb()
    wells = None
    if well_id and well_id in k.wells:
        w = k.wells[well_id]
        wells = {x.id for x, _ in k.nearby(w.lat, w.lon, radius_km, {well_id})}
    return J(recommend(k, hazard, formation, wells))


# ---------------------------------------------------------------------------- knowledge
@app.get("/api/search")
def search(q: str, types: str = "event,lesson,passage", limit: int = 25):
    k = kb()
    center = (k.active.lat, k.active.lon) if k.active else None
    return J(S.index.search(q, tuple(types.split(",")), limit, center))


@app.get("/api/ask")
def ask(q: str):
    k = kb()
    center = (k.active.lat, k.active.lon) if k.active else None
    return J(answer(k, S.index, q, center))


@app.get("/api/kg")
def kg(formation: str | None = None, hazard: str | None = None, well_id: str | None = None, radius_km: float | None = None):
    k = kb()
    wells = None
    if well_id and radius_km and well_id in k.wells:
        w = k.wells[well_id]
        wells = {x.id for x, _ in k.nearby(w.lat, w.lon, radius_km, {well_id})}
    return J(summary_subgraph(k, formation, hazard, wells))


@app.get("/api/events")
def events(hazard: str | None = None, formation: str | None = None, well_id: str | None = None):
    k = kb()
    ev = [e for e in k.events if (hazard is None or e["hazard"] == hazard) and (formation is None or e["formation"] == formation)
          and (well_id is None or e["well_id"] == well_id)]
    return J(ev)


@app.get("/api/lessons")
def lessons(formation: str | None = None, hazard: str | None = None):
    k = kb()
    return J([l for l in k.lessons if (formation is None or l["formation"] == formation) and (hazard is None or l["hazard"] == hazard)])


@app.get("/api/documents")
def documents(well_id: str | None = None):
    if well_id:
        return J(S.db.query("SELECT id, well_id, kind, title, pages, ocr_pages, created, meta FROM documents WHERE well_id=?", (well_id,)))
    return J(S.db.query("SELECT id, well_id, kind, title, pages, ocr_pages, created, meta FROM documents ORDER BY created DESC LIMIT 300"))


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str):
    d = S.db.one("SELECT path FROM documents WHERE id=?", (doc_id,))
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404, "document not found")
    media = "application/pdf" if d["path"].lower().endswith(".pdf") else "application/xml"
    return FileResponse(d["path"], media_type=media)


@app.get("/api/documents/{doc_id}/page/{page_no}")
def document_page(doc_id: str, page_no: int):
    d = S.db.one("SELECT id, well_id, kind, title, pages, ocr_pages FROM documents WHERE id=?", (doc_id,))
    p = S.db.one("SELECT page_no, text, ocr, ocr_conf FROM pages WHERE doc_id=? AND page_no=?", (doc_id, page_no))
    if not d or not p:
        raise HTTPException(404, "page not found")
    return J({"document": d, "page": p})


# ---------------------------------------------------------------------------- ingestion & review
SAMPLES = {"ddr": "NDH-21_DDR_latest.pdf", "scanned": "legacy_scanned_WCR.pdf", "witsml": "drillreport_sample.xml"}


def _ingest_path(path: Path, source: str) -> dict:
    ing = Ingestor(S.db, S.clf)
    if path.suffix.lower() == ".xml":
        res = ing.ingest_witsml(path, source=source)
    else:
        res = ing.ingest_pdf(path, source=source)
    S.refresh()
    return res


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...)):
    kb()
    name = Path(file.filename or "upload.pdf").name
    if not name.lower().endswith((".pdf", ".xml")):
        raise HTTPException(400, "Only PDF (DDR/WCR, text or scanned) and WITSML drillReport XML are supported")
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOADS_DIR / f"{dt.datetime.now():%Y%m%d%H%M%S}_{name}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    res = await asyncio.to_thread(_ingest_path, dest, "upload")
    return J(res)


@app.post("/api/ingest/sample")
async def ingest_sample(kind: str = "ddr"):
    kb()
    src = config.DATA_DIR / "samples" / SAMPLES.get(kind, "")
    if not src.exists():
        raise HTTPException(404, "sample not available")
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOADS_DIR / f"sample_{src.name}"
    shutil.copy(src, dest)
    res = await asyncio.to_thread(_ingest_path, dest, "sample")
    return J(res)


@app.get("/api/review")
def review_list(status: str = "open"):
    rows = S.db.query("SELECT * FROM review_queue WHERE status=? ORDER BY confidence", (status,))
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return J(rows)


@app.post("/api/review/{rid}")
def review_action(rid: str, body: dict):
    r = S.db.one("SELECT * FROM review_queue WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "not found")
    action = body.get("action")
    payload = json.loads(r["payload"])
    if action == "approve":
        S.db.execute("UPDATE events SET status='verified', confidence=MAX(confidence, 0.95) WHERE id=?", (payload["id"],))
        # active learning: the verified sentence becomes a new training example
        fb = S.db.kv_get("verified_sentences", [])
        for c in payload.get("citations", [])[:1]:
            fb.append({"text": c["text"], "label": payload["hazard"]})
        S.db.kv_set("verified_sentences", fb)
    elif action == "reject":
        S.db.execute("UPDATE events SET status='rejected' WHERE id=?", (payload["id"],))
        fb = S.db.kv_get("verified_sentences", [])
        for c in payload.get("citations", [])[:1]:
            fb.append({"text": c["text"], "label": "NONE"})
        S.db.kv_set("verified_sentences", fb)
    else:
        raise HTTPException(400, "action must be approve|reject")
    S.db.execute("UPDATE review_queue SET status=? WHERE id=?", ("approved" if action == "approve" else "rejected", rid))
    S.db.commit()
    S.refresh()
    return {"ok": True}


# ---------------------------------------------------------------------------- analytics & feedback
@app.post("/api/alerts/feedback")
def alert_feedback(body: dict):
    S.db.insert("alert_feedback", {"alert_key": body.get("alert_key"), "hazard": body.get("hazard"),
                                   "useful": int(bool(body.get("useful"))), "note": body.get("note", ""),
                                   "ts": dt.datetime.now().isoformat(timespec="seconds")}, replace=False)
    S.db.commit()
    return {"ok": True}


@app.get("/api/analytics")
def analytics():
    k = kb()
    npt_h = defaultdict(float)
    cnt_h = Counter()
    by_fm = defaultdict(lambda: defaultdict(float))
    by_year = defaultdict(Counter)
    for e in k.events:
        npt_h[e["hazard"]] += e["npt_hours"] or 0
        cnt_h[e["hazard"]] += 1
        if e["formation"]:
            by_fm[e["formation"]][e["hazard"]] += e["npt_hours"] or 0
        w = k.wells.get(e["well_id"])
        if w and w.spud_year:
            by_year[(w.spud_year // 5) * 5][e["hazard"]] += 1
    fb = S.db.query("SELECT hazard, useful, COUNT(*) n FROM alert_feedback GROUP BY hazard, useful")
    inv = {
        "wells": sum(1 for w in k.wells.values() if not w.is_active), "documents": S.db.one("SELECT COUNT(*) n FROM documents")["n"],
        "pages": S.db.one("SELECT COUNT(*) n FROM pages")["n"], "ocr_pages": S.db.one("SELECT COUNT(*) n FROM pages WHERE ocr=1")["n"],
        "events": len(k.events), "lessons": len(k.lessons),
        "review_open": S.db.one("SELECT COUNT(*) n FROM review_queue WHERE status='open'")["n"],
        "citations": S.db.one("SELECT COUNT(*) n FROM citations")["n"],
    }
    return J({"npt_by_hazard": [{"hazard": h, "label": HAZARD_BY_CODE[h].label, "npt_hours": round(npt_h[h], 1),
                                 "events": cnt_h[h]} for h in sorted(npt_h, key=lambda x: -npt_h[x])],
              "npt_by_formation": [{"formation": f, **{h: round(v, 1) for h, v in d.items()}} for f, d in
                                   sorted(by_fm.items(), key=lambda kv: FORMATION_ORDER.index(kv[0]))],
              "events_by_period": [{"period": f"{y}-{y + 4}", **dict(c)} for y, c in sorted(by_year.items())],
              "risk_metrics": S.db.kv_get("risk_metrics"), "extraction_eval": S.db.kv_get("extraction_eval"),
              "feedback": fb, "inventory": inv, "build": S.db.kv_get("build_info")})


# ---------------------------------------------------------------------------- live (eRTMAC stream)
@app.websocket("/ws/live")
async def live(ws: WebSocket):
    await ws.accept()
    if not S.ready:
        await ws.send_json({"type": "error", "message": "knowledge base not built"})
        await ws.close()
        return
    session = await asyncio.to_thread(LiveSession, S.kb, S.model, S.analogs)
    state = {"playing": True, "speed": 4}
    lock = asyncio.Lock()   # the session is not thread-safe: never step it while a jump/reset is running

    def init_msg():
        return {"type": "init", "well": _well_summary(S.kb.active), "episodes": session.episodes,
                "zones": [session._zone_payload(z) | {"evidence": z["evidence"][:6]} for z in session.zones],
                "tops": session.tops, "window": session.win["formations"], "grid": session.win["grid"],
                "status": session.status(), "alerts": session.alerts.snapshot(),
                "ribbon": session.ribbon(0, 6000), "sections": S.kb.active.sections}

    await ws.send_text(json.dumps(_clean(init_msg()), default=_json_default))

    async def reader():
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            if cmd == "play":
                state["playing"] = True
            elif cmd == "pause":
                state["playing"] = False
            elif cmd == "speed":
                state["speed"] = int(max(1, min(60, msg.get("value", 4))))
            elif cmd in ("jump", "restart"):
                async with lock:
                    if cmd == "jump":
                        await asyncio.to_thread(session.jump_to_episode, msg.get("episode"))
                    else:
                        await asyncio.to_thread(session.reset, 0)
                    state["playing"] = True
                    await ws.send_text(json.dumps(_clean(init_msg()), default=_json_default))
            elif cmd == "ack":
                session.alerts.ack(msg.get("id"))
            elif cmd == "analogs":
                await ws.send_text(json.dumps(_clean({"type": "analogs", **session.analog_snapshot()}), default=_json_default))

    async def writer():
        tick = 0
        while True:
            await asyncio.sleep(0.1)
            async with lock:
                if not state["playing"] or session.i >= session.n:
                    if session.alerts.changed:
                        await ws.send_text(json.dumps(_clean({"type": "tick", "samples": [], "alerts": session.alerts.pop_changed(),
                                                              "status": session.status(), "events": []}), default=_json_default))
                    continue
                r = await asyncio.to_thread(session.step, state["speed"])
            tick += 1
            r["type"] = "tick"
            for smp in r["samples"]:
                smp.pop("fm_est", None)
            if tick % 20 == 0 or r["events"]:
                r["ribbon"] = session.ribbon(0, 6000)
                r["zones"] = [session._zone_payload(z) | {"evidence": z["evidence"][:6]} for z in session.zones]
                r["tops"] = session.tops
            await ws.send_text(json.dumps(_clean(r), default=_json_default))

    try:
        await asyncio.gather(reader(), writer())
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---------------------------------------------------------------------------- frontend
if config.FRONTEND_DIST.exists():
    assets = config.FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        f = config.FRONTEND_DIST / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
