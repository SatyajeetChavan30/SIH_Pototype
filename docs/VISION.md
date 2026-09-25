# eRTMAC-NWIS: End Goal and Vision

**What NWIS must become at full deployment inside Oil India, how we will know it worked, and why it will beat what exists today.**
SIH 2026 · Problem Statement 26121 · Oil India Limited · Smart Automation

> **North Star:** *No drilling team at OIL is surprised by a hazard that an offset well has already met.*

This document describes the **end state**. For what the prototype does today, see [`SOLUTION.md`](SOLUTION.md). For the market and literature review behind the original design, see [`RESEARCH.md`](RESEARCH.md). References marked `[n]` point to the sources at the end of this file; references marked `[R-n]` point to the sources in `RESEARCH.md`.

---

## 1. The end goal in one paragraph

At the end of this journey, NWIS is **Oil India's institutional drilling memory**. Every Daily Drilling Report, Well Completion Report, mud log, scanned legacy file and senior engineer's hard-won experience since the company began drilling in Assam is turned into structured, cited knowledge. That knowledge is then put to work in three places. Before spud, it gives planners a formation-by-formation hazard brief with honest uncertainty. While drilling, it runs next to eRTMAC and speaks up **before the bit reaches a problem interval**, telling the crew what happened in which offset well, on which report page, and what actually fixed it. After every well, it learns: today's well becomes tomorrow's offset. It runs **on OIL's own servers, without internet access**, and every statement it makes can be traced to a source.

## 2. How the end state answers Problem Statement 26121

| # | Expected outcome in the PS | End-state capability | Prototype status today |
|---|---|---|---|
| i | AI, NLP and OCR to extract and structure information from drilling reports | Whole-archive ingestion (text PDFs, scans, WITSML, spreadsheets) plus **voice capture of expert knowledge**. Local fine-tuning per field and contractor with a small labelling budget | **Built** on synthetic data: NLP with negation and hypothetical handling, OCR, units, review queue ([SOLUTION §3.7](SOLUTION.md#37-evidence-grounded-document-understanding)). **Expert memos** (typed; voice through optional on-prem ASR) with peer review ([§3.15](SOLUTION.md#315-institutional-memory-expert-memos-after-action-reviews-shift-handover)). Needs re-measurement on OIL's archive |
| ii | Interactive map of nearby wells within a user-defined radius | Map with radius **and** structure/fault awareness, 3D trajectory distance, and click-anywhere assessment of a planned location | **Built** (Offset Map view). 3D trajectory distance is roadmap |
| iii | Searchable knowledge repository of events, lessons and mitigations | Hybrid search, knowledge graph and a grounded assistant that answers with statistics and citations and **abstains** when evidence is thin | **Built**: hybrid BM25 + LSA search, query parser, Ask NWIS, knowledge graph ([SOLUTION §3.8](SOLUTION.md#38-query-understanding-without-an-llm)), cited after-action reviews that become lessons ([§3.15](SOLUTION.md#315-institutional-memory-expert-memos-after-action-reviews-shift-handover)) |
| iv | Correlate geological, drilling and reservoir data by depth and formation | **Formation-aligned** correlation with live, automatic top re-anchoring from gamma-ray and ROP | **Built**: formation projection with IDW tops ± σ, re-anchoring from mud-logger picks ([SOLUTION §3.1](SOLUTION.md#31-formation-aligned-look-ahead)). **DTW auto-picking built** as an independent QC (median 8 m error on synthetic tops; [§3.12](SOLUTION.md#312-automatic-formation-top-picking-dtw)) |
| v | Predictive models for mud losses, stuck pipe, overpressure, torque spikes, cementing issues | Calibrated risk by depth for every hazard, an offset-derived mud-weight window, and **causal** estimates of which mitigations work | **Built**: Beta-Binomial ribbon, HistGB model, MW window ([SOLUTION §3.2–3.3](SOLUTION.md#32-uncertainty-aware-risk-ribbon)), **case-mix-adjusted mitigation ranking** and a **what-if planner** ([§3.6](SOLUTION.md#36-what-worked-recommendations-and-analog-replay), [§3.14](SOLUTION.md#314-what-if-planner)). Stuck-pipe ML is weak and currently given zero weight |
| vi | Real-time alerts and recommendations | Physics-residual and statistically calibrated detectors, fused with the look-ahead, with a **stated false-alarm budget** and an audit trail | **Built** on stream replay ([SOLUTION §3.4–3.6](SOLUTION.md#34-physics-informed-real-time-detectors)), with **physics baselines, a conformal alarm budget with digest, and the decision black box** ([§3.11](SOLUTION.md#311-alarm-budget-physics-baselines-and-conformal-calibration), [§3.13](SOLUTION.md#313-decision-black-box)). **Live WITS-0 / WITSML 1.4.1 adapters built** and tested with a rig simulator over TCP ([§3.16](SOLUTION.md#316-live-rig-feed-adapter-next-to-ertmac)); connection to OIL's actual eRTMAC endpoint is the pilot step |
| vii | User-friendly dashboard for field and office staff | RTOC console, rig-site view, planner workspace, tablet client and optional Assamese/Hindi interface | **Built**: seven web views, a cited shift-handover brief, and an **installable rig-site app that works offline** and queues acknowledgements ([§3.18](SOLUTION.md#318-rig-site-offline-app)) |

## 3. Who it serves at full deployment

| User | What changes for them |
|---|---|
| **RTOC / eRTMAC engineer** (Duliajan decision support centre [1][2]) | Watches more rigs with less effort. Gets one fused alert feed where each alert says *why*, *which offsets* and *what worked*, instead of raw channel alarms. |
| **Rig-site company man / tool pusher** | One clear instruction on a tablet that keeps working when the VSAT link drops. |
| **Well planner / drilling engineer** | A hazard brief for any planned location in minutes rather than days, with credible intervals, a mud-weight window and what-if analysis. |
| **Geologist / mud logger** | Predicted formation tops with uncertainty, re-anchored automatically as the well drills. |
| **Knowledge owner / senior engineer** | A way to record what they know before they retire, and a review queue that turns low-confidence extractions into training data. |
| **HSE, management and OISD auditors** | A tamper-evident record of what the system showed, when, to whom, and what was done. NPT trends by formation, field and cause. |

## 4. End-state capability pillars

Each pillar lists what it is, why it matters, and the evidence behind it.

### A. Institutional memory engine
- **Everything, since inception.** DDRs, WCRs, mud logs, casing and cementing records, fishing reports and NPT records, including decades of scanned paper. Ingestion is a one-time batch for the archive, then a daily trickle for active wells.
- **Tacit knowledge capture.** Much of OIL's know-how sits with senior engineers. NWIS lets them record short voice notes ("in Hapjan the Sylhet losses only stopped with cement plugs") in **Assamese, Hindi or English**. Speech is transcribed on-prem with Indic speech models [20][21], linked to wells and formations, and stored as a cited "expert memo" that goes through the same review queue as extracted events.
- **Automatic after-action reviews.** After every NPT event, NWIS drafts a short review (symptoms, timeline, actions, outcome, NPT) from the DDRs and the sensor stream. The engineer corrects and approves it, and it becomes a first-class lesson.
- **Closed loop.** The active well's reports are ingested daily, so every well drilled makes the next one safer.
- **Why it matters.** The PS asks for a system with "institutional memory". Knowledge that depends on who happens to be on shift is lost when people move or retire.
- **Evidence.** Digital-first tools ignore scanned history [R-19]. NLP on drilling reports is well established [R-20][R-21][R-22]. DDR models do **not** transfer between operators without local labels, but about 60 labelled local report-days recovered roughly 90% of the achievable gain in one study [7]. That is why local labelling is built into the plan (Section 7).

### B. Formation-aware foresight
- **Correlate by formation, not depth.** This is already the core of the prototype and remains the backbone. Offset events are projected onto the active well by their relative position inside each formation.
- **Automatic top re-anchoring.** Dynamic time warping (DTW) on LWD gamma ray and ROP re-picks tops while drilling, so the look-ahead shifts without waiting for a manual pick [R-34][R-35].
- **3D trajectory awareness.** Offset relevance uses true 3D distance between wellbore segments, the same structure or fault block, and depletion age, not just surface distance.
- **Why it matters.** In the Assam-Arakan basin, tops shift by hundreds of metres across structures, and pressure regimes change across thrusts [R-6]. A depth-only overlay points the warning at the wrong depth.

### C. Trustworthy alerts
- **A stated false-alarm budget.** Detector thresholds are calibrated with conformal prediction, which gives distribution-free bounds on the false-alarm rate [18]. The RTOC sets the budget, and NWIS keeps alarm rates inside ISA-18.2 guidance for a manageable operator load [25].
- **Physics-residual detectors.** Expected hookload, torque, standpipe pressure and ECD come from hydraulics and torque-and-drag models of the kind in NORCE's OpenLab simulator [19]. Alerts fire on the *residual* between measured and expected values, which is more robust than rolling baselines.
- **Explained, including the unexpected.** Each alert names the channels and offsets that drove it. For patterns no detector was built for, an explanation layer summarises what changed in plain language, following recent work on explainable open-world anomaly detection in oil wells [17].
- **Corroboration.** A symptom inside a formation-aligned look-ahead zone escalates; a symptom alone stays advisory. This is already in the prototype ([SOLUTION §3.5](SOLUTION.md#35-alert-fusion-anti-alarm-fatigue)).
- **Procedure-linked well-control alerts.** Kick and overpressure alerts carry the relevant response checklist from OIL's well-control procedures under the statutory OISD-STD-174 standard [15], so the alert tells the crew what to do, not only what is happening.
- **Lightweight models first.** A 2026 cost-aware study found that classical methods often match time-series foundation models per unit of compute in industrial monitoring [16]. NWIS uses small, explainable models by default and adopts heavier ones only when measured to be better on OIL data.

### D. Causal "what worked"
- **Beyond raw cure rates.** Today's ranking uses cure rate, first-try success and NPT. At the end state, mitigation effects are adjusted for confounders such as loss severity, formation and mud system, so a treatment that is only used on the worst losses is not unfairly penalised.
- **What-if planner.** Planners change the mud weight, casing point or LCM strategy and see the risk ribbon and mud-weight window recompute, with evidence counts.
- **Why it matters.** Engineers already know the list of possible actions. What they lack is evidence of which action worked *in this formation, in this field*. The prototype already shows the effect on synthetic data: fine LCM fails in fractured Sylhet while cement plugs work ([SOLUTION §3.6](SOLUTION.md#36-what-worked-recommendations-and-analog-replay)).

### E. Grounded assistant
- **Tool-using, not free-writing.** A local LLM orchestrates NWIS's own tools (search, statistics, risk, mud-weight window, analogs). TADI (2026) found that domain-specific tool design, not model size, drove answer quality in drilling analytics [6].
- **Cite or drop.** Every sentence must carry a citation to a report page or data record, or it is removed. This citation guard already exists in the prototype.
- **Abstain when thin.** When fewer than a set number of offsets support an answer, NWIS says so instead of guessing.
- **Shift handover brief.** Each shift ends with an automatic, cited summary: depth drilled, events, open alerts, what is coming in the next 150 m.
- **Measured.** Groundedness is scored on a fixed question set, in the spirit of TADI's Evidence Grounding Score [6], and reported in the Analytics view.

### F. Planning and execution as one contract
- The pre-spud **Offset Hazard Brief** becomes the live **watch-list** for that well. If the well departs from the plan (mud weight below the offset-derived kick bound, casing point moved, a formation top far off prognosis), NWIS flags the deviation.
- **Why it matters.** Expert and NGT panels on the Baghjan-5 blowout cited a "mismatch between planning and execution" [R-8][R-9].

### G. Decision black box
- An append-only, time-stamped log of every alert NWIS raised, the evidence shown, who acknowledged it, what action was recorded and what happened next.
- **Why it matters.** After an incident, investigators ask whether early signs were visible and acted on. A decision log answers that objectively, supports OISD audits, and gives the feedback data needed to improve the detectors.

## 5. Built for OIL's reality

| Constraint | End-state design |
|---|---|
| **Data sovereignty** | Fully on-prem and air-gapped. No foreign SaaS and no cloud LLM APIs. Comparable national oil companies keep AI drilling platforms inside sovereign environments; ADNOC hosts its 120-rig SLB RTOC in its own sovereign cloud [4]. Personal data (names in reports, voice notes) handled in line with the DPDP Act, 2023 [26]. |
| **Rig links over VSAT** | OIL's e-RTMAC contract specifies VSAT links from rigs to a decision support centre in Duliajan [1]. A small rig-site edge node keeps detectors and the latest look-ahead running during link outages, stores and forwards data, and sends compact alert payloads. |
| **Integration with eRTMAC, not replacement** | WITS-0 and WITSML 1.4 today (parsers built). A path to WITSML 2.1 and ETP 1.2 streaming, which were designed for secure real-time transfer and OSDU compatibility [12]. |
| **Enterprise data standards** | OSDU-compatible export of wells, trajectories, markers and events [13]. IADC activity and NPT codes for reporting. Alignment with DGH's National Data Repository for well master data [14]. |
| **Regulation and safety** | Well-control content aligned with OISD-STD-174 [15]. Decision black box for audits. |
| **Cost** | Open-source stack, one server class per region, no per-seat licences. |
| **People** | Tablet-friendly rig view. Optional Assamese and Hindi interface and voice input. |

## 6. Why this beats current solutions

### 6.1 Landscape (updated September 2026)

| Solution | Strength | Gap against this PS |
|---|---|---|
| **SLB DrillOps + Lumi / Tela agentic assistant** [3][4][5] | Tela turns structured and unstructured offset-well data into risks and lessons learned. DrillOps RTOC at ADNOC reports 30–40% less engineering effort, 2–3× more rigs per engineer and 4–12 h faster incident response [4] | Tied to the SLB platform and licensing. No published method for formation-aligned projection, uncertainty or source-page citation. |
| **Halliburton DecisionSpace 365 / LOGIX** [11][R-12] | Automation and remote operations, ML for drilling optimisation | Planning and performance focus. No mining of free-text lessons learned. |
| **DrillEdge (case-based reasoning)** [R-14][R-15] | Matches live data to past cases with known fixes | Cases are built by hand. Reported results for lost circulation were modest. |
| **Exebenus** [9] | ML stuck-pipe warnings, deployed on 1,400+ wells | Sensor data only; no document knowledge; commercial licence. |
| **Corva** [10] | Real-time apps, offset benchmarking, parameter recommendations | Performance benchmarking, not hazard knowledge. SaaS. |
| **DeepIQ** [5] | Knowledge-graph assistant that finds comparable wells, historical hazards and mitigations (OMV) | Runs in the customer's cloud. No published live-alerting or uncertainty method. |
| **Kwantis ID3** [5] | LLM-assisted DDR capture and IADC activity coding | Reporting automation, not offset-hazard foresight. |
| **OffsetEye** (rival SIH prototype) [8] | Map, OCR, pgvector RAG with citations, rule-based alerts | Defers ML risk, trajectory correlation, fishing and real eRTMAC integration. No uncertainty or mud-weight window. |
| **DrillScribe** (DDR NPT research prototype) [7] | Honest cross-operator transfer study | NPT ledger only. No map, look-ahead or real-time alerts. |

### 6.2 Feature comparison

"Not published" means we could not find public evidence either way.

| Capability | SLB Tela / DrillOps | DrillEdge | Exebenus | DeepIQ | OffsetEye | **NWIS end state** |
|---|---|---|---|---|---|---|
| Offsets projected **by formation** with top uncertainty | Not published | No | No | Not published | Rule-based only | **Yes** |
| Legacy **scanned** DDR/WCR plus expert voice notes | Unstructured data: yes. Voice: not published | No | No | Documents: yes | OCR: yes | **Yes, both** |
| Risk shown with **uncertainty and evidence count** | Not published | No | Not published | Not published | No | **Yes** |
| Every alert and answer **cited to a report page** | Not published | Case reference | No | Not published | Answers: yes | **Yes, enforced** |
| Mitigations ranked by **outcome**, adjusted for confounders | Lessons surfaced | Case fixes | No | Mitigations surfaced | No | **Yes** |
| **Stated false-alarm budget** | Not published | No | Not published | No | No | **Yes** |
| **Offline / on-prem**, no licence | No (platform) | No | No | Customer cloud | Self-hosted | **Yes** |
| **Decision audit trail** | Not published | No | Not published | Not published | No | **Yes** |

### 6.3 Five things we have not found together anywhere else
1. **Formation-aligned foresight with honest uncertainty.** A warning says "4 of 15 exposed offsets lost returns in this Tipam interval (90% CI …), expected 150 m ahead", not just "high risk".
2. **Knowledge that includes people, not only paper.** Scanned archives and senior engineers' voice notes, in their own language, become cited evidence.
3. **Causal "what worked".** Recommendations are ranked by adjusted outcome, including what *not* to do.
4. **A false-alarm budget and a decision black box.** The RTOC controls alert load, and every decision is reconstructable for OISD and for learning.
5. **Sovereign by construction.** No internet, no licences, open standards, and it fits beside eRTMAC instead of replacing it.

## 7. Success metrics

All numbers below are **targets** for the pilot and full deployment. They are not results. Current prototype results on synthetic data are in [SOLUTION §4](SOLUTION.md#4-measured-results-synthetic-upper-assam-dataset-reproducible).

| Metric | How it is measured | Today (synthetic prototype) | Pilot target | Full-scale target |
|---|---|---|---|---|
| Time to find an offset lesson | Timed task with RTOC engineers, NWIS vs manual search | Manual search takes hours to days (PS statement) | < 2 min | < 30 s |
| Look-ahead coverage | Share of hazard events in pilot wells preceded by an offset-derived warning ≥ 100 m ahead | 3 of 4 replayed hazards | ≥ 60% | ≥ 75% |
| Kick detection lead time | Detector vs conventional pit-volume alarm, on replayed and live data | Overpressure warning ~50 m before the kick | ≥ 5 min earlier | ≥ 10 min earlier, in line with literature [R-31] |
| Alert usefulness | Share of alerts marked "useful" by engineers | Feedback loop built | ≥ 60% | ≥ 75% |
| Alert load | Alerts per console-hour | Budget and digest built; nuisance stress replay 14 → 9 false alarms at 1/h, 4/4 hazards kept | Within ISA-18.2 guidance [25] | Within guidance on every console |
| Extraction quality on OIL archive | F1 on a held-out, hand-labelled set of OIL reports | 0.93 on held-out synthetic phrasing; public Volve check built ([§3.17](SOLUTION.md#317-real-data-check-on-public-equinor-volve-reports)) | ≥ 0.85 after ≤ 100 labelled report-days | ≥ 0.90 |
| Risk model skill | Leave-wells-out ROC-AUC vs "nearest offset well" baseline | 0.89 vs 0.59 (synthetic) | Beat the baseline by ≥ 0.15 | Same, per field |
| Grounding | Share of assistant sentences with a valid citation | Citation guard built | 100% | 100% |
| Hazard-related NPT | NPT hours for losses, kicks, stuck pipe and cementing vs a 3-year field baseline | Not measurable on synthetic data | −10% | −15 to −20% |
| Adoption | Share of RTOC shifts that use NWIS each week | n/a | ≥ 70% | ≥ 90% |

**Why NPT is the headline value metric.** NPT commonly makes up 20–30% of conventional drilling time, stuck pipe alone accounts for over a quarter of NPT [23], and lost circulation can cost 10–20% of overall drilling cost [24]. Even a small reduction in hazard-related NPT repays the system many times over. The value calculator in the Analytics view makes the assumptions explicit for OIL to set.

## 8. Staged path to the end goal

| Stage | Scope | Exit criteria |
|---|---|---|
| **0. SIH prototype** (done) | Synthetic Upper-Assam dataset, seven views, 34 tests. First versions of pillars A–G are built: memos, reviews, handover, DTW QC, physics baselines, alarm budget, what-if, case-mix ranking and the decision log. See [`SOLUTION.md`](SOLUTION.md) | Mechanisms demonstrated and measured on known ground truth |
| **1. Archive pilot** | One field, 3–5 years of DDR/WCR plus master data. Local labelling of about 100 report-days, following the non-transfer finding in [7] | Extraction F1 ≥ 0.85 on OIL data. Risk AUC beats the nearest-offset baseline. Planners confirm hazard briefs match their experience |
| **2. Shadow mode** | Live beside eRTMAC on the 4 e-RTMAC rigs [1]. Alerts go to the RTOC only, not the rig. Decision black box on | Look-ahead coverage ≥ 60%. Alert load within budget. Enough labelled feedback to calibrate detectors |
| **3. Advisory mode** | All Assam and Arunachal rigs. Rig-site tablet view. Voice capture and after-action reviews live | Alert usefulness ≥ 60%. Measurable fall in hazard-related NPT in pilot fields |
| **4. Enterprise** | OSDU-compatible data layer, WITSML 2.1 / ETP 1.2, SSO. Optional federated learning with other Indian operators, which shares model parameters and never raw data, as PDO has prototyped [22] | Full-scale targets in Section 7 |

## 9. Risks and how we reduce them

| Risk | Mitigation |
|---|---|
| Access to OIL archives and master data is slow | Start with one field. The pipeline already runs on public Volve WITSML data, so integration work can start before access is granted. |
| OCR quality on old scans | Cross-document consolidation, confidence scores and a human review queue. DDRs usually repeat what the WCR says. |
| Models trained on synthetic data do not transfer | Expected, not a surprise [7]. Budget for local labelling. Report metrics per field. |
| Alarm fatigue | False-alarm budget, fusion, cool-downs and escalation only on corroboration. Alerts are reviewed in shadow mode before they reach the rig. |
| Over-trust in automation | NWIS advises and never controls the rig. Every alert shows its evidence and its uncertainty, and thin evidence looks thin. |
| LLM hallucination | The LLM is optional. It only rephrases retrieved facts, and uncited sentences are dropped. The core runs without any LLM. |
| Change management | Built with RTOC engineers in shadow mode. Senior engineers are credited as authors of their expert memos. |

## 10. Non-goals

- **No autonomous control** of the rig or the drilling parameters. NWIS is decision support.
- **No replacement** of eRTMAC, the mud-logging unit or OIL's existing databases. NWIS reads from them.
- **No cloud dependency** and no data leaving OIL's network.
- **No field-accuracy claims** until they have been measured on OIL's own data in Stage 1.

---

## Sources

1. Oil India Ltd — IFB CDH6605P21, hiring of e-RTMAC (enhanced Real-Time Monitoring & Analytics Center) for 4 rigs, including VSAT communications and a decision support center at Duliajan. https://www.oil-india.com/files/oldtender/national/NIC_CDH6605P21.pdf
2. Oil India Ltd — Technology and Innovation (eRTMAC). https://www.oil-india.com/leveraging-technology
3. SLB — Tela agentic-AI assistant for offset wells insights. https://www.slb.com/products-and-services/delivering-digital-at-scale/artificial-intelligence-solutions/innovation-factori/tailored-solutions/tela-agentic-ai-assistant-for-offset-wells-insights
4. World Oil (Aug 2026) — ADNOC, SLB deploy AI platform across more than 120 drilling rigs. https://www.worldoil.com/news/2026/8/4/adnoc-slb-deploy-ai-platform-across-more-than-120-drilling-rigs/
5. Drilling Contractor — Generative and agentic AI solutions unlock new insights for drilling (SLB Tela, DeepIQ, Kwantis ID3). https://drillingcontractor.org/generative-and-agentic-ai-solutions-unlock-new-insights-for-drilling-78837
6. TADI: Tool-Augmented Drilling Intelligence via Agentic LLM Orchestration over Heterogeneous Wellsite Data, arXiv 2605.00060 (2026). https://arxiv.org/abs/2605.00060
7. DrillScribe — DDR-to-NPT ledger and cross-operator transfer study. https://github.com/chinmoypaul8897/drillscribe
8. OffsetEye (eRTMAC-NWIS, another SIH team). https://github.com/bishopcommander/OffsetEye
9. Exebenus — Predictive AI for drilling operations. https://www.exebenus.com/
10. Corva — Predictive Drilling. https://www.corva.ai/energy/predictive-drilling
11. Halliburton — LOGIX automation and remote operations. https://www.halliburton.com/en/well-construction/automation-and-remote-operations
12. Energistics — Business value of WITSML v2.1 and ETP v1.2. https://energistics.org/energisticsr-publishes-brochure-extolling-business-value-using-witsml-v21-and-etp-v12
13. The Open Group OSDU Forum — The OSDU Data Platform: a primer. https://osduforum.org/osdu-data-platform-primer-1/
14. DGH — National Data Repository objectives. https://www.ndrdgh.gov.in/NDR/?page_id=503
15. Oil Industry Safety Directorate — OISD standards list (OISD-STD-174, Well Control). https://www.oisd.gov.in/en-in/oisd-standards-list
16. Do Time-Series Foundation Models Pay Off for Industrial Monitoring? A Cost-Aware Empirical Study, arXiv 2608.22968 (2026). https://arxiv.org/abs/2608.22968
17. An Explainable LLM Agent Layer for Open-World Anomaly Detection in Oil Wells, arXiv 2608.04041 (2026). https://arxiv.org/abs/2608.04041
18. Angelopoulos & Bates — A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification, arXiv 2107.07511. https://arxiv.org/abs/2107.07511
19. NORCE — OpenLab Drilling simulator (transient hydraulics, torque and drag, cuttings transport). https://openlab.app/
20. Robust Assamese Speech Recognition through Controlled Fine-Tuning of Whisper Models, arXiv 2607.17164 (2026). https://arxiv.org/abs/2607.17164
21. Sarvam AI — Speech, text and translation models for Indian languages. https://www.sarvam.ai/models
22. Federated Learning: Unlocking Collaborative Machine Learning in Oman's Oil & Gas Sector Without Sharing Sensitive Data, SPE (2026). https://onepetro.org/SPEOGWA/proceedings-abstract/26OPES/26OPES/D021S016R004/799172
23. Mati — Stuck Pipe Prediction and Avoidance, Stanford Geothermal Workshop 2024. https://pangea.stanford.edu/ERE/db/GeoConf/papers/SGW/2024/Mati.pdf
24. ScienceDirect Topics — Loss of Circulation (overview of cost and NPT share). https://www.sciencedirect.com/topics/engineering/loss-of-circulation
25. ANSI/ISA-18.2, Management of Alarm Systems for the Process Industries (alarm-rate guidance per operator console).
26. Ministry of Electronics and IT — Digital Personal Data Protection Act, 2023. https://www.meity.gov.in/data-protection-framework

Earlier sources (`[R-n]`) are listed in [`RESEARCH.md` §7](RESEARCH.md#7-sources).
