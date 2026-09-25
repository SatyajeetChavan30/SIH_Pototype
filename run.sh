#!/usr/bin/env bash
# One-command setup & launch for the eRTMAC-NWIS prototype.
#   ./run.sh            install deps (first run), build demo data if missing, build UI, serve on :8000
#   ./run.sh --rebuild  regenerate the synthetic knowledge base from scratch
#   ./run.sh --dev      run backend (:8000) + Vite dev server (:5173) with hot reload
#   ./run.sh --public   real public North Sea data (Sodir FactPages, NLOD) instead of the synthetic demo
#                       (first run downloads the CSV exports; QUADRANTS=15,16 by default)
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
  echo "[nwis] building synthetic Upper-Assam knowledge base (~2 min; ~8 min with OCR installed)"
  (cd backend && python -m nwis.cli build-demo)
fi

if [ "${1:-}" = "--public" ]; then
  export NWIS_REGION=norway NWIS_DATA_DIR="$ROOT/data_norway"
  if [ ! -f data_norway/nwis.db ]; then
    echo "[nwis] building the real public-data knowledge base (Sodir FactPages, NLOD)"
    (cd backend && python -m nwis.cli build-public --download --quadrants "${QUADRANTS:-15,16}")
  fi
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

echo "[nwis] open http://localhost:${PORT:-8000}  (demo sign-in: field / office / admin, password demo; NWIS_AUTH=off to skip)"
cd backend && exec python -m nwis.cli serve --port "${PORT:-8000}"
