# Data sources: what StrataSense needs, where it comes from, and how to get it

The problem statement lists nine data sources "available within OIL". All of them are **OIL-internal and confidential**, and no dataset was published with PS 26121. StrataSense therefore ships with three kinds of data:

1. **Synthetic Upper-Assam demo data**, generated on your machine from the first-run page of the dashboard (**Build knowledge base**). This is the default.
2. **Real public stand-ins** from Norway (Sodir FactPages) and Equinor (Volve). They check the pipeline on genuine records.
3. **OIL's own data**, in a pilot. The same importers take it unchanged.

## 1. The nine OIL sources and how StrataSense takes them in

| # | OIL source | What StrataSense needs from it | How StrataSense ingests it (already built) |
|---|---|---|---|
| i | Well Completion Reports (WCRs) | PDFs, scanned or digital | PDF text layer or OCR, then NLP events, lessons and citations (`ingest/pdf.py`, `ingest/ocr.py`, `ingest/pipeline.py`) |
| ii | Daily Drilling Reports (DDRs) | PDFs or WITSML `drillReport` XML | Same NLP pipeline; WITSML importer (`ingest/witsml.py`) |
| iii | Drilling and mud-logging databases | Table export (CSV / SQL) | Loaded into `wells`, `sections` and mud tables, like the Sodir importer (`public/sodir.py`) |
| iv | Historical well parameters and drilling records | Depth-indexed logs (LAS / CSV / WITSML log) | Offset logs for correlation, DTW top picking and analogs |
| v | Reservoir and geological data | Formation tops per well, structure outlines | `tops` table, correlation by formation |
| vi | eRTMAC data streams | WITS-0 over TCP or WITSML 1.4.1 store | Live adapter (`realtime/sources.py`; WITSML time logs parsed with null handling and SI → engine unit conversion in `ingest/witsml_log.py`), chosen in **System → Live rig feed** |
| vii | Well trajectory and survey data | MD / inclination / azimuth stations | `surveys` table, minimum-curvature trajectories |
| viii | Casing, cementing and mud programme records | Casing depths and sizes, mud weights, LOT/FIT | `sections` table (casing, MW/ECD), `lot_tests` table |
| ix | Operational event records (losses, kicks, stuck pipe, fishing, NPT) | Event table or free text | Direct load, or NLP extraction from text |

**Getting it:** only through OIL, via the SIH problem-statement mentors or, for a pilot, a data-sharing agreement or NDA. StrataSense runs fully on-prem with no cloud calls, so OIL's data never has to leave its network.

## 2. Real public stand-ins (usable now)

| OIL source | Public stand-in | Licence |
|---|---|---|
| i WCRs | Sodir **wellbore history** narratives (completion-report style summaries with real kicks, losses, stuck pipe and fishing) | NLOD 2.0: free reuse with attribution |
| ii DDRs | **Equinor Volve** daily drilling reports (1,759 report-days, WITSML) | Equinor Open Data Licence: attribution, no resale; you accept it on Equinor's site |
| iii / viii mud and casing | Sodir **drilling mud** (depth, mud weight, mud type) and **casing and leak-off tests** (casing size and depth, hole size, LOT/FIT) | NLOD 2.0 |
| v geology | Sodir **lithostratigraphy** (group and formation tops per wellbore) | NLOD 2.0 |
| vii trajectories | Sodir coordinates, TD and maximum inclination (StrataSense uses near-vertical wells only) | NLOD 2.0 |
| ix events | Extracted by StrataSense from the Sodir histories | NLOD 2.0 |
| vi eRTMAC streams | **Equinor Volve** real-time WITSML surface-sensor logs (26 wellbores), imported with `import-volve-stream` or in System → Dataset (below). The built-in rig simulator can send them as real WITS-0 frames. | Equinor Open Data Licence |

### Build the real-data version (Norwegian North Sea)

StrataSense opens on this dataset once it is built. The synthetic Assam demo stays one click away: the **Synthetic · Assam | Real · North Sea** switch in the header (admins), or **System → Dataset**. Switching restarts the server on the other knowledge base.

In the dashboard, open **System → Dataset** (admin), set the quadrants (`all` = the whole Norwegian North Sea), tick *download from factpages.sodir.no* and click **Build North Sea knowledge base**. The FactPages CSV exports (tens of MB) are fetched once and kept for offline rebuilds. A server without internet can take the CSV files through the upload box on the same card.

For scripting, the same from a terminal:
```bash
cd backend
STRATASENSE_REGION=norway STRATASENSE_DATA_DIR=../data_norway python -m stratasense.cli build-public --download --quadrants all
```

What the current build holds (whole North Sea, 27 Sep 2026):

| | Count | Source |
|---|---|---|
| Near-vertical exploration wellbores | 1,024 (217 fields and discoveries) | Sodir FactPages |
| Group-level formation tops | 6,331 | Sodir FactPages |
| Wellbore histories read by the NLP pipeline | 999, giving 658 incident events | Sodir FactPages |
| Volve development wellbores with real drilling logs | 7 (15/9-F-4, F-7, F-9 A, F-10, F-12, F-14, F-15) | Equinor Volve (below) |
| Their real formation tops | 35 | Volve well picks |
| Their daily drilling reports read by the NLP pipeline | 636 report-days, giving 537 events | Volve DDR XML |

- **Region.** The North Sea dataset switches the stratigraphy to North Sea **groups** (Nordland … Hegre). Well-known formation names map to their group, e.g. Lista → Rogaland and Draupne → Viking.
- **ECD.** Sodir publishes mud weight but not ECD. ECD is set to mud weight + **0.42 ppg**, the median ECD − MW measured while drilling in the Volve depth logs (1,845 samples; by hole: 17½″ 0.24, 12¼″ 0.21, 8½″ 0.63 ppg). Without the Volve import it falls back to an assumed 0.3 ppg.
- **Report reader.** When the Volve reports are imported, the sentence classifier is refitted on the synthetic training set plus 21,329 real Volve report sentences labelled by the operator's own activity codes. The reports of the replayed well (F-14) are held out.
- **Offline copies.** Upload the CSVs in the dashboard (or pass `--from-folder DIR` on the command line) instead of downloading.
- **Required attribution:** *Contains data under the Norwegian licence for Open Government data (NLOD) distributed by the Norwegian Offshore Directorate.*

Limits, stated plainly:
- Only near-vertical exploration wells are used from Sodir (maximum inclination ≤ 15°), so their MD is treated as TVD. The Volve wells use their real WITSML surveys.
- The Sodir histories are summaries, so incidents are under-reported compared with DDRs. The risk model's pooled AUC is 0.69 against 0.59 for the base rate and 0.55 for "look at the nearest well": better, but still modest. The ML model on its own (0.38) is worse than the base rate, and the blend relies on offset evidence.
- The Sodir wells have no public well logs, so Correlation, gamma-ray top picking and analog matching use the Volve wells' logs only.
- North Sea geology is not Assam. This checks that StrataSense works on real records; it does not measure accuracy for OIL.

### Real rig data: Equinor Volve (logs, casing, picks, daily reports)

Equinor published the Volve field's data (block 15/9, inside quadrant 15). StrataSense uses four parts of it:

| Volve file | What it gives StrataSense |
|---|---|
| `WITSML Realtime drilling data` (2.5 GB zip, 26 wellbore folders) | Time logs → the Live Ops stream. Depth-indexed section logs ("12 1/4in Section - MD Log") → offset logs for Correlation, DTW and analogs. Surveys (`trajectory`) → TVD. Casing strings (`wbGeometry`) → hole sections |
| `Geophysical_Interpretations/Wells/Well_picks_Volve_v1.dat` (75 KB) | Real formation tops of the Volve wells, and of Sodir wells 15/9-11 and 15/9-17 |
| `Well_technical_data/Daily Drilling Report - XML Version` (1,759 files, 25 MB) | Cited report documents and events for each Volve well; operator-coded incidents; real-text training for the report reader |
| Sodir `wellbore_development_all.csv` | Wellhead position, TD and dates of each Volve wellbore |

1. **Get the data.** Go to https://www.equinor.com/energy/volve-data-sharing, then Databricks Marketplace, then **Volve Data Village**, then **Get instant access**. You need a free Databricks account, and access can take up to an hour to appear. Then:
   - Download **`volvezipfiles/Volve_WITSML Realtime drilling data.zip`** and unzip it. Use the zip: in the unzipped `volve` volume the log folders hold only index files.
   - With the Databricks CLI, copy `volve/Geophysical_Interpretations/Wells/Well_picks_Volve_v1.dat` and the folder `volve/Well_technical_data/Daily Drilling Report - XML Version`.
2. **Import it in the dashboard:** go to **System → Dataset** and open the **Real rig stream for Live Ops: Equinor Volve** block on the North Sea card.
   - Enter the WITSML folder, the reports folder and the picks file (paths on the server), or upload a .zip. Uploads are unpacked into `volve_raw/`, which is gitignored.
   - Tick **import every usable wellbore** and click **Import Volve stream**. The import runs as a background job with progress; the server restarts if the North Sea dataset is in use.
   - **Replay this well** switches which imported wellbore Live Ops replays, without re-reading the export.
3. **Or from a terminal:**
   ```bash
   cd backend
   STRATASENSE_REGION=norway STRATASENSE_DATA_DIR=../data_norway python -m stratasense.cli import-volve-stream "D:/Volve/WITSML Realtime drilling data" --all --ddr "D:/Volve/DDR" --picks "D:/Volve/picks/Well_picks_Volve_v1.dat" --active 15/9-F-14
   ```
   The whole folder takes about 30 minutes, most of it reading 6,800 log-file headers. Pointing it at a single wellbore folder (without `--all`) takes about 80 seconds.
4. **Rebuild** the North Sea knowledge base, so the report reader is refitted and the risk model is retrained with the new wells. Every rebuild re-applies the imported Volve data from `data_norway/public/volve/` (a few MB; the raw export is read only once).

What the import does:
- **Units and cleaning.** It converts units to the ones the detectors use (kkgf → klbf, kN·m → kft·lbf, kPa → psi, L/min → gpm, m³ → bbl, g/cm³ → ppg). It drops the `-999.25` nulls and physically impossible readings (e.g. an ECD of 258 ppg).
- **Replay window.** It resamples to 30 s and keeps the 12 h that drill the most new hole with returns to the rig, which skips riserless top hole where a loss alarm would be meaningless. An operator-coded incident pulls the window onto itself only if that stretch is mostly drilling.
- **Sections.** Boundaries come from where the bit size changes in the depth logs and from the real casing shoes (`wbGeometry`, the most consistent report when there are several). Sections with no casing record are labelled "(typical)". Example, F-14: 26″ → 17½″ with the real 10¾″ string at 1,604 m → 12¼″ to the real 9⅝″ shoe at 2,597 m → 8½″ to the real 7″ liner at 3,695 m.
- **Tops.** The picks are mapped to North Sea groups; base picks and faulted-out, not-reached and eroded picks are skipped. The replayed well's real tops drive its mud-logger picks and are the truth for the top-picking evaluation.

**Replayed well: 15/9-F-14**, 12–13 May 2008, 17½″ section, drilling 1,549 → 1,841 m. The look-ahead predicts the Rogaland top 870 ± 36 m below the bit at 1,601 m; the real pick is 866 m below. At 1,601 m the detectors raise a critical "lost circulation" alert. The rig's report for that morning describes normal drilling from 1,568 to 1,725 m, with shaker-screen plugging and mud overflowing at the shakers shortly before. So the alert is a **false alarm caused by surface mud handling**, the kind a pit-transfer or shaker flag from the rig would suppress.

- **Licence:** Equinor Open Data Licence. It is based on CC BY 4.0; the data may not be sold.
- **Required attribution** (shown on Live Ops): *Real-time drilling data from the Volve field, © Equinor and the former Volve licence partners (ExxonMobil Exploration and Production Norway AS, Bayerngas Norge AS), Equinor Open Data Licence.*

Limits:
- **No coded incidents fall inside a drilling window.** The Volve operator codes 39 interruptions across the wellbores, but they happen during trips, fishing or plugging and abandonment (e.g. F-14's two "lost circulation" codes are from August 2016). So the real replays have no V1… scenarios, and the alarm-budget evaluation has nothing to score on them. The F-14 alert above was checked against the report by hand.
- **3 wellbores are not imported.** 15/9-F-1, F-5 and F-9 have no 12 h stretch with ROP, WOB, SPP, flow and returns recorded together.
- **Some mud weights are defaults.** Where a Volve log has no mud-weight channel (e.g. F-10, F-15), MW falls back to 9.0 ppg, while ECD is the measured value.
- **Derived channels.** StrataSense derives TVD (from the WITSML survey), rig state and d-exponent, plus any channel a log lacks. The Live Ops tooltip lists them.
- **Report reading on real reports is weak.** Scored against the operator's own codes on all 1,759 Volve report-days (23 wells, 160 coded events), extraction agreement is F1 0.07 (precision 0.04, recall 0.23), and adding 20–140 labelled local report-days does not lift it (about 0.05).
  - This is not a clean zero-shot figure: the evaluated model had been refitted on most of these reports.
  - Part of the gap is likely the scoring rule, since many coded events carry depth 0 and matching is by depth.
  - Diagnosing it is the next piece of work before any skill is claimed on real text.

### Score extraction on Volve DDRs

In **Analytics → Real-data check**, upload the Volve drillReport XML files (a folder or a .zip works too). Scoring runs on the server as a background job and the results replace the instructions on that card. Scripted: `cd backend && python -m stratasense.cli validate-volve /path/to/volve/drilling_reports`.

## 3. Real Indian well data: DGH National Data Repository (NDR)

The NDR is India's government E&P data bank, run by the Directorate General of Hydrocarbons. It holds well data for Indian basins, including the Assam-Arakan basin where OIL operates. Students can register as academic users.

**Step by step**

1. **Use an institutional email.** Registration is accepted only with an official email ID. Gmail, Yahoo, Outlook and other personal addresses are rejected. Use your college email.
2. **Register online.** Go to the NDR portal (https://www.ndrdgh.gov.in/NDR/), open the **Data** tab and click **Register**. Fill in the mandatory (*) fields. You get a system email confirming the request.
3. **Email the academic documents** to **indr@dghindia.gov.in**. Registration starts only after they arrive.
   - A **colour scan of your student ID card**, smaller than 100 KB.
   - An **authority letter** from a competent authority (HOD, Dean or Vice-Chancellor) on the institute's official letterhead, **with seal or stamp**, confirming the data is for legitimate academic use (the SIH project). Smaller than 500 KB, colour scan of the original.
4. **Wait for verification.** Allow about **24 working hours** (Mon–Fri, 9:30 AM–5:30 PM IST, excluding public holidays). Login credentials arrive by email.
5. **Log in and browse** the Assam-Arakan basin data, using the help guides inside the portal. Request what StrataSense can use directly:
   - **Well reports / WCRs** (PDF): these go into Ingestion as they are.
   - **Well logs** (LAS or DLIS): gamma ray, caliper, resistivity; these go into correlation and DTW top picking.
   - **Formation tops, deviation surveys, casing and mud data** where available.
   - **Well locations** (lat/lon) for the Offset Map.
6. **Check the access terms.** The NDR states that data is not free for all users, so confirm any fee or usage conditions for academic users before requesting a large package. Keep the data on your machine; do not commit it to the repository.

**Contacts:** indr@dghindia.gov.in · +91-120-2472578 (HoD-NDR / Chief NDR) · +91-120-2472551 (NDR technical support) · DGH, OIDB Bhawan, Plot 2, Sector 73, Noida 201301.

**Tip for the letter:** name the project (SIH 2026, PS 26121, sponsored by Oil India Limited), the purpose (academic research prototype, no commercial use), the data types from step 5, and that data stays on the team's own machine.

## 4. Sources

- Norwegian Offshore Directorate FactPages and open data (NLOD): https://factpages.sodir.no · https://www.sodir.no/en/facts/data-and-analyses/open-data/
- Equinor Volve data sharing: https://www.equinor.com/energy/volve-data-sharing
- DGH National Data Repository: https://www.ndrdgh.gov.in/NDR/ · Registration manual: https://www.ndrdgh.gov.in/NDR/pdf/RegistrationManual.pdf
