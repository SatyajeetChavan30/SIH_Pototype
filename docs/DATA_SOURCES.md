# Data sources: what NWIS needs, where it comes from, and how to get it

The problem statement lists nine data sources "available within OIL". All of them are **OIL-internal and confidential**, and no dataset was published with PS 26121. NWIS therefore ships with three kinds of data:

1. **Synthetic Upper-Assam demo data**, generated on your machine (`python -m nwis.cli build-demo`). This is the default.
2. **Real public stand-ins** from Norway (Sodir FactPages) and Equinor (Volve). They check the pipeline on genuine records.
3. **OIL's own data**, in a pilot. The same importers take it unchanged.

## 1. The nine OIL sources and how NWIS takes them in

| # | OIL source | What NWIS needs from it | How NWIS ingests it (already built) |
|---|---|---|---|
| i | Well Completion Reports (WCRs) | PDFs, scanned or digital | PDF text layer or OCR, then NLP events, lessons and citations (`ingest/pdf.py`, `ingest/ocr.py`, `ingest/pipeline.py`) |
| ii | Daily Drilling Reports (DDRs) | PDFs or WITSML `drillReport` XML | Same NLP pipeline; WITSML importer (`ingest/witsml.py`) |
| iii | Drilling and mud-logging databases | Table export (CSV / SQL) | Loaded into `wells`, `sections` and mud tables, like the Sodir importer (`public/sodir.py`) |
| iv | Historical well parameters and drilling records | Depth-indexed logs (LAS / CSV / WITSML log) | Offset logs for correlation, DTW top picking and analogs |
| v | Reservoir and geological data | Formation tops per well, structure outlines | `tops` table, correlation by formation |
| vi | eRTMAC data streams | WITS-0 over TCP or WITSML 1.4.1 store | Live adapter (`realtime/sources.py`, `NWIS_STREAM=…`) |
| vii | Well trajectory and survey data | MD / inclination / azimuth stations | `surveys` table, minimum-curvature trajectories |
| viii | Casing, cementing and mud programme records | Casing depths and sizes, mud weights, LOT/FIT | `sections` table (casing, MW/ECD), `lot_tests` table |
| ix | Operational event records (losses, kicks, stuck pipe, fishing, NPT) | Event table or free text | Direct load, or NLP extraction from text |

**Getting it:** only through OIL, via the SIH problem-statement mentors or, for a pilot, a data-sharing agreement or NDA. NWIS runs fully on-prem with no cloud calls, so OIL's data never has to leave its network.

## 2. Real public stand-ins (usable now)

| OIL source | Public stand-in | Licence |
|---|---|---|
| i WCRs | Sodir **wellbore history** narratives (completion-report style summaries with real kicks, losses, stuck pipe and fishing) | NLOD 2.0: free reuse with attribution |
| ii DDRs | **Equinor Volve** daily drilling reports (1,759 report-days, WITSML) | Equinor Open Data Licence: attribution, no resale; you accept it on Equinor's site |
| iii / viii mud and casing | Sodir **drilling mud** (depth, mud weight, mud type) and **casing and leak-off tests** (casing size and depth, hole size, LOT/FIT) | NLOD 2.0 |
| v geology | Sodir **lithostratigraphy** (group and formation tops per wellbore) | NLOD 2.0 |
| vii trajectories | Sodir coordinates, TD and maximum inclination (NWIS uses near-vertical wells only) | NLOD 2.0 |
| ix events | Extracted by NWIS from the Sodir histories | NLOD 2.0 |
| vi eRTMAC streams | No public equivalent. The built-in rig simulator sends real WITS-0 frames. | — |

### Build the real-data version (Norwegian North Sea)

```bash
cd backend
# one-off: fetch the five Sodir CSV exports (about tens of MB), cached for offline use
NWIS_REGION=norway NWIS_DATA_DIR=../data_norway python -m nwis.cli build-public --download --quadrants 15,16
# serve it (sign in as usual)
NWIS_REGION=norway NWIS_DATA_DIR=../data_norway python -m nwis.cli serve
```

- **Region.** `NWIS_REGION=norway` switches the stratigraphy to North Sea **groups** (Nordland … Hegre). Well-known formation names map to their group, e.g. Lista → Rogaland and Draupne → Viking.
- **Area.** `--quadrants 15,16` is the Sleipner / Volve / Utsira High area. Use `all` for the whole North Sea.
- **Offline copies.** Put the downloaded CSVs in a folder and pass `--from-folder DIR` instead of `--download`.
- **Required attribution:** *Contains data under the Norwegian licence for Open Government data (NLOD) distributed by the Norwegian Offshore Directorate.*

Limits, stated plainly:
- Only near-vertical exploration wells are used (maximum inclination ≤ 15°), so MD is treated as TVD.
- ECD is not published, so it is set to mud weight + 0.3 ppg.
- The histories are summaries, so incidents are under-reported compared with DDRs.
- There is no public real-time stream.
- North Sea geology is not Assam. This checks that NWIS works on real records; it does not measure accuracy for OIL.

### Score extraction on Volve DDRs

```bash
cd backend && python -m nwis.cli validate-volve /path/to/volve/drilling_reports
```

## 3. Real Indian well data: DGH National Data Repository (NDR)

The NDR is India's government E&P data bank, run by the Directorate General of Hydrocarbons. It holds well data for Indian basins, including the Assam-Arakan basin where OIL operates. Students can register as academic users.

**Step by step**

1. **Use an institutional email.** Registration is accepted only with an official email ID. Gmail, Yahoo, Outlook and other personal addresses are rejected. Use your college email.
2. **Register online.** Go to the NDR portal (https://ndr.dghindia.gov.in), open the **Data** tab and click **Register**. Fill in the mandatory (*) fields. You get a system email confirming the request.
3. **Email the academic documents** to **indr@dghindia.gov.in**. Registration starts only after they arrive.
   - A **colour scan of your student ID card**, smaller than 100 KB.
   - An **authority letter** from a competent authority (HOD, Dean or Vice-Chancellor) on the institute's official letterhead, **with seal or stamp**, confirming the data is for legitimate academic use (the SIH project). Smaller than 500 KB, colour scan of the original.
4. **Wait for verification.** Allow about **24 working hours** (Mon–Fri, 9:30 AM–5:30 PM IST, excluding public holidays). Login credentials arrive by email.
5. **Log in and browse** the Assam-Arakan basin data, using the help guides inside the portal. Request what NWIS can use directly:
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
