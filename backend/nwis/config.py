"""Runtime configuration (all overridable through environment variables)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NWIS_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "nwis.db"
DOCS_DIR = DATA_DIR / "documents"
LOGS_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
UPLOADS_DIR = DATA_DIR / "uploads"
FRONTEND_DIST = Path(os.environ.get("NWIS_FRONTEND_DIST", ROOT / "frontend" / "dist"))

SEED = int(os.environ.get("NWIS_SEED", "26121"))
# Stratigraphic region (see domain/ontology.py): "assam" = synthetic demo, "norway" = real public Sodir data.
# Each region needs its own data folder (NWIS_DATA_DIR), because tops and models are region-specific.
REGION = os.environ.get("NWIS_REGION", "assam").lower()
# Reference "today" for recency weighting; fixed so demo results are reproducible.
REFERENCE_YEAR = 2026

# Optional on-prem LLM (Ollama). Off unless NWIS_LLM=ollama.
LLM_BACKEND = os.environ.get("NWIS_LLM", "off").lower()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Extraction confidence below which an item goes to the human review queue.
REVIEW_THRESHOLD = 0.7

# Live formation-top picking: "mudlogger" (lagged cuttings picks only), "dtw" (gamma-ray correlation only),
# or "auto" (mud-logger picks drive re-anchoring; DTW runs independently as a QC and flags disagreements).
TOP_PICK_MODE = os.environ.get("NWIS_TOP_PICK", "auto").lower()
DTW_CONFLICT_M = 15.0   # DTW vs mud-logger difference that raises a correlation-conflict note

# Live rig feed: "replay" (stored stream per browser), "wits0-listen:5501", "wits0-connect:host:port",
# or "witsml:https://store/...?well=..&wellbore=..&log=..". See realtime/sources.py.
STREAM = os.environ.get("NWIS_STREAM", "replay")
STREAM_GAP_S = float(os.environ.get("NWIS_STREAM_GAP_S", "300"))   # no packet for this long -> stream-gap event

# Offset-map background tiles. Default: OpenStreetMap's public tiles (no key; fine for a demo, attribution
# required). For OIL, point this at an on-prem tile server so the map works without internet.
TILE_URL = os.environ.get("NWIS_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTRIBUTION = os.environ.get("NWIS_TILE_ATTRIBUTION", "&copy; OpenStreetMap contributors")

# Optional on-prem speech-to-text for expert voice memos (pip install "nwis[asr]"; weights must be cached).
ASR_MODEL = os.environ.get("NWIS_ASR_MODEL", "small")


def ensure_dirs() -> None:
    for d in (DATA_DIR, DOCS_DIR, LOGS_DIR, MODELS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)

# Sign-in and role-based access (see auth.py). NWIS_AUTH=off disables it (local development, unit tests).
AUTH = os.environ.get("NWIS_AUTH", "on").lower() not in ("off", "0", "false", "no")
DEMO_PASSWORD = os.environ.get("NWIS_DEMO_PASSWORD", "demo")   # seeded accounts field / office / admin
SECRET = os.environ.get("NWIS_SECRET", "")                      # session-signing key; default: generated, kept in the DB
