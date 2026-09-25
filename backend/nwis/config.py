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
# Reference "today" for recency weighting; fixed so demo results are reproducible.
REFERENCE_YEAR = 2026

# Optional on-prem LLM (Ollama). Off unless NWIS_LLM=ollama.
LLM_BACKEND = os.environ.get("NWIS_LLM", "off").lower()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Extraction confidence below which an item goes to the human review queue.
REVIEW_THRESHOLD = 0.7


def ensure_dirs() -> None:
    for d in (DATA_DIR, DOCS_DIR, LOGS_DIR, MODELS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)
