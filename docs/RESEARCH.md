# eRTMAC-NWIS — Research Dossier

**SIH Problem Statement 26121 · Oil India Limited · Theme: Smart Automation**
*Nearby Wells Intelligence System: an AI-powered offset-well knowledge and decision-support platform for drilling operations*

This document covers what exists today, what the literature says works, where current solutions fall short, and how NWIS is designed to close those gaps. Sources are listed at the end, and every numbered reference `[n]` points there.

---

## 1. The problem in one paragraph

OIL's **eRTMAC** (enhanced Real-Time Monitoring & Analytics Centre) already streams rig sensor data. Engineers monitor it around the clock through analytics and visualisation dashboards [1][2]. What eRTMAC lacks is **context from the offset wells**. Knowledge of past losses, kicks, stuck pipe and cementing failures lives in Well Completion Reports, Daily Drilling Reports, scanned PDFs, and in the memories of experienced engineers. Getting at it is slow and depends on individual people. When a well is drilling toward a problem interval, nobody is automatically told that "4 of the 6 nearest wells lost returns 40 m below here, and here is what fixed it".

## 2. OIL operating context (why generic tools are not enough)

| Aspect | What the literature says | Design implication for NWIS |
|---|---|---|
| Basin | Upper Assam Shelf and the Assam-Arakan fold-thrust belt (Belt of Schuppen). OIL operates Naharkatiya, Moran, Jorajan, Hapjan, Baghjan, Kathaloni, Tengakhat and others [3][4] | Wells cluster by **structure**. A same-structure offset is worth more than a closer one across a fault. |
| Stratigraphy | Alluvium → Dhekiajuli → Namsang → Girujan Clay → Tipam Sst → Barail (sand-shale-coal) → Kopili → Sylhet (limestone) → Langpar/Lakadong → basement. Production comes mainly from Tipam and Barail, with newer finds in Langpar/Lakadong [3][4] | Correlate by **formation**, not by measured depth. Tops shift hundreds of metres between structures. |
| Typical hazards | "Practically all types of problems like high pressure, lost circulation, stuck pipe and hole instability" are met in Upper Assam [5]. Girujan and Tipam hold hydratable, dispersible clays [5] | The ontology must cover losses, kicks and overpressure, stuck pipe (differential, pack-off, mechanical), tight hole and bit balling, instability and cavings, cementing issues, and fishing. |
| Pressure regime | Shelf formations are near-hydrostatic, with gradients rising slightly in Sylhet and Langpar [6]. In the Schuppen belt, supra-thrust rocks at 700–1400 m carry 9.6–19.5 MPa, and sub-thrust **Barail is overpressured from about 3,700 m (48–54 MPa)** [6] | Location-dependent pressure makes a single MW template unsafe. The system needs **offset-derived, location-specific mud-weight windows**. |
| Casing and mud practice | The 9⅝″ shoe is set in the Tipam-bottom shale to isolate the differential-sticking-prone layers. Tipam-bottom shale needs about 10.0–10.5 ppg, Barail shale about 10.5–11.0 ppg vertical and 11.2–11.8 ppg as deviation increases [5]. KCl-PHPA-glycol systems are common in the region [7] | Risk must account for **trajectory (inclination)**, and the MW window must be shown against inclination. |
| Safety culture | The Baghjan-5 blowout (May 2020) burned for more than 5 months. Expert and NGT panels cited a **"mismatch between planning and execution"** and asked why early signs were not acted on [8][9] | NWIS must link the **pre-spud plan** to **live execution** and escalate early signs quickly, with traceable evidence. |

## 3. Existing solutions: landscape and gaps

| Solution | What it does well | Gaps relative to this problem statement |
|---|---|---|
| **SLB DrillPlan / DrillOps** (DELFI) [10][11] | Offset-well analysis, automated reporting, well montage, and AI-driven predictive analytics with embedded automation | Cloud and platform lock-in (DELFI), and costly for a PSU. Offsets are compared mostly on digital data, and legacy scanned WCRs/DDRs need separate digitisation. |
| **Halliburton DecisionSpace 365 Well Construction, LOGIX, WellPlan** [12][13] | Links offset analysis to engineering models (T&D, hydraulics). LOGIX uses ML for directional tendency | Oriented to planning and engineering. No lessons-learned mining of free-text reports; vendor ecosystem. |
| **DrillEdge (Verdande → Halliburton)** [14][15] | Case-based reasoning: matches live data to historical "cases" and suggests known mitigations | Cases must be **built by hand by experts**, and the method is not tied to formation or geology. Tests showed strong results on stuck pipe but only "modest" ones on lost circulation [15]. |
| **Corva** [16] | Real-time apps; parameter overlay from an offset well; BHA benchmarking; offset visualisation | Offset **performance** benchmarking (ROP, BHA, cost) rather than hazard knowledge. US-shale focus, SaaS. |
| **Exebenus Spotter / Pulse on Kongsberg SiteCom** [17] | ML agents for stuck pipe, washout, losses and hole cleaning. Claims >96% of stuck-pipe incidents pre-warned across 1,400+ wells | Black-box alerts, sensor data only (no document knowledge), commercial licence plus SiteCom dependency. |
| **Another SIH team: "OffsetEye"** (same PS) [18] | Map, OCR/NLP extraction, pgvector semantic search, rule-based depth/formation alerts, citations | Its MVP **defers** trajectory correlation, fishing operations, ML risk scoring and real eRTMAC integration [18]. Alerts are deterministic MD/formation rules, with no uncertainty, mud-weight window or analog outcomes. |

**Common gaps across the market:**
1. **Depth-naive offset comparison.** Offsets are overlaid by MD or TVD, but hazards follow **formations**, whose tops move by hundreds of metres across structures.
2. **Knowledge stays in PDFs.** Digital-first tools ignore decades of scanned WCRs and DDRs, and LLM digitisation work (MEOS 2025) targets batch spreadsheets, not live decision support [19].
3. **No uncertainty.** A risk score from 1 offset well looks the same as one from 12. Engineers cannot tell thin evidence from strong evidence.
4. **Alerts without "so what".** Detectors say *something is wrong* but not *what worked last time*, *in which well*, or *per which report page*.
5. **Planning and execution are disconnected.** Offset analysis is a planning artefact that is rarely consulted automatically while drilling. Baghjan showed the cost of that gap [8].
6. **Data sovereignty.** PSU operational data cannot casually go to foreign SaaS or cloud LLM APIs.

## 4. What the literature says works

| Topic | Key findings | How NWIS uses it |
|---|---|---|
| NLP on drilling reports | Sentence classification into **EVENT / SYMPTOM / ACTION** across hundreds of wells [20]. NLP + ML predicts NPT type from D&C reports [21]. Deep-NLP root-cause analysis of NPT (ATCE 2025) [22]. Anomaly detection in daily reports [23]. LLM + few-shot DDR digitisation cut analysis "from months to hours" [19] | A hybrid extractor: domain lexicon, rules and an ML classifier, with **negation and hypothetical handling**, symptom/event/action roles, and span-level citations. An LLM is optional, never required. |
| Stuck pipe | No single universal leading indicator; multiple indicators reduce false alerts. A real-time T&D/hydraulics-deviation method gave a mean 38-minute warning on 36 incidents [24][25]. A 2026 hybrid physics + AI agent approach [26] | Stuck-pipe **risk index** built from torque trend, overpull, SPP pack-off and ROP change, plus formation look-ahead. |
| Lost circulation | ML on offset DDR data can predict losses **at the planning stage** [27]. Random Forest and Extra Trees rank best for loss intensity [28]. Explainable probabilistic ML for losses [29] | Offset-evidence risk ribbon, gradient-boosted model, and a **P(loss \| ECD)** curve per formation. |
| Kick detection | Differential flow is a more sensitive early indicator than pit volume. Trend analysis on calibrated volumes cuts false alarms [30]. ML warns 10–12 minutes earlier than conventional systems [31] | Kick detector on the flow-out delta trend, pit gain and gas, with hysteresis and pump-state gating. |
| Overpressure | The **corrected d-exponent (dxc)** deviates from its normal-compaction trend when the bit enters overpressure [32][33] | Real-time dxc against a normal-trend line, plus a gas-based overpressure warning. |
| Well correlation | Dynamic time warping on GR logs correlates wells and picks tops automatically (≥97% alignment reported) [34][35] | Formation-top prediction for the active well, re-anchored as tops are penetrated. DTW re-anchoring is on the roadmap. |
| Case-based reasoning | DrillEdge showed that matching live data to past cases gives early warnings with known fixes [14][15] | **Analog Replay**: automatic case capture from offset wells, kNN matching, and "what happened next" with mitigation outcomes. |
| Knowledge graphs + RAG | Graph-structured retrieval reduces LLM hallucination and improves reasoning [36]. The TADI agentic LLM over Volve wellsite data (2026) [37] | Well→Formation→Event→Cause→Mitigation→Outcome graph. Retrieval is grounded, and a citation guard removes uncited LLM text. |

## 5. Gap → NWIS feature matrix

| # | Gap | NWIS answer | Status in prototype |
|---|---|---|---|
| 1 | Depth-naive offsets | **Formation-aligned look-ahead.** Offset events are projected by relative position within a formation onto the active well's predicted tops (IDW with uncertainty band), then converted TVD → MD with minimum curvature | Built |
| 2 | No mud-weight context | **Probabilistic offset-derived MW window.** Logistic P(loss \| ECD) and P(kick \| MW) per formation from censored offset evidence. The safe window is shown live against the current MW/ECD | Built |
| 3 | No uncertainty | **Beta-Binomial risk ribbon.** Offset weights come from distance, same-structure, recency (depletion) and data quality. Only wells that drilled the interval count. The output is p with a 90% credible interval and the evidence count | Built |
| 4 | Alerts without "so what" | **Evidence drawer.** Every alert cites offset events, source document and page, and recommends mitigations ranked by historical cure rate and median NPT, including actions to avoid | Built |
| 5 | Manual case building (DrillEdge) | **Analog Replay.** Cases are captured automatically from offset logs, then matched by kNN to show "what happened next" | Built |
| 6 | Knowledge locked in PDFs | NLP/OCR ingestion with negation handling, unit normalisation, cross-day consolidation, confidence scores and a human review queue | Built |
| 7 | Plan vs. execution | **Pre-spud Offset Hazard Brief** (DWOP pack), and the same risk model drives live alerts | Built |
| 8 | Alert fatigue | Fusion, de-duplication, hysteresis, escalation when look-ahead and symptoms corroborate each other, and an engineer feedback loop | Built |
| 9 | Data sovereignty | Runs fully air-gapped with no LLM needed. An optional local LLM (Ollama) runs on-prem | Built |
| 10 | Integration | WITS-0 parser and WITSML 1.4 drillReport importer (Volve-compatible). eRTMAC data enters through a stream-source adapter | Built (parsers); live eRTMAC connection is the roadmap |

## 6. Why the prototype uses synthetic Assam data

OIL's WCRs and DDRs are proprietary and no dataset is linked to the problem statement. The prototype therefore ships a **deterministic synthetic Upper-Assam dataset** calibrated to the geology and hazards above. It includes latent pore, fracture and collapse fields, and hazard hot-spots such as depletion-driven Tipam losses, Girujan bit balling, Barail coal instability and SE overpressure, and Sylhet fractured-limestone losses. Because the ground truth is known, we can **measure** extraction F1, MW-window recovery and risk-model skill honestly, which is not possible with real, unlabelled data. The same pipeline accepts real data: PDFs through NLP/OCR, and WITSML DDR XML such as the public Equinor Volve dataset [37][38].

Every synthetic record is labelled **SYNTHETIC** in the UI and documents. Well names are fictitious.

## 7. Sources

1. Oil India Ltd — Drilling. https://www.oil-india.com/drilling
2. Oil India Ltd — Technology & Innovation / Digitalization. https://www.oil-india.com/leveraging-technology · https://www.oil-india.com/digitalfootprint
3. DGH — Assam-Arakan Basin. https://dghindia.gov.in/assets/downloads/56cc43934337fAssam-Arakan_Basin.pdf
4. USGS Bulletin 2208-D — Sylhet-Kopili/Barail-Tipam Composite Total Petroleum System. https://pubs.usgs.gov/bul/2208/D/b2208-d_508.pdf
5. Drilling fluid technology in Oil India (ETDEWEB) and regional MW/casing practice. https://www.osti.gov/etdeweb/biblio/5503884
6. Estimation of pore pressure, tectonic strain and stress magnitudes in the Upper Assam basin, *Geophys. J. Int.* 216(1), 2019. https://academic.oup.com/gji/article/216/1/659/5144768 · Dasgupta et al., Pore pressure modelling in a compressional setting: Assam, *J. Pet. Geol.* 2019. https://onlinelibrary.wiley.com/doi/abs/10.1111/jpg.12736
7. Field notes on KCl-PHPA-glycol mud in Assam/Tripura. http://wwwprojectsivsagar.blogspot.com/p/our-mud-system-is-glycolkclphpa-mudthis.html
8. The Wire — Expert panel report on Baghjan blowout. https://science.thewire.in/environment/oil-india-baghjan-blowout-expert-panel-report-national-green-tribunal-precious-time/
9. Analytical case study of the Baghjan blowout, *Environ. Monit. Assess.* 2024. https://link.springer.com/article/10.1007/s10661-024-13070-7
10. SLB DrillPlan. https://www.slb.com/products-and-services/delivering-digital-at-scale/software/delfi/delfi-solutions/drillplan
11. SLB DrillOps. https://www.slb.com/products-and-services/delivering-digital-at-scale/software/delfi/delfi-solutions/drillops
12. Halliburton Digital Well Construction (DecisionSpace 365). https://www.halliburton.com/en/software/decisionspace-365-enterprise/digital-well-construction
13. Halliburton WellPlan. https://www.halliburton.com/en/products/engineers-desktop-suite/wellplan-software
14. Case-Based Reasoning: Predicting Real-Time Drilling Problems, SPE-141598-MS. https://www.onepetro.org/conference-paper/SPE-141598-MS
15. An overview of case-based reasoning applications in drilling engineering. https://www.researchgate.net/publication/257512759 · Rigzone on DrillEdge onshore. https://www.rigzone.com/news/artificial_intelligence_software_aids_decisionmaking_in_onshore_drilling-10-jul-2014-133973-article/
16. Corva 2023 App Bundle (offset benchmarking, parameter comparison). https://www.corva.ai/blog/corvas-new-2023-app-bundle-maximizes-drilling-efficiency
17. Exebenus (Spotter ML Stuck Pipe on Kongsberg SiteCom). https://kongsbergdigital.com/partners/partner-solutions/exebenus · https://www.exebenus.com/
18. OffsetEye (eRTMAC-NWIS, another SIH team). https://github.com/bishopcommander/OffsetEye
19. Digitization of Daily Drilling Reports Using LLMs, SPE MEOS 2025. https://onepetro.org/SPEMEOS/proceedings-abstract/25MEOS/25MEOS/790010
20. Sequence Mining and Pattern Analysis in Drilling Reports with Deep NLP, SPE ATCE 2018. https://arxiv.org/pdf/1712.01476
21. ML and NLP for Automated Analysis of Drilling and Completion Data, SPE-192280-MS. https://onepetro.org/conference-paper/SPE-192280-MS
22. Deep NLP for Automatic Root Cause Analysis of NPT Events in Drilling Reports, SPE ATCE 2025. https://onepetro.org/SPEATCE/proceedings-abstract/25ATCE/25ATCE/D021S020R008/792137
23. Drilling and Completion Anomaly Detection in Daily Reports by Deep Learning and NLP, URTeC 2020. https://onepetro.org/URTECONF/proceedings-abstract/20URTC/20URTC/D023S027R004/448910
24. Stuck-Pipe Prediction by Use of Automated Real-Time Modeling and Data Analysis, SPE Drilling & Completion 32(03). https://onepetro.org/DC/article/32/03/184/205973
25. Review of Stuck Pipe Prediction Methods and Future Directions, SPE Journal 30(06), 2025. https://onepetro.org/SJ/article/30/06/3334/649145
26. Enhanced Real-Time Stuck Pipe Prediction Using Hybrid Physics + AI Agents, SPE/IADC DC 2026. https://onepetro.org/SPEDC/proceedings-abstract/26DC/26DC/D031S020R003/796098
27. ML Prediction of Lost Circulation Events at the Well Planning Stage, OTC Asia 2024. https://onepetro.org/OTCASIA/proceedings-abstract/24OTCA/24OTCA/D021S015R007/541868
28. Lost circulation intensity characterization using ML and well logs, *Heliyon* 2024. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11699355/
29. Explainable Probabilistic ML for Predicting Drilling Fluid Loss of Circulation. https://arxiv.org/pdf/2511.06607
30. Trend Analysis for Calibrated Active System Volume and Differential Flow Reduces False Kick Alarms, APOGCE 2024. https://onepetro.org/SPEAPOG/proceedings-abstract/24APOG/24APOG/D031S019R005/570425
31. Intelligent Kick Warning Model Based on Machine Learning, *Processes* 13(7), 2025. https://doi.org/10.3390/pr13072162
32. Corrected d-exponent. https://en.wikipedia.org/wiki/Corrected_d-exponent
33. Real-Time Pore Pressure Detection: Indicators and Improved Methods, *Geofluids* 2017. https://onlinelibrary.wiley.com/doi/10.1155/2017/3179617
34. Automatic geological formation tops picking using DTW, US11914099B2. https://patents.google.com/patent/US11914099B2/en
35. Automated multi-well stratigraphic correlation using relative geologic time, *Basin Research* 2023. https://onlinelibrary.wiley.com/doi/full/10.1111/bre.12787
36. Knowledge-graph-enhanced RAG for FMEA. https://arxiv.org/pdf/2406.18114
37. TADI: Tool-Augmented Drilling Intelligence via Agentic LLM Orchestration over Heterogeneous Wellsite Data (2026). https://arxiv.org/html/2605.00060v1
38. Volve WITSML/DDR exploration notes. https://github.com/f0nzie/volve-drilling/blob/master/notebooks/witsml-howto.md
