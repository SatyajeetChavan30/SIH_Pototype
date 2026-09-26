#!/usr/bin/env bash
# StrataSense launcher for Linux / macOS (Windows: double-click start.bat).
#   ./run.sh            install what StrataSense needs (first run), build the web UI, start the server and open the dashboard.
#                       On first run the dashboard offers to build the knowledge base; everything after that
#                       (imports, dataset switch, live rig feed, simulator, settings, users) is done in the browser.
#   ./run.sh --dev      backend (:8000) + Vite dev server (:5173) with hot reload, for developing StrataSense itself
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
PORT="${PORT:-8000}"

if [ ! -d .venv ]; then
  echo "[stratasense] creating virtualenv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c "import stratasense" 2>/dev/null; then
  echo "[stratasense] installing backend (FastAPI, scikit-learn, PyMuPDF, OCR)"
  pip install -q --upgrade pip
  pip install -q -e "backend[dev,ocr]" || pip install -q -e "backend[dev]"   # OCR is optional (installable later from System)
fi

if [ "${1:-}" = "--dev" ]; then
  (cd frontend && [ -d node_modules ] || npm install)
  (cd backend && python -m stratasense.cli serve --port "$PORT") &
  trap 'kill %1' EXIT
  cd frontend && npm run dev
  exit 0
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "[stratasense] building web UI"
  (cd frontend && ( [ -d node_modules ] || npm install --no-audit --no-fund ) && npm run build)
fi

echo "[stratasense] open http://localhost:${PORT}  (first run: the page builds the knowledge base; sign-in: field / office / admin, password demo)"
cd backend && exec python -m stratasense.cli start --port "$PORT" --open
