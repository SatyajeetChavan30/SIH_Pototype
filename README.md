# eRTMAC-NWIS: Nearby Wells Intelligence System

**SIH 2026 · PS 26121 · Oil India Limited.** An AI-powered offset-well knowledge and decision-support platform that runs alongside eRTMAC.

NWIS turns decades of DDRs, WCRs and scanned reports into **cited, structured drilling knowledge**. It then uses that knowledge to warn the rig **before** the bit reaches a problem interval, by projecting offset-well events onto the active well **by formation, not measured depth**.

> ⚠️ **All data in this repository's demo is SYNTHETIC.** It is generated from published Upper-Assam geology (Girujan clay, depleted Tipam sands, Barail coal and thrust-proximal overpressure, fractured Sylhet limestone). Well names are fictitious. A **real public-data mode** (Norwegian North Sea, Sodir FactPages, open licence) runs with `./run.sh --public`; where OIL's own data would come from, and how to request real Assam well data from DGH's National Data Repository, is in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

![Live Ops](docs/screenshots/01_live_ops.png)

## Why it's different

| | Typical offset tools | **NWIS** |
|---|---|---|
| Offset comparison | by MD / TVD | **by formation**: tops interpolated with ±σ and re-anchored live as tops are picked |
| Risk | score, no uncertainty | **probability, 90% credible interval and evidence count** (Beta-Binomial) plus an ML model; beats "look at the nearest well" (AUC 0.89 vs 0.59) |
| Mud weight | fixed program | **offset-derived, depletion-aware MW/ECD window**: P(loss\|ECD), P(kick\|MW) |
| Alerts | "something is wrong" | **fused and corroborated**: the source page, what worked last time (case-mix-adjusted cure rates) and analog situations; physics-expected baselines; an RTOC-set **alarm budget** with a visible digest |
| Accountability | none | **decision black box**: hash-chained log of every alert shown, who acknowledged it and what they said |
| Legacy knowledge | digital data only | **NLP + OCR** over DDR/WCR PDFs and scans, with negation, units, citations and a review queue; **expert memos** (typed or voice), **after-action reviews** and cited **shift handovers** |
| Access | shared logins | **sign-in with field / office / admin roles**, enforced on every API call; the decision log names the signed-in person |
| Deployment | cloud SaaS / licences | **on-prem, air-gapped, open source**; WITS-0 and WITSML adapters for eRTMAC |

Full rationale: [`docs/VISION.md`](docs/VISION.md) (end goal, success metrics, staged path) · [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) (data sources, public stand-ins, DGH NDR guide) · [`docs/RESEARCH.md`](docs/RESEARCH.md) (market and literature) · [`docs/SOLUTION.md`](docs/SOLUTION.md) (design, metrics, demo script, roadmap).

## Quick start

Requirements: Python 3.10+ and Node 18+.

```bash
./run.sh            # installs deps, builds the synthetic knowledge base, builds the UI, serves it
                    # (build ~2 min; ~8 min on 4 cores with OCR installed, which adds the scanned-report evaluation)
# open http://localhost:8000 and sign in: field / office / admin, password "demo"
```

Sign-in is on by default. A `field` account (rig site) sees Live Ops, the rig view, map, correlation, risk, knowledge and memo capture. `office` (RTOC, drilling engineer) adds ingestion, the review queue, after-action approval, the what-if planner and Analytics. `admin` adds user management. Set `NWIS_AUTH=off` to skip sign-in during development.

Other modes:
```bash
./run.sh --rebuild  # regenerate all demo data
./run.sh --dev      # backend :8000 + Vite hot-reload :5173
./run.sh --public   # real public North Sea data (Sodir FactPages, NLOD) instead of the synthetic demo; see docs/DATA_SOURCES.md
```

Manual steps:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev,ocr]"          # the [ocr] extra (RapidOCR) is optional; without it scans are stored unread and flagged
cd frontend && npm install && npm run build   # the server then serves frontend/dist
cd ../backend && python -m nwis.cli build-demo && python -m nwis.cli serve
```

Most of the commands below can also be run from the dashboard, without a terminal:
- **⇪ Upload reports** (top bar, office and admin): one file, many files or a whole folder of DDR/WCR PDFs and WITSML XML.
- **Analytics › Maintenance** (office): re-run the live evaluation, re-score OCR, retrain the risk model, and retrain the sentence classifier from review-queue verdicts (`retrain-risk`, `retrain-classifier` on the CLI). Admins can also rebuild the demo or build the North Sea data. A rebuild runs in a separate folder while the current data stays live, then switches over, or waits for the next start if a live rig feed is connected. User accounts, sign-ins, the decision log and alert feedback are kept, and the previous folder stays as `data.bak-<date>`.
- **Live Ops › Rig simulator** (office, when NWIS runs with `NWIS_STREAM=wits0-listen:PORT`): starts and stops `simulate-rig`.
- **Knowledge › Browse all** lists every event and lesson. **Ingestion** shows review history (approved and rejected). **Analytics › Decision log** browses the log, and admins can re-verify the hash chain there.

Every job started from the dashboard is recorded in the decision log under the name of whoever started it.

Installed OCR after `build-demo`? No rebuild is needed:
```bash
cd backend
python -m nwis.cli reread-scans    # OCR the scanned documents that were stored unread (also a button in Ingestion)
python -m nwis.cli evaluate-ocr    # re-score scanned-report recall (standard and poor scans) for Analytics
```

The `[ocr]` extra installs `rapidocr-onnxruntime` 1.x on Python ≤ 3.12 and its successor `rapidocr` 3.x on 3.13+. Both bundle their models in the wheel, so OCR runs offline.

Import real WITSML drillReport XML (e.g. the public Equinor Volve DDRs):
```bash
cd backend && python -m nwis.cli import-volve /path/to/volve/Well_technical_data/Daily_Drilling_Report_XML
```

Re-run the live-alerting evaluation (alarm-budget sweep and DTW top-pick accuracy; `build-demo` already does this):
```bash
cd backend && python -m nwis.cli evaluate-live
```

Optional on-prem speech-to-text for voice memos (Assamese, Hindi, English). Cache the Whisper model on the server for offline use:
```bash
pip install -e "backend[asr]"
```

Live rig feed instead of the stored replay: start NWIS as a WITS-0 listener, then push the demo well into it as real WITS-0 frames:
```bash
NWIS_STREAM=wits0-listen:5501 python -m nwis.cli serve
python -m nwis.cli simulate-rig --connect 127.0.0.1:5501 --speed 600
```
Other feeds: `NWIS_STREAM=wits0-connect:HOST:PORT` or `witsml:https://store/…?well=W&wellbore=WB&log=L`. The rig-site tablet app is at `/#/rig`. It is installable and keeps the last picture when the link drops.

Real-data check on the public Equinor Volve daily drilling reports (download them first and accept Equinor's licence):
```bash
cd backend && python -m nwis.cli validate-volve /path/to/volve/drill_reports
```

Formation-top picking mode: `NWIS_TOP_PICK=auto` (default: mud logger plus DTW QC), `dtw` or `mudlogger`.

Optional on-prem LLM phrasing, off by default. It is citation-guarded, and answers stay grounded without it:
```bash
NWIS_LLM=ollama OLLAMA_MODEL=llama3.1:8b python -m nwis.cli serve
```

## The seven views

| View | What to show |
|---|---|
| **Live Ops** | Replay of the active well NDH-21's eRTMAC stream. "Jump to" S1–S4 scenarios. Fused alert feed with p-values, look-ahead ribbon, physics-expected lines, alarm budget and digest, evidence drawer with citations and decision log, DTW top-pick QC, shift-handover brief, rig-site view. |
| **Offset Map** | Wells within a user-defined radius, coloured by dominant hazard. Click anywhere to assess a planned location. |
| **Correlation** | Offset logs side by side; flatten on a formation top and the Tipam thief sand lines up. |
| **Risk & Planning** | Depth × hazard risk with CIs, headline zones, MW window vs plan, **what-if planner** (MW / ECD / casing points), printable Offset Hazard Brief. |
| **Knowledge** | Search with auto-parsed filters and **CSV export** of every match with its source page, "Ask NWIS" with numbered citations, knowledge graph (what cured what), **after-action review** on any event. |
| **Ingestion** | Upload PDF/XML or use a sample. Sentence-level NLP trace, extracted events, human review queue, **expert memo** capture with peer review. Scans stored before OCR was installed are flagged, with a one-click re-read. |
| **Analytics** | Model skill vs baselines, extraction F1, NPT Pareto, calibration, what-if value, alarm-budget trade-off, DTW top-pick accuracy, decision-log verification. |

<details><summary>More screenshots</summary>

| | |
|---|---|
| ![](docs/screenshots/02_alert_evidence.png) | ![](docs/screenshots/03_citation.png) |
| ![](docs/screenshots/06_risk_planning.png) | ![](docs/screenshots/07_correlation.png) |
| ![](docs/screenshots/08_knowledge_search.png) | ![](docs/screenshots/11_ingestion.png) |
| ![](docs/screenshots/10_graph.png) | ![](docs/screenshots/12_analytics.png) |
| ![](docs/screenshots/14_upload_reports.png) | ![](docs/screenshots/15_maintenance_jobs.png) |
| ![](docs/screenshots/16_rig_simulator.png) | ![](docs/screenshots/18_handover_brief.png) |
| ![](docs/screenshots/13_sign_in.png) | ![](docs/screenshots/17_rig_site_app.png) |
</details>

## Repository layout

```
backend/nwis/
  domain/ontology.py      formations, hazards, mitigations, negation/hypothetical lexicons
  data/                   synthetic Upper-Assam world, DDR/WCR PDF renderer, drilling logs + active-well stream
  ingest/                 PDF/OCR, NLP extraction, pipeline + review queue, WITSML + WITS-0, evaluation
  correlation.py          IDW tops, formation-relative projection, correlation panel
  risk/                   Beta-Binomial ribbon + zones, HistGB model (leave-wells-out), MW window, case-mix-adjusted recommendations, what-if
  search/                 query parser, BM25 + LSA + RRF index, Ask NWIS
  realtime/               detectors, physics baselines, conformal alarm budget, DTW top picking, alert fusion, analog replay, live engine, replay evaluation
  kg.py report.py llm.py  knowledge graph, Offset Hazard Brief, optional Ollama with citation guard
  memory.py audit.py      shift handover + after-action reviews; hash-chained decision log
  auth.py                 sign-in (PBKDF2 + signed cookie) and the field / office / admin access policy
  realtime/sources.py hub.py simulator.py   WITS-0 TCP / WITSML sources, shared live session, rig simulator
  validate/volve.py       real-data check on the public Equinor Volve reports
  public/sodir.py         real public-data build from Sodir FactPages (North Sea wells, tops, casing, mud, LOT/FIT, histories)
  api/main.py             FastAPI REST + /ws/live WebSocket, serves the UI
backend/tests/            69 tests (NLP, OCR, geometry, parsers, model claims, live replay, API, sign-in and roles, vision features, live feed, public-data validation, Norway region)
frontend/src/             React + TypeScript views and components
docs/                     VISION.md, RESEARCH.md, SOLUTION.md, DATA_SOURCES.md, screenshots
```

## Tests

```bash
cd backend && pytest -q    # unit tests always run; system tests run once build-demo has been done
```
