"""Runtime configuration.

Most values can be changed from the dashboard (System view, admin). They are kept in a settings file
(stratasense_settings.json in the repository root, or STRATASENSE_SETTINGS). An environment variable, when set, still wins:
it is a deployment override, and the dashboard shows that setting as locked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(os.environ.get("STRATASENSE_SETTINGS", ROOT / "stratasense_settings.json"))

# Knowledge-base file name in each data folder. Before the rename to StrataSense it was "nwis.db";
# migrate_legacy_db() moves an old one into place so an existing build keeps working.
DB_NAME = "stratasense.db"
_LEGACY_DB_NAME = "nwis.db"
_LEGACY_SETTINGS = ROOT / "nwis_settings.json"


def migrate_legacy_db(folder: Path) -> None:
    """Rename a pre-rename nwis.db (and its WAL/SHM files) in `folder` to the current name, once."""
    old, new = Path(folder) / _LEGACY_DB_NAME, Path(folder) / DB_NAME
    if new.exists() or not old.exists():
        return
    # main file first: if it is open elsewhere (Windows refuses the rename) nothing has moved yet
    try:
        for suffix in ("", "-wal", "-shm"):
            src = old.with_name(old.name + suffix)
            if src.exists():
                src.rename(new.with_name(new.name + suffix))
    except OSError as e:
        print(f"[stratasense] could not rename {old} to {new.name} ({e}); stop any older server using it and restart",
              flush=True)


if "STRATASENSE_SETTINGS" not in os.environ and not SETTINGS_PATH.exists() and _LEGACY_SETTINGS.exists():
    _LEGACY_SETTINGS.rename(SETTINGS_PATH)

# dashboard setting -> environment variable that overrides it
ENV_OF = {"dataset": "STRATASENSE_REGION", "stream": "STRATASENSE_STREAM", "stream_gap_s": "STRATASENSE_STREAM_GAP_S",
          "top_pick_mode": "STRATASENSE_TOP_PICK", "llm_backend": "STRATASENSE_LLM", "ollama_url": "OLLAMA_URL",
          "ollama_model": "OLLAMA_MODEL", "auth": "STRATASENSE_AUTH", "tile_url": "STRATASENSE_TILE_URL",
          "tile_attribution": "STRATASENSE_TILE_ATTRIBUTION", "asr_model": "STRATASENSE_ASR_MODEL"}


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
    return {k for k, env in ENV_OF.items() if env in os.environ} | ({"dataset"} if "STRATASENSE_DATA_DIR" in os.environ else set())


def data_dir_for(region: str) -> Path:
    return ROOT / ("data" if region == "assam" else f"data_{region}")


# Stratigraphic region (see domain/ontology.py): "assam" = synthetic demo, "norway" = real public data (Sodir wells,
# Equinor Volve rig data). Each region has its own data folder, because tops and models are region-specific.
# StrataSense opens on the real North Sea data once it has been built; the synthetic Assam demo is one switch away.
REGION = _get("dataset", "norway" if (data_dir_for("norway") / DB_NAME).exists() else "assam").lower()


def log_path(well_id: str) -> Path:
    """Depth-indexed drilling log of an offset well; public well names contain '/' (e.g. 15/9-F-12)."""
    return LOGS_DIR / f"{str(well_id).replace('/', '_')}.npz"


DATA_DIR = Path(os.environ.get("STRATASENSE_DATA_DIR", data_dir_for(REGION)))
migrate_legacy_db(DATA_DIR)
DB_PATH = DATA_DIR / DB_NAME
DOCS_DIR = DATA_DIR / "documents"
LOGS_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
UPLOADS_DIR = DATA_DIR / "uploads"
FRONTEND_DIST = Path(os.environ.get("STRATASENSE_FRONTEND_DIST", ROOT / "frontend" / "dist"))

SEED = int(os.environ.get("STRATASENSE_SEED", "26121"))
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
DEMO_PASSWORD = os.environ.get("STRATASENSE_DEMO_PASSWORD", "demo")   # seeded accounts field / office / admin
SECRET = os.environ.get("STRATASENSE_SECRET", "")                      # session-signing key; default: generated, kept in the DB


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
