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

In the dashboard, open **System → Dataset** (admin), set the quadrants, tick *download from factpages.sodir.no* and click **Build North Sea knowledge base**. The five FactPages CSV exports (tens of MB) are fetched once and kept for offline rebuilds. A server without internet can take the CSV files through the upload box on the same card. When the build finishes StrataSense switches to it. **Switch to this** on either dataset, or the **Synthetic · Assam | Real · North Sea** switch in the header, moves back and forth; the server restarts on the other knowledge base. With no rig stream imported, the switch opens the Offset Map and the header reads "no real-time stream in this dataset".

For scripting, the same from a terminal:
```bash
cd backend
STRATASENSE_REGION=norway STRATASENSE_DATA_DIR=../data_norway python -m stratasense.cli build-public --download --quadrants 15,16
```

- **Region.** The North Sea dataset switches the stratigraphy to North Sea **groups** (Nordland … Hegre). Well-known formation names map to their group, e.g. Lista → Rogaland and Draupne → Viking.
- **Area.** Quadrants `15,16` are the Sleipner / Volve / Utsira High area. Use `all` for the whole North Sea.
- **Offline copies.** Upload the CSVs in the dashboard (or pass `--from-folder DIR` on the command line) instead of downloading.
- **Required attribution:** *Contains data under the Norwegian licence for Open Government data (NLOD) distributed by the Norwegian Offshore Directorate.*

Limits, stated plainly:
- Only near-vertical exploration wells are used (maximum inclination ≤ 15°), so MD is treated as TVD.
- ECD is not published, so it is set to mud weight + 0.3 ppg.
- The histories are summaries, so incidents are under-reported compared with DDRs.
- Sodir has no real-time data. Live Ops needs the Volve stream below; without it, Live Ops shows "No real-time stream".
- North Sea geology is not Assam. This checks that StrataSense works on real records; it does not measure accuracy for OIL.

### Real rig stream for Live Ops: Equinor Volve real-time WITSML

Equinor published the real-time surface-sensor logs of the Volve development wells (block 15/9, inside quadrant 15 above). `import-volve-stream` turns one wellbore of it into the Live Ops replay of the North Sea dataset. The rig simulator can then also send it as WITS-0 frames.

**Status:** the importer is tested on generated files in the Volve format. It has not yet been run on the real download, which may still need channel-name mapping fixes.

1. **Get the data.** Go to https://www.equinor.com/energy/volve-data-sharing, then Databricks Marketplace, then **Volve Data Village**, then **Get instant access**. You need a free Databricks account, and access can take up to an hour to appear.
   - Download the folder **`WITSML Realtime drilling data`** (about 2.8 GB zipped, 26 wellbore folders such as `Norway-StatoilHydro-15_$47$_9-F-14`). One wellbore folder is enough.
   - Optional, for real incidents: download the daily drilling reports (WITSML `drillReport` XML) from **`Well_technical_data`**.
2. **Import it in the dashboard:** go to **System → Dataset** and open the **Real rig stream for Live Ops: Equinor Volve** block on the North Sea card.
   - Enter the folder paths on the server, or upload a .zip of each. Uploads are unpacked into `volve_raw/`, which is gitignored.
   - Click **Scan folder**, pick the wellbore and the replay length, then click **Import Volve stream**.
   - The import runs as a background job with progress. When it finishes, the server restarts if the North Sea dataset is in use.
3. **Or from a terminal:**
   ```bash
   cd backend
   STRATASENSE_REGION=norway STRATASENSE_DATA_DIR=../data_norway python -m stratasense.cli import-volve-stream "/path/to/WITSML Realtime drilling data" --list
   STRATASENSE_REGION=norway STRATASENSE_DATA_DIR=../data_norway python -m stratasense.cli import-volve-stream "/path/to/WITSML Realtime drilling data" --wellbore "15/9-F-14" --hours 12 --ddr "/path/to/drilling reports"
   ```
   - Without `--wellbore`, the command picks a wellbore that has coded incidents, or else the one with the most drilling time logs.
   - With `--ddr`, the operator's coded interruptions (lost circulation, stuck pipe, well control, fishing, tight hole) become Live Ops scenarios **V1, V2…**.
     - The replay window is chosen to contain them, with 30 min before and after each.
     - A jump starts 30 min before the incident.
     - The alarm-budget evaluation scores alerts from 30 min before to 10 min after each incident.
   - It reads the time-indexed logs and converts units to the ones the detectors use (kkgf → klbf, kN·m → kft·lbf, kPa → psi, L/min → gpm, m³ → bbl, g/cm³ → ppg). It drops the `-999.25` nulls.
   - It resamples to 30 s and keeps the most drilling-active window of `--hours`.
   - It adds the wellbore as the active well. Its wellhead position, TD and dates come from Sodir's `wellbore_development_all` table, downloaded once and cached.
4. **Restart the server** after a terminal import (System → Restart, or switch datasets) so it loads the new stream.

The converted window is stored in `data_norway/public/volve/` (a few hundred kB), and every North Sea rebuild re-applies it. The raw export is read only once. `data_norway/` is gitignored, so none of this data is committed.

- **Licence:** Equinor Open Data Licence. It is based on CC BY 4.0; the data may not be sold.
- **Required attribution** (shown on Live Ops): *Real-time drilling data from the Volve field, © Equinor and the former Volve licence partners (ExxonMobil Exploration and Production Norway AS, Bayerngas Norge AS), Equinor Open Data Licence.*

Limits:
- Incidents are the **operator's activity codes** from the daily reports, not hand-checked truth. Their times are to the report's resolution (often 15–30 min). Without the reports there are no scenarios and no alarm-budget score.
- The Volve wellbore's formation tops are predicted from nearby Sodir wells, not picked. There is no depth-indexed offset gamma ray for DTW picking.
- StrataSense derives some channels itself: TVD (from the WITSML survey), rig state and d-exponent, plus any channel a log lacks, such as gamma ray. The Live Ops tooltip lists them.

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
