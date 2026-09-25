#!/usr/bin/env bash
# One-command setup & launch for the eRTMAC-NWIS prototype.
#   ./run.sh            install deps (first run), build demo data if missing, build UI, serve on :8000
#   ./run.sh --rebuild  regenerate the synthetic knowledge base from scratch
#   ./run.sh --dev      run backend (:8000) + Vite dev server (:5173) with hot reload
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "[nwis] creating virtualenv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c "import nwis" 2>/dev/null; then
  echo "[nwis] installing backend (FastAPI, scikit-learn, PyMuPDF, OCR)"
  pip install -q --upgrade pip
  pip install -q -e "backend[dev,ocr]" || pip install -q -e "backend[dev]"   # OCR is optional
fi

if [ "${1:-}" = "--rebuild" ] || [ ! -f data/nwis.db ]; then
  echo "[nwis] building synthetic Upper-Assam knowledge base (~2 min)"
  (cd backend && python -m nwis.cli build-demo)
fi

if [ "${1:-}" = "--dev" ]; then
  (cd frontend && [ -d node_modules ] || npm install)
  (cd backend && python -m nwis.cli serve --port 8000) &
  trap 'kill %1' EXIT
  cd frontend && npm run dev
  exit 0
fi

if [ ! -f frontend/dist/index.html ] || [ "${1:-}" = "--rebuild" ]; then
  echo "[nwis] building web UI"
  (cd frontend && ( [ -d node_modules ] || npm install --no-audit --no-fund ) && npm run build)
fi

echo "[nwis] open http://localhost:${PORT:-8000}"
cd backend && exec python -m nwis.cli serve --port "${PORT:-8000}"
