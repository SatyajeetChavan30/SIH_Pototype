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
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import audit, auth, config, ops
from ..correlation import correlation_panel, default_plan, target_from_well
from ..db import DB
from ..domain.ontology import REGION as ONTOLOGY_REGION
from ..domain.ontology import DEFAULT_TD, FORMATION_ORDER, HAZARD_BY_CODE, RIBBON_HAZARDS, ontology_payload
from ..geo import haversine_km
from ..ingest.nlp import SentenceClassifier
from ..ingest.ocr import ocr_status
from ..ingest.pipeline import Ingestor
from ..kb import KnowledgeBase
from ..kg import summary_subgraph
from ..llm import llm_status
from ..ingest.voice import asr_status, transcribe
from ..jobs import JOBS, Job
from ..memory import after_action_review, approve_aar, handover_brief
from ..realtime.analogs import AnalogIndex
from ..realtime.engine import LiveSession
from ..realtime.hub import LiveHub, init_payload, tick_payload
from ..realtime.sources import from_spec
from ..report import hazard_brief
from ..risk.evidence import risk_profile
from ..risk.model import RiskModel
from ..risk.mw_window import mw_window
from ..risk.recommend import recommend
from ..risk.whatif import run_whatif
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
        self.index_lock = threading.Lock()
        self.analog_lock = threading.Lock()
        self.db = DB()
        self.kb: KnowledgeBase | None = None
        self.model: RiskModel | None = None
        self._index: SearchIndex | None = None
        self._analogs: AnalogIndex | None = None
        self.clf: SentenceClassifier | None = None
        self.hub: LiveHub | None = None
        self.hub_task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = False
        self.load_error: str | None = None

    def load(self):
        if not config.DB_PATH.exists():
            return
        try:
            self.db.init()   # CREATE IF NOT EXISTS: upgrades older demo databases with new tables (decision_log)
            auth.ensure_demo_users(self.db)
            self.kb = KnowledgeBase(self.db)
            self.model = RiskModel.load(config.MODELS_DIR / "risk_model.joblib")
            self.clf = SentenceClassifier.load(config.MODELS_DIR / "sentence_clf.joblib")
        except Exception as e:  # noqa: BLE001 - a broken or half-built base: offer a rebuild in the page instead
            self.load_error = f"{type(e).__name__}: {e}"[:300]
            return
        self._index = self._analogs = None
        self.load_error = None
        self.ready = True

    async def start_services(self):
        """Warm the search + analog indexes in the background and attach the configured rig feed."""
        threading.Thread(target=lambda: (self.analogs, self.index), daemon=True).start()   # live consoles need analogs first
        try:
            await self.set_stream(config.STREAM)
        except Exception as e:  # noqa: BLE001 - a bad feed setting must not stop the dashboard from starting
            print(f"[nwis] live feed '{config.STREAM}' not started: {e}", flush=True)

    async def stop_hub(self):
        hub, task = self.hub, self.hub_task
        self.hub, self.hub_task = None, None
        if hub is not None:
            hub.broadcast({"type": "reconnect"})   # consoles on the old feed reconnect to whatever replaces it
            await asyncio.sleep(0.2)
            hub.source.stop()
        if task is not None:
            task.cancel()

    async def set_stream(self, spec: str):
        """Replace the live rig feed while the server runs ('replay' = no shared feed)."""
        src = from_spec(spec)        # raises ValueError on a malformed setting, before anything is stopped
        await self.stop_hub()
        config.STREAM = spec
        if src is not None and self.ready:   # a real rig feed: one shared session for every console
            self.hub = await asyncio.to_thread(LiveHub, self.kb, self.model, src,
                                               lambda rows: audit.append_many(self.db, rows), lambda: self._analogs)
            self.hub_task = asyncio.create_task(self.hub.run())

    # each index has its own lock: a search waiting for the (slow) search index must not hold up live consoles
    @property
    def index(self) -> SearchIndex:
        with self.index_lock:
            if self._index is None:
                self._index = SearchIndex(self.kb)
            return self._index

    @property
    def analogs(self) -> AnalogIndex:
        with self.analog_lock:
            if self._analogs is None:
                self._analogs = AnalogIndex(self.kb)
            return self._analogs

    def refresh(self):
        with self.lock:
            self.kb.refresh()
            with self.index_lock:
                self._index = None


S = State()


@asynccontextmanager
async def lifespan(_app):
    ops.promote_all()   # a rebuild finished in the dashboard is swapped in before anything opens the database
    S.loop = asyncio.get_running_loop()
    S.load()
    if S.ready:
        await S.start_services()
    yield
    ops.SIM.stop_evt.set()
    await S.stop_hub()


app = FastAPI(title="eRTMAC-NWIS", version="0.1.0", lifespan=lifespan,
              description="Nearby Wells Intelligence System - offset-well knowledge & decision support (SIH PS 26121)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def access_control(request: Request, call_next):
    """Every /api/* request is checked against auth.RULES; the UI shell and assets stay public."""
    request.state.user = None
    path = request.url.path
    if not path.startswith("/api/") or not auth.enabled() or not S.ready:
        return await call_next(request)
    user = auth.user_from_token(S.db, request.cookies.get(auth.COOKIE))
    request.state.user = user
    if not auth.can(user, request.method, path):
        if user is None:
            return JSONResponse({"detail": "sign in required"}, status_code=401)
        return JSONResponse({"detail": f"a {user['role']} account cannot do this"}, status_code=403)
    return await call_next(request)


def _actor(request: Request, claimed: str | None, default: str = "RTOC", n: int = 40) -> str:
    """Who did it: the signed-in user when sign-in is on, otherwise the name the client sent."""
    user = getattr(request.state, "user", None)
    if user:
        return user["display_name"][:n]
    return (str(claimed or "").strip() or default)[:n]


NOT_BUILT = "The knowledge base is not built yet. Open NWIS in the browser and choose 'Build knowledge base'."


def kb() -> KnowledgeBase:
    if not S.ready:
        raise HTTPException(503, NOT_BUILT)
    return S.kb


def _target(well_id: str | None, lat: float | None, lon: float | None, td_formation: str = DEFAULT_TD):
    k = kb()
    if well_id:
        if well_id not in k.wells:
            raise HTTPException(404, f"unknown well {well_id}")
        return target_from_well(k, well_id)
    if lat is None or lon is None:
        return target_from_well(k, k.active_id)
    return default_plan(k, lat, lon, td_formation, name=f"Planned well @ {lat:.4f}, {lon:.4f}")


# ---------------------------------------------------------------------------- sign-in & users
@app.post("/api/auth/login")
def login(body: dict, response: Response):
    if not S.ready:
        raise HTTPException(503, NOT_BUILT)
    user = auth.authenticate(S.db, body.get("username") or "", body.get("password") or "")
    if user is None:
        raise HTTPException(401, "wrong username or password")
    response.set_cookie(auth.COOKIE, auth.issue_token(S.db, user), max_age=auth.SESSION_S, httponly=True,
                        samesite="lax")
    audit.append_many(S.db, [audit.make_row("sign_in", actor=user["display_name"][:40],
                                            payload={"username": user["username"], "role": user["role"]})])
    return {"user": user}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = auth.user_from_token(S.db, request.cookies.get(auth.COOKIE)) if S.ready and auth.enabled() else None
    return {"auth": auth.enabled(), "user": user,
            "demo_users": [{"username": u, "role": r} for u, _, r in auth.DEMO_USERS] if auth.enabled() else []}


@app.get("/api/users")
def users():
    return auth.list_users(S.db)


@app.post("/api/users")
def add_user(body: dict):
    try:
        return auth.create_user(S.db, body.get("username") or "", body.get("display_name") or "",
                                body.get("role") or "", body.get("password") or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ---------------------------------------------------------------------------- meta
@app.get("/api/health")
def health():
    return {"ok": True, "ready": S.ready, "boot": ops.BOOT_ID}


@app.get("/api/meta")
def meta():
    k = kb()
    return J({"ontology": ontology_payload(), "structures": list(k.structures.values()), "active_well": k.active_id,
              "ocr": ocr_status(), "llm": llm_status(), "asr": asr_status(), "build": S.db.kv_get("build_info"),
              "synthetic": ONTOLOGY_REGION["synthetic"], "top_pick_mode": config.TOP_PICK_MODE,
              "map_tiles": {"url": config.TILE_URL, "attribution": config.TILE_ATTRIBUTION},
              "stream": {"live_available": S.hub is not None, "spec": config.STREAM,
                         "describe": S.hub.source.describe() if S.hub else "stored replay"},
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


@app.get("/api/wells/{well_id:path}")   # public well names contain "/" (e.g. 15/9-19 A)
def well_detail(well_id: str):
    k = kb()
    w = k.wells.get(well_id)
    if not w:
        raise HTTPException(404, "unknown well")
    docs = S.db.query("SELECT id, kind, title, pages, ocr_pages FROM documents WHERE well_id=?", (well_id,))
    return J({**_well_summary(w), "tops": [{"formation": c, "md": w.tops_md[c], "tvd": w.tops_tvd[c]} for c in FORMATION_ORDER if c in w.tops_md],
              "sections": w.sections, "events": w.events, "documents": docs,
              "lessons": [l for l in k.lessons if l["well_id"] == well_id],
              "lot_tests": S.db.query("SELECT depth_md, casing, emw_ppg, test_type FROM lot_tests WHERE well_id=? "
                                      "ORDER BY depth_md", (well_id,)),
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
            td_formation: str = DEFAULT_TD, bin_m: float = 25.0):
    t = _target(well_id, lat, lon, td_formation)
    p = risk_profile(kb(), t, radius_km, bin_m=bin_m, model=S.model)
    for b in p["bins"]:
        for h in b["hazards"].values():
            h["evidence"] = h["evidence"][:3]
    return J(p)


@app.get("/api/risk/mw-window")
def window(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 10.0,
           td_formation: str = DEFAULT_TD):
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


@app.post("/api/risk/whatif")
def whatif(body: dict):
    t = _target(body.get("well_id"), body.get("lat"), body.get("lon"), body.get("td_formation") or DEFAULT_TD)
    return J(run_whatif(kb(), t, body.get("overrides") or {}, S.model, float(body.get("radius_km") or 8.0)))


@app.get("/api/brief", response_class=HTMLResponse)
def brief(well_id: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 8.0,
          td_formation: str = DEFAULT_TD):
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


@app.post("/api/memo")
async def memo(body: dict, request: Request):
    kb()
    text = (body.get("text") or "").strip()
    author = _actor(request, body.get("author"), default="", n=60)
    if len(text) < 20 or not author:
        raise HTTPException(400, "a memo needs an author and at least a sentence of text")
    title = (body.get("title") or f"Expert memo — {author}").strip()[:120]

    def run():
        res = Ingestor(S.db, S.clf).ingest_text(text, title, author[:60], body.get("well_id") or None,
                                                body.get("lang") or "en", body.get("original"))
        S.refresh()
        return res
    return J(await asyncio.to_thread(run))


@app.post("/api/memo/audio")
async def memo_audio(request: Request, file: UploadFile = File(...), author: str = "", well_id: str = "",
                     lang: str = ""):
    kb()
    author = _actor(request, author, default="", n=60)
    if not author.strip():
        raise HTTPException(400, "author is required")
    st = asr_status()
    if not st["available"]:
        raise HTTPException(501, st["hint"])
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOADS_DIR / f"{dt.datetime.now():%Y%m%d%H%M%S}_{Path(file.filename or 'memo.webm').name}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tr = await asyncio.to_thread(transcribe, dest, lang or None)

    def run():
        res = Ingestor(S.db, S.clf).ingest_text(tr["english"], f"Voice memo — {author.strip()[:60]}", author.strip()[:60],
                                                well_id or None, tr["language"], tr["original"], source="voice-memo")
        S.refresh()
        return res
    return J({"transcript": tr, **(await asyncio.to_thread(run))})


@app.get("/api/aar/{event_id:path}")
def aar(event_id: str):
    r = after_action_review(kb(), event_id)
    if r is None:
        raise HTTPException(404, "unknown event")
    return J(r)


@app.post("/api/aar/{event_id:path}/approve")
def aar_approve(event_id: str, body: dict, request: Request):
    k = kb()
    text = (body.get("text") or "").strip()
    if len(text) < 20:
        raise HTTPException(400, "lesson text too short")
    try:
        res = approve_aar(k, event_id, text, _actor(request, body.get("reviewer"), n=60))
    except KeyError:
        raise HTTPException(404, "unknown event") from None
    S.refresh()
    return res


@app.get("/api/review")
def review_list(status: str = "open"):
    rows = S.db.query("SELECT * FROM review_queue WHERE status=? ORDER BY confidence", (status,))
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return J(rows)


@app.post("/api/review/{rid:path}")
def review_action(rid: str, body: dict, request: Request):
    r = S.db.one("SELECT * FROM review_queue WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "not found")
    action = body.get("action")
    payload = json.loads(r["payload"])
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve|reject")
    if r["kind"] == "lesson":
        # expert-memo lesson: approval publishes it as a cited lesson credited to its author
        if action == "approve":
            who = payload.get("author")
            S.db.insert("lessons", {"id": payload["id"], "well_id": payload.get("well_id"), "hazard": payload.get("hazard"),
                                    "formation": payload.get("formation"),
                                    "text": payload["text"] + (f" (expert memo: {who})" if who else ""),
                                    "doc_id": payload["doc_id"], "page_no": payload.get("page_no"),
                                    "start": payload.get("start"), "end": payload.get("end")})
    elif action == "approve":
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
    S.db.execute("UPDATE review_queue SET status=? WHERE id=?", ("approved" if action == "approve" else "rejected", rid))
    audit.append_many(S.db, [audit.make_row("review", well_id=payload.get("well_id"), actor=_actor(request, body.get("reviewer")),
                                            payload={"review_id": rid, "kind": r["kind"], "action": action,
                                                     "hazard": payload.get("hazard")})])
    if r["doc_id"] and not S.db.one("SELECT id FROM review_queue WHERE doc_id=? AND status='open'", (r["doc_id"],)):
        S.db.execute("UPDATE documents SET status='ingested' WHERE id=? AND kind='MEMO'", (r["doc_id"],))
    S.db.commit()
    S.refresh()
    return {"ok": True}


# ---------------------------------------------------------------------------- analytics & feedback
@app.get("/api/stream/status")
def stream_status():
    if S.hub is None:
        return J({"mode": "replay", "spec": config.STREAM})
    st = S.hub.session.status()
    return J({"mode": "live", "spec": config.STREAM, "in_gap": S.hub.in_gap, "samples": S.hub.session.n,
              "bit_md": st["md"], "subscribers": len(S.hub.subs), **(st.get("stream") or {})})


FEEDBACK_VERDICTS = ("useful", "false_alarm", "not_actionable")


@app.post("/api/alerts/feedback")
def alert_feedback(body: dict, request: Request):
    verdict = body.get("verdict") or ("useful" if body.get("useful") else "false_alarm")
    if verdict not in FEEDBACK_VERDICTS:
        raise HTTPException(400, f"verdict must be one of {FEEDBACK_VERDICTS}")
    S.db.insert("alert_feedback", {"alert_key": body.get("alert_key"), "hazard": body.get("hazard"),
                                   "useful": int(verdict == "useful"), "note": body.get("note", ""),
                                   "ts": dt.datetime.now().isoformat(timespec="seconds")}, replace=False)
    S.db.commit()
    alert = {"id": body.get("alert_id"), "key": body.get("alert_key"), "hazard": body.get("hazard"),
             "level": body.get("level")}
    audit.append_many(S.db, [audit.make_row("feedback", session_id=body.get("session_id"),
                                            well_id=S.kb.active_id if S.kb else None, t=body.get("t"), md=body.get("md"),
                                            alert=alert, actor=_actor(request, body.get("actor")),
                                            payload={"verdict": verdict, "note": body.get("note", "")})])
    return {"ok": True}


@app.get("/api/audit")
def audit_log(session_id: str | None = None, alert_id: str | None = None, alert_key: str | None = None, limit: int = 200):
    return J({"rows": audit.query(S.db, session_id, alert_id, alert_key, min(limit, 2000))})


@app.get("/api/audit/verify")
def audit_verify():
    return J(audit.summary(S.db))


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
              "risk_metrics": S.db.kv_get("risk_metrics"), "extraction_eval": S.db.kv_get("extraction_eval"), "ocr_eval": S.db.kv_get("ocr_eval"),
              "feedback": fb, "inventory": inv, "build": S.db.kv_get("build_info"), "audit": audit.summary(S.db),
              "live_eval": S.db.kv_get("live_eval"), "public_eval": S.db.kv_get("public_eval")})


# ---------------------------------------------------------------------------- live (eRTMAC stream)
@app.websocket("/ws/live")
async def live(ws: WebSocket):
    await ws.accept()
    if not S.ready:
        await ws.send_json({"type": "error", "message": "knowledge base not built"})
        await ws.close()
        return
    user = auth.user_from_token(S.db, ws.cookies.get(auth.COOKIE)) if auth.enabled() else None
    if auth.enabled() and user is None:
        await ws.send_json({"type": "error", "message": "sign in required"})
        await ws.close(code=4401)
        return
    ws.state.user = user
    if ws.query_params.get("mode") == "live" and S.hub is not None:
        await _live_feed(ws, S.hub)
        return
    if not (config.LOGS_DIR / "active_stream.npz").exists() or S.kb.active is None:
        # e.g. the real public-data region: there is no public real-time stream to replay
        await ws.send_json({"type": "error", "code": "no_stream",
                            "message": "No real-time stream in this dataset. An administrator can connect a WITS-0 / "
                                       "WITSML rig feed under System → Live rig feed, or switch to the Assam demo "
                                       "for the replay."})
        await ws.close()
        return
    # S.analogs may still be building at start-up: wait for it off the event loop, never on it
    session = await asyncio.to_thread(lambda: LiveSession(S.kb, S.model, S.analogs))
    state = {"playing": True, "speed": 4}
    lock = asyncio.Lock()   # the session is not thread-safe: never step it while a jump/reset is running

    def flush_audit():
        rows = session.pop_audit()
        if rows:
            audit.append_many(S.db, rows)

    def send(msg: dict):
        return ws.send_text(json.dumps(_clean(msg), default=_json_default))

    await send(init_payload(session, _well_summary(S.kb.active), S.kb.active.sections))

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
                    flush_audit()
                    await send(init_payload(session, _well_summary(S.kb.active), S.kb.active.sections))
            else:
                async with lock:
                    reply = await _common_command(session, msg, user)
                    flush_audit()
                if reply:
                    await send(reply)

    async def writer():
        tick = 0
        while True:
            await asyncio.sleep(0.1)
            async with lock:
                if not state["playing"] or session.i >= session.n:
                    if session.alerts.changed:
                        await send({"type": "tick", "samples": [], "alerts": session.alerts.pop_changed(),
                                    "status": session.status(), "events": []})
                    continue
                r = await asyncio.to_thread(session.step, state["speed"])
                flush_audit()
            tick += 1
            await send(tick_payload(session, r, full=tick % 20 == 0))

    try:
        await asyncio.gather(reader(), writer())
    except (WebSocketDisconnect, RuntimeError):
        pass


def _ack_extra(msg: dict) -> dict:
    """Acknowledgements queued on an offline rig tablet carry when they were really made."""
    extra = {}
    if msg.get("acted_at"):
        extra["acted_at"] = str(msg["acted_at"])[:40]
    if msg.get("queued_offline"):
        extra["queued_offline"] = True
    return extra


async def _common_command(session: LiveSession, msg: dict, user: dict | None = None) -> dict | None:
    """Commands shared by replay and live consoles. Caller holds the session lock."""
    cmd = msg.get("cmd")
    # with sign-in on, the decision log names the signed-in person, never what the browser claims
    actor = user["display_name"][:40] if user else (str(msg.get("actor") or "").strip() or "RTOC")[:40]
    if cmd == "ack":
        session.ack(msg.get("id"), actor, key=msg.get("key"), **_ack_extra(msg))
        return {"type": "tick", "samples": [], "alerts": session.alerts.pop_changed(), "status": session.status(),
                "events": []}
    if cmd == "budget":
        session.set_budget(float(msg.get("value", 1.0)), actor)
        return {"type": "tick", "samples": [], "alerts": [], "status": session.status(), "events": []}
    if cmd == "handover":
        return {"type": "handover", **await asyncio.to_thread(handover_brief, session, float(msg.get("hours", 12)))}
    if cmd == "analogs":
        return {"type": "analogs", **session.analog_snapshot()}
    return None


async def _live_feed(ws: WebSocket, hub: LiveHub) -> None:
    """A console attached to the shared live session. Play/pause/speed/jump do not exist on a real feed."""
    q = hub.subscribe()

    def send(msg: dict):
        return ws.send_text(json.dumps(_clean(msg), default=_json_default))

    async with hub.lock:
        await send(init_payload(hub.session, _well_summary(S.kb.active), S.kb.active.sections))

    async def reader():
        while True:
            msg = json.loads(await ws.receive_text())
            async with hub.lock:
                reply = await _common_command(hub.session, msg, ws.state.user)
                audit.append_many(S.db, hub.session.pop_audit())
            if reply:
                if reply["type"] == "tick":
                    hub.broadcast(reply)          # an acknowledgement is visible on every console
                else:
                    await send(reply)

    async def writer():
        while True:
            msg = await q.get()
            if msg.get("type") == "reconnect":   # the feed was replaced from the dashboard
                await ws.close(code=4000)
                return
            await send(msg)

    try:
        await asyncio.gather(reader(), writer())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(q)


# ---------------------------------------------------------------------------- setup & operations (dashboard-driven)
def _job_or_409(start):
    try:
        return start()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None


def _after_build(job: Job, switch: bool = False) -> None:
    """A finished build: load it right away on first run, otherwise swap it in with a restart."""
    region = job.result["region"]
    final = ops.data_dir(region)
    if region == config.REGION and not S.ready:
        ops.promote_staged(final, job.log)
        S.load()
        if not S.ready:
            raise RuntimeError(f"the new knowledge base did not load: {S.load_error}")
        asyncio.run_coroutine_threadsafe(S.start_services(), S.loop).result(timeout=60)
        job.result["restart"] = False
        job.stage = "Ready"
        job.log("Knowledge base loaded. NWIS is ready.")
        return
    if region != config.REGION:
        ops.promote_staged(final, job.log)          # not the base in use: swap it in now
        ops.copy_accounts(config.DB_PATH, final / "nwis.db")
        if not switch:
            job.result["restart"] = False
            job.stage = "Built (not in use)"
            job.log(f"{ops.DATASETS[region]['label']} is built. Switch to it under System → Dataset.")
            return
        config.save_settings({"dataset": region})
    job.result["restart"] = True
    job.stage = "Restarting to load it"
    job.log("Restarting the server to load the new knowledge base…")
    ops.restart_soon(1.5)


def _start_build(body: dict) -> dict:
    region = (body.get("dataset") or config.REGION).lower()
    if region not in ops.DATASETS:
        raise HTTPException(400, f"dataset must be one of {sorted(ops.DATASETS)}")
    if "dataset" in config.locked() and region != config.REGION:
        raise HTTPException(409, "the dataset is fixed by an environment variable (NWIS_REGION / NWIS_DATA_DIR)")
    title = f"Build: {ops.DATASETS[region]['label']}"
    switch = bool(body.get("switch", True))
    job = _job_or_409(lambda: JOBS.start(
        "build", title, lambda j: ops.build_dataset(j, region, str(body.get("quadrants") or "15,16"),
                                                    bool(body.get("download"))),
        exclusive=True, on_success=lambda j: _after_build(j, switch)))
    return job.payload()


@app.get("/api/setup/status")
def setup_status():
    """Public: what the first-run page needs (is a knowledge base loaded, is one being built)."""
    busy = JOBS.running_exclusive()
    datasets = {k: {x: v[x] for x in ("code", "label", "about", "built", "current", "synthetic")} | {"needs": v.get("needs")}
                for k, v in ops.dataset_status().items()}
    # before set-up nobody can sign in, so the build log is shown to whoever opens the page; afterwards it is not
    last = None if S.ready else next((j for j in sorted(JOBS.jobs.values(), key=lambda j: -j.started)
                                      if j.kind == "build"), None)
    return J({"ready": S.ready, "load_error": S.load_error, "region": config.REGION, "boot": ops.BOOT_ID,
              "datasets": datasets, "job": (last or busy).payload() if (last or busy) and not S.ready else None,
              "building": busy is not None and busy.kind == "build", "ocr": ocr_status(),
              "stages": {"assam": [x[2] for x in ops.ASSAM_BUILD_STAGES], "norway": [x[2] for x in ops.NORWAY_BUILD_STAGES]},
              "dataset_locked": "dataset" in config.locked()})


@app.post("/api/setup/build")
def setup_build(body: dict):
    """First run only (no knowledge base loaded yet); afterwards rebuilding is an administrator action."""
    if S.ready:
        raise HTTPException(409, "NWIS is already set up; an administrator can rebuild under System")
    return J(_start_build(body))


@app.get("/api/jobs")
def jobs_list():
    return J(JOBS.list())


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, since: int = 0):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found (the server may have restarted)")
    return J(job.payload(since))


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    return {"ok": JOBS.cancel(job_id)}


SETTING_CHOICES = {"top_pick_mode": ("auto", "dtw", "mudlogger"), "llm_backend": ("off", "ollama")}


def _stream_payload() -> dict:
    hub = S.hub
    return {"spec": config.STREAM, "live": hub is not None, "describe": hub.source.describe() if hub else "stored replay",
            "stats": hub.source.stats() if hub else None, "in_gap": hub.in_gap if hub else False,
            "samples": hub.session.n if hub else 0,
            "bit_md": hub.session.status()["md"] if hub and hub.session.n else None,
            "listen_port": getattr(hub.source, "bound_port", None) if hub and getattr(hub.source, "mode", "") == "listen" else None}


@app.get("/api/admin/status")
def admin_status():
    episodes = (S.db.kv_get("active_episodes") or []) if S.ready else []
    return J({"settings": config.current_settings(), "locked": sorted(config.locked()), "region": config.REGION,
              "data_dir": str(config.DATA_DIR), "datasets": ops.dataset_status(), "components": ops.component_status(),
              "stream": _stream_payload(), "simulator": ops.SIM.status(), "episodes": episodes,
              "supervised": ops.supervised(), "boot": ops.BOOT_ID, "jobs": JOBS.list(),
              "build": S.db.kv_get("build_info") if S.ready else None,
              "settings_file": str(config.SETTINGS_PATH)})


@app.post("/api/admin/settings")
async def admin_settings(body: dict):
    changes = {k: v for k, v in body.items() if k in config.ENV_OF and k not in ("dataset", "stream")}
    bad = sorted(set(changes) & config.locked())
    if bad:
        raise HTTPException(409, f"set by environment variable, cannot change here: {', '.join(bad)}")
    for k, v in changes.items():
        if k in SETTING_CHOICES and v not in SETTING_CHOICES[k]:
            raise HTTPException(400, f"{k} must be one of {SETTING_CHOICES[k]}")
    if "stream_gap_s" in changes:
        try:
            changes["stream_gap_s"] = max(10.0, float(changes["stream_gap_s"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "stream_gap_s must be a number of seconds") from None
    if "auth" in changes:
        changes["auth"] = bool(changes["auth"])
    for k in ("ollama_url", "ollama_model", "tile_url", "tile_attribution", "asr_model"):
        if k in changes:
            changes[k] = str(changes[k]).strip()
            if not changes[k]:
                raise HTTPException(400, f"{k} cannot be empty")
    attr = {"stream_gap_s": "STREAM_GAP_S", "top_pick_mode": "TOP_PICK_MODE", "llm_backend": "LLM_BACKEND",
            "ollama_url": "OLLAMA_URL", "ollama_model": "OLLAMA_MODEL", "auth": "AUTH", "tile_url": "TILE_URL",
            "tile_attribution": "TILE_ATTRIBUTION", "asr_model": "ASR_MODEL"}
    for k, v in changes.items():
        setattr(config, attr[k], v)
    if "stream_gap_s" in changes and S.hub is not None:
        S.hub.gap_s = changes["stream_gap_s"]
    if "asr_model" in changes:
        from ..ingest import voice
        voice._model = None
    config.save_settings(changes)
    return J({"settings": config.current_settings()})


@app.post("/api/admin/stream")
async def admin_stream(body: dict):
    if "stream" in config.locked():
        raise HTTPException(409, "the live feed is fixed by the NWIS_STREAM environment variable")
    spec = str(body.get("spec") or "replay").strip()
    try:
        await S.set_stream(spec)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"invalid feed setting: {e}") from None
    if S.hub is None:
        await asyncio.to_thread(ops.SIM.stop)
    config.save_settings({"stream": spec})
    return J(_stream_payload())


@app.post("/api/admin/simulator/start")
async def simulator_start(body: dict):
    """Demo rig: send the stored active-well stream as WITS-0 frames into NWIS's own listener."""
    kb()
    if not ops.SIM.available():
        raise HTTPException(409, "this dataset has no recorded rig stream to transmit (use the Assam demo)")
    port = int(body.get("port") or 5501)
    src = S.hub.source if S.hub else None
    if not (src is not None and getattr(src, "mode", None) == "listen"):
        if "stream" in config.locked():
            raise HTTPException(409, "the live feed is fixed by NWIS_STREAM and is not a WITS-0 listener")
        await S.set_stream(f"wits0-listen:{port}")
        config.save_settings({"stream": config.STREAM})
        src = S.hub.source
        await asyncio.to_thread(src.wait_ready, 5.0)
    port = src.bound_port or src.port
    from_md = body.get("from_md")
    if body.get("episode"):
        ep = next((e for e in S.db.kv_get("active_episodes") or [] if e["id"] == body["episode"]), None)
        if ep is None:
            raise HTTPException(404, "unknown scenario")
        from_md = max(0.0, float(ep.get("onset_md") or ep["md"]) - 150)
    speed = float(body["speed"]) if body.get("speed") is not None else 60.0   # 0 = as fast as possible
    if speed < 0:
        raise HTTPException(400, "speed must be 0 (as fast as possible) or a positive factor")
    await asyncio.to_thread(ops.SIM.start, port, speed, float(from_md) if from_md else None)
    return J({"simulator": ops.SIM.status(), "stream": _stream_payload()})


@app.post("/api/admin/simulator/stop")
async def simulator_stop():
    await asyncio.to_thread(ops.SIM.stop)
    return J({"simulator": ops.SIM.status()})


@app.post("/api/admin/build")
def admin_build(body: dict):
    return J(_start_build(body))


@app.post("/api/admin/sodir-csv")
async def admin_sodir_csv(files: list[UploadFile] = File(...)):
    """FactPages CSV exports uploaded from the browser (for servers without internet access)."""
    from ..public import sodir
    dest = ops.data_dir("norway") / "public" / "sodir"
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for f in files:
        name = Path(f.filename or "").name
        if name.lower().endswith(".csv"):
            with (dest / name).open("wb") as out:
                shutil.copyfileobj(f.file, out)
            names.append(name)
    have = sorted(p.stem for p in dest.glob("*.csv"))
    return J({"saved": names, "have": have, "missing": [t for t in sodir.TABLES if t not in have]})


@app.post("/api/admin/dataset")
def admin_dataset(body: dict):
    region = (body.get("dataset") or "").lower()
    st = ops.dataset_status()
    if region not in st:
        raise HTTPException(400, f"dataset must be one of {sorted(st)}")
    if "dataset" in config.locked():
        raise HTTPException(409, "the dataset is fixed by an environment variable (NWIS_REGION / NWIS_DATA_DIR)")
    if region == config.REGION:
        return {"ok": True, "restart": False}
    if not st[region]["built"]:
        raise HTTPException(409, f"{st[region]['label']} is not built yet")
    busy = JOBS.running_exclusive()
    if busy:
        raise HTTPException(409, f"'{busy.title}' is still running")
    ops.copy_accounts(config.DB_PATH, ops.data_dir(region) / "nwis.db")
    config.save_settings({"dataset": region})
    ops.restart_soon()
    return {"ok": True, "restart": True}


@app.post("/api/admin/restart")
def admin_restart():
    busy = JOBS.running_exclusive()
    if busy:
        raise HTTPException(409, f"'{busy.title}' is still running")
    ops.restart_soon()
    return {"ok": True, "restart": True, "supervised": ops.supervised()}


@app.post("/api/admin/install")
def admin_install(body: dict):
    name = body.get("component")
    if name not in ops.COMPONENTS:
        raise HTTPException(400, f"component must be one of {sorted(ops.COMPONENTS)}")
    job = _job_or_409(lambda: JOBS.start("install", f"Install {ops.COMPONENTS[name]['label']}",
                                         lambda j: ops.install_component(j, name), exclusive=True))
    return J(job.payload())


@app.post("/api/admin/llm/test")
def admin_llm_test(body: dict):
    import urllib.request
    url = (body.get("url") or config.OLLAMA_URL).rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=4) as r:
            models = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        return {"ok": True, "models": models}
    except Exception as e:  # noqa: BLE001 - report whatever went wrong to the admin
        return {"ok": False, "error": str(e)[:200]}


UPLOAD_KINDS = (".pdf", ".xml")


def _save_uploads(files: list[UploadFile], folder: Path, kinds: tuple[str, ...]) -> list[Path]:
    """Save uploaded files; a .zip is expanded (only files of the allowed kinds, flattened, no path traversal)."""
    import zipfile
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    def target(name: str) -> Path:
        base = Path(name).name or "file"
        p, i = folder / base, 1
        while p.exists():
            p = folder / f"{Path(base).stem}_{i}{Path(base).suffix}"
            i += 1
        return p

    for f in files:
        name = Path(f.filename or "upload").name
        low = name.lower()
        if low.endswith(".zip"):
            tmp = folder / f"_{name}"
            with tmp.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            try:
                with zipfile.ZipFile(tmp) as z:
                    for m in z.infolist():
                        if m.is_dir() or not m.filename.lower().endswith(kinds) or m.file_size > 300 * 2 ** 20:
                            continue
                        if Path(m.filename).name.startswith("._"):   # macOS resource forks
                            continue
                        dest = target(m.filename)
                        with z.open(m) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
                        saved.append(dest)
            except zipfile.BadZipFile:
                raise HTTPException(400, f"{name} is not a valid zip file") from None
            finally:
                tmp.unlink(missing_ok=True)
        elif low.endswith(kinds):
            dest = target(name)
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append(dest)
    return saved


@app.post("/api/ingest/batch")
async def ingest_batch(files: list[UploadFile] = File(...)):
    """Many reports at once (a folder, several files or a .zip): a background job with per-file results."""
    kb()
    folder = config.UPLOADS_DIR / f"batch_{dt.datetime.now():%Y%m%d%H%M%S}"
    paths = await asyncio.to_thread(_save_uploads, files, folder, UPLOAD_KINDS)
    if not paths:
        raise HTTPException(400, "no PDF or WITSML XML files found in the upload")

    def run(job: Job):
        ing = Ingestor(DB(), S.clf)
        rows, n = [], len(paths)
        for i, p in enumerate(paths):
            job.check_cancel()
            job.progress, job.stage = i / n, f"{i + 1}/{n} · {p.name}"
            try:
                r = ing.ingest_witsml(p, source="upload") if p.suffix.lower() == ".xml" else ing.ingest_pdf(p, source="upload")
                ev = r.get("events", [])
                row = {"file": p.name, "kind": r.get("kind"), "well_id": r.get("well_id"), "pages": len(r.get("pages") or []),
                       "events": len(ev), "review": sum(1 for e in ev if e.get("status") != "auto"),
                       "lessons": len(r.get("lessons") or []), "warnings": r.get("warnings") or [], "error": None}
                job.log(f"{p.name}: {row['events']} events ({row['review']} to review), well {row['well_id'] or '?'}")
            except Exception as e:  # noqa: BLE001 - one bad file must not stop a large import
                row = {"file": p.name, "error": str(e)[:200]}
                job.log(f"{p.name}: skipped ({e})")
            rows.append(row)
            if (i + 1) % 20 == 0:
                S.refresh()
        S.refresh()
        return {"files": rows, "n_files": n, "events": sum(r.get("events") or 0 for r in rows),
                "review": sum(r.get("review") or 0 for r in rows), "errors": sum(1 for r in rows if r.get("error"))}
    return J(JOBS.start("import", f"Import {len(paths)} document(s)", run).payload())


@app.post("/api/analytics/evaluate-live")
def analytics_evaluate_live():
    kb()
    if not (config.LOGS_DIR / "active_stream.npz").exists():
        raise HTTPException(409, "this dataset has no recorded rig stream to replay")

    def run(job: Job):
        from ..realtime.evaluate import evaluate_live
        job.stage = "Replaying the active well (alarm-budget sweep, then top picking)"
        job.log(job.stage)
        res = evaluate_live(KnowledgeBase(DB()), S.model)
        for r in res["budget"]["rows"]:
            job.log(f"nuisance={r['nuisance']} budget={r['budget_per_hour']}: detected {r['detected']}/"
                    f"{len(r['episodes'])}, false alarms {r['false_alarms']} ({r['false_per_hour']}/h)")
        return {"computed": res["computed"]}
    return J(_job_or_409(lambda: JOBS.start("evaluate", "Live alerting evaluation", run, exclusive=True)).payload())


@app.post("/api/analytics/validate-volve")
async def analytics_validate_volve(files: list[UploadFile] = File(...)):
    """Score report reading on the public Equinor Volve daily drilling reports (XML files or a .zip)."""
    kb()
    folder = config.UPLOADS_DIR / f"volve_{dt.datetime.now():%Y%m%d%H%M%S}"
    paths = await asyncio.to_thread(_save_uploads, files, folder, (".xml",))
    if not paths:
        raise HTTPException(400, "no drillReport XML files found in the upload")

    def run(job: Job):
        from ..validate.volve import run_and_store
        job.stage = f"Scoring {len(paths)} daily drilling reports"
        res = run_and_store(DB(), folder, log=job.log)
        return {"n_wells": res["n_wells"], "n_reports": res["n_reports"], "f1": res["zero_shot"]["f1"]}
    return J(_job_or_409(lambda: JOBS.start("validate", "Real-data check (Equinor Volve)", run,
                                            exclusive=True)).payload())


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
        # the shell files must always be revalidated, or a browser keeps an old index.html pointing at old bundles
        no_cache = {"Cache-Control": "no-cache"}
        if full_path and f.is_file():
            if full_path in ("sw.js", "precache.json", "index.html"):
                return FileResponse(f, headers=no_cache)
            if full_path.endswith(".webmanifest"):
                return FileResponse(f, media_type="application/manifest+json")
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html", headers=no_cache)
