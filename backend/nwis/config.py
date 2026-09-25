"""Runtime configuration.

Most values can be changed from the dashboard (System view, admin). They are kept in a settings file
(nwis_settings.json in the repository root, or NWIS_SETTINGS). An environment variable, when set, still wins:
it is a deployment override, and the dashboard shows that setting as locked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(os.environ.get("NWIS_SETTINGS", ROOT / "nwis_settings.json"))

# dashboard setting -> environment variable that overrides it
ENV_OF = {"dataset": "NWIS_REGION", "stream": "NWIS_STREAM", "stream_gap_s": "NWIS_STREAM_GAP_S",
          "top_pick_mode": "NWIS_TOP_PICK", "llm_backend": "NWIS_LLM", "ollama_url": "OLLAMA_URL",
          "ollama_model": "OLLAMA_MODEL", "auth": "NWIS_AUTH", "tile_url": "NWIS_TILE_URL",
          "tile_attribution": "NWIS_TILE_ATTRIBUTION", "asr_model": "NWIS_ASR_MODEL"}


def read_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_SETTINGS = read_settings()


def _get(key: str, default: str) -> str:
    env = ENV_OF[key]
    if env in os.environ:
        return os.environ[env]
    v = _SETTINGS.get(key)
    return default if v is None else str(v)


def _flag(v: str) -> bool:
    return str(v).lower() not in ("off", "0", "false", "no")


def locked() -> set[str]:
    """Settings fixed by an environment variable (the dashboard cannot change them)."""
    return {k for k, env in ENV_OF.items() if env in os.environ} | ({"dataset"} if "NWIS_DATA_DIR" in os.environ else set())


# Stratigraphic region (see domain/ontology.py): "assam" = synthetic demo, "norway" = real public Sodir data.
# Each region has its own data folder, because tops and models are region-specific.
REGION = _get("dataset", "assam").lower()


def data_dir_for(region: str) -> Path:
    return ROOT / ("data" if region == "assam" else f"data_{region}")


DATA_DIR = Path(os.environ.get("NWIS_DATA_DIR", data_dir_for(REGION)))
DB_PATH = DATA_DIR / "nwis.db"
DOCS_DIR = DATA_DIR / "documents"
LOGS_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
UPLOADS_DIR = DATA_DIR / "uploads"
FRONTEND_DIST = Path(os.environ.get("NWIS_FRONTEND_DIST", ROOT / "frontend" / "dist"))

SEED = int(os.environ.get("NWIS_SEED", "26121"))
# Reference "today" for recency weighting; fixed so demo results are reproducible.
REFERENCE_YEAR = 2026

# Optional on-prem LLM (Ollama). Off unless switched on.
LLM_BACKEND = _get("llm_backend", "off").lower()
OLLAMA_URL = _get("ollama_url", "http://localhost:11434")
OLLAMA_MODEL = _get("ollama_model", "llama3.1:8b")

# Extraction confidence below which an item goes to the human review queue.
REVIEW_THRESHOLD = 0.7

# Live formation-top picking: "mudlogger" (lagged cuttings picks only), "dtw" (gamma-ray correlation only),
# or "auto" (mud-logger picks drive re-anchoring; DTW runs independently as a QC and flags disagreements).
TOP_PICK_MODE = _get("top_pick_mode", "auto").lower()
DTW_CONFLICT_M = 15.0   # DTW vs mud-logger difference that raises a correlation-conflict note

# Live rig feed: "replay" (stored stream per browser), "wits0-listen:5501", "wits0-connect:host:port",
# or "witsml:https://store/...?well=..&wellbore=..&log=..". See realtime/sources.py.
STREAM = _get("stream", "replay")
STREAM_GAP_S = float(_get("stream_gap_s", "300"))   # no packet for this long -> stream-gap event

# Offset-map background tiles. Default: OpenStreetMap's public tiles (no key; fine for a demo, attribution
# required). For OIL, point this at an on-prem tile server so the map works without internet.
TILE_URL = _get("tile_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTRIBUTION = _get("tile_attribution", "&copy; OpenStreetMap contributors")

# Optional on-prem speech-to-text for expert voice memos (weights must be cached on the server).
ASR_MODEL = _get("asr_model", "small")


def ensure_dirs() -> None:
    for d in (DATA_DIR, DOCS_DIR, LOGS_DIR, MODELS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)

# Sign-in and role-based access (see auth.py). Switch off only for local development and unit tests.
AUTH = _flag(_get("auth", "on"))
DEMO_PASSWORD = os.environ.get("NWIS_DEMO_PASSWORD", "demo")   # seeded accounts field / office / admin
SECRET = os.environ.get("NWIS_SECRET", "")                      # session-signing key; default: generated, kept in the DB


def current_settings() -> dict:
    """What the dashboard shows and edits (values currently in force)."""
    return {"dataset": REGION, "stream": STREAM, "stream_gap_s": STREAM_GAP_S, "top_pick_mode": TOP_PICK_MODE,
            "llm_backend": LLM_BACKEND, "ollama_url": OLLAMA_URL, "ollama_model": OLLAMA_MODEL, "auth": AUTH,
            "tile_url": TILE_URL, "tile_attribution": TILE_ATTRIBUTION, "asr_model": ASR_MODEL}


def save_settings(changes: dict) -> None:
    """Persist dashboard changes so they survive a restart."""
    s = read_settings()
    s.update(changes)
    SETTINGS_PATH.write_text(json.dumps(s, indent=1), encoding="utf-8")
