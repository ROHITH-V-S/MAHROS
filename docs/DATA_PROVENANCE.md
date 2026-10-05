# Data provenance

> **Generated file.** Produced by `python experiments/build_provenance.py` from the code that builds the network and the data file that feeds it. Do not edit by hand.

Every number the simulator consumes is classified as **observed** (traceable to a named field of a named public dataset), *derived* (computed from observed values by a stated rule), or _assumed_ (an author decision that no dataset supports).

For the Houston metro, TX network: **8 observed, 4 derived, 6 assumed.**

## 1. Source dataset

- **Dataset** — COVID-19 Reported Patient Impact and Hospital Capacity by Facility
- **Publisher** — U.S. Department of Health & Human Services / CDC NHSN
- **Endpoint** — `https://healthdata.gov/resource/anag-cw7u.json`
- **Licence** — U.S. Public Domain (USGOV_WORKS)
- **Dataset provenance flag** — `official`
- **Collection ended** — 2024-05-03
- **Contains patient-level data** — **no**

Facility-level operational reporting. Self-reported by hospitals via HHS TeleTracking or state health departments; not independently audited. Excludes VA, IHS, DoD, psychiatric and rehabilitation facilities by design.

Counts of -999999 are CDC privacy suppression of small numbers. Stored as null: missing, not zero.

## 2. Which hospitals, and who chose them

These are author decisions, not facts the data dictates. They are listed so a reviewer can challenge them, and they are swept in sensitivity analysis.

| Choice | Value |
|---|---|
| Study region | Houston metro, TX |
| Catchment centre | (29.708, -95.4002) |
| Catchment radius | 40 km |
| Minimum inpatient beds | 25 |
| Hospital subtypes kept | Short Term |
| Reference week | 2021-01-10 |
| Facilities selected | **27** |

*Rationale.* A dense tertiary hub (Texas Medical Center) surrounded by community hospitals out to ~38 km -- the hub-and-spoke topology that makes escalation transfer a real problem. Chosen by the authors; radius and bed floor are swept in sensitivity analysis.

### Facilities excluded by judgement

Every facility removed by a judgement call is listed here with its reason. Facilities outside the catchment radius or of a different hospital_subtype are out of scope by declared design and are not listed individually.

| Facility | CCN | Reason |
|---|---|---|
| HARRIS HEALTH SYSTEM | `450289` | HARRIS HEALTH SYSTEM reports 546 inpatient beds at 2525 Holly Hall, an administrative address. Harris Health operates Ben Taub and LBJ as separate hospitals; this row aggregates them. |
| AD HOSPITAL EAST, LLC | `670102` | 21 inpatient beds is below the declared 25-bed floor |
| ALTUS HOUSTON HOSPITAL, LP | `670135` | 7 inpatient beds is below the declared 25-bed floor |
| SUGAR LAND SURGICAL HOSPITAL LLP | `450860` | 7 inpatient beds is below the declared 25-bed floor |
| MEMORIAL HERMANN HOSPITAL SYSTEM | `450184` | MEMORIAL HERMANN HOSPITAL SYSTEM reports 1,526 inpatient beds at 1635 North Loop West, the address of Memorial Hermann Greater Heights (~250 beds). Consistent with system-wide reporting under one CCN, not a single facility. |
| SURGERY SPECIALTY HOSPITALS OF AMERICA SE HOUSTON | `450831` | 12 inpatient beds is below the declared 25-bed floor |
| THE HEIGHTS HOSPITAL | `670129` | 0 inpatient beds is below the declared 25-bed floor |
| TOPS SURGICAL SPECIALTY HOSPITAL | `450774` | 18 inpatient beds is below the declared 25-bed floor |
| FIRST TEXAS HOSPITAL | `670118` | 0 inpatient beds is below the declared 25-bed floor |

Of these, 2 were removed as multi-site system aggregates rather than by a mechanical threshold. That is the most contestable call in the selection, so it is isolated in `SUSPECTED_AGGREGATES` and can be re-included to show it does not drive any result.

## 3. Field-by-field provenance

| Field | Provenance | Source |
|---|---|---|
| `CARDIAC / NEURO / TRAUMA / OBSTETRIC / BURNS` | **observed** | CMS Provider of Services Q1 2021 service certification codes, joined on CCN |
| `CATH_LAB` | **observed** | CMS Provider of Services Q1 2021: CRDC_CTHRTZTN_PRCDR_ROOMS_CNT (actual room count) |
| `ICU_BED` | **observed** | HHS/CDC NHSN facility reporting: total_staffed_adult_icu_beds_7_day_avg |
| `PAEDIATRIC specialty` | **observed** | HHS/CDC NHSN facility reporting: total_staffed_pediatric_icu_beds_7_day_avg > 0 |
| `hospital_id (CCN)` | **observed** | HHS/CDC NHSN facility reporting: ccn |
| `name` | **observed** | HHS/CDC NHSN facility reporting: hospital_name |
| `travel time` | **observed** | OSRM routed driving time over OpenStreetMap between the real coordinates (free-flow; no live traffic) |
| `x, y (km)` | **observed** | HHS/CDC NHSN facility reporting: geocoded_hospital_address, projected about the cluster centroid |
| `WARD_BED` | derived | HHS/CDC NHSN facility reporting: inpatient_beds minus ICU and HDU |
| `admission_mix` | derived | HHS/CDC NHSN facility reporting observed bed composition, normalised by pool length of stay |
| `base_arrival_rate` | derived | per-pool Erlang-B inversion of HHS/CDC NHSN facility reporting observed occupancy against the assumed mean length of stay |
| `tier` | derived | observed inpatient beds against national p55/p88 bed percentiles, gated on observed ICU depth |
| `HDU_BED` | _assumed_ | 0.5 x ICU beds -- not in any public dataset |
| `OR_SLOT` | _assumed_ | 1 per 30 inpatient beds -- not reported |
| `VENTILATOR` | _assumed_ | 0.8 x ICU beds -- not reported |
| `coordinators` | _assumed_ | 1 per 150 inpatient beds |
| `handover overhead` | _assumed_ | 12 min added per journey for patient packaging and crew handover; not reported anywhere; swept |
| `mean length of stay` | _assumed_ | 110 h (~4.6 d) typical US acute ALOS; not reported in HHS data; swept |

8 observed, 4 derived, 6 assumed.

### What the assumed rows mean

The HHS dataset reports beds, ICU beds and occupancy. It reports **no** step-down beds, operating-room slots, ventilators or cath labs, and **no** specialty other than paediatric intensive care. Those are generated from declared ratios.

The honest consequence: MAHROS's *capability* model — which hospital can treat what — is assumed rather than observed, except for paediatrics. Since capability is what makes an escalation transfer necessary at all, this is the most important remaining gap. Closing it means joining CMS Care Compare on the CCN, which every facility record already carries.

## 4. The network as instantiated

27 real hospitals, week of 2021-01-10.

| Hospital | CCN | Tier | Inpatient beds | Adult ICU | Observed occupancy |
|---|---|---|---|---|---|
| Memorial Hermann Texas Medical Center | `450068` | 3 | 1472 | 168 | 0.604 |
| Houston Methodist Hospital | `450358` | 3 | 899 | 158 | 0.948 |
| University Of Texas M D Anderson Cancer Center,The | `450076` | 3 | 785 | 52 | 0.724 |
| Hca Houston Healthcare Clear Lake | `450617` | 3 | 518 | 80 | 0.973 |
| Womans Hospital Of Texas,The | `450674` | 2 | 513 | 4 | 0.573 |
| Chi St Luke'S Health Baylor College Of Medicine Me | `450193` | 3 | 474 | 119 | 0.982 |
| Memorial Hermann Memorial City Medical Center | `450610` | 3 | 463 | 79 | 0.644 |
| Houston Methodist Willowbrook Hospital | `450844` | 2 | 351 | 29 | 0.850 |
| Houston Methodist Sugarland Hospital | `450820` | 2 | 290 | 34 | 0.986 |
| St Joseph Medical Center | `450035` | 2 | 260 | 35 | 0.927 |
| Memorial Hermann Northeast Hospital | `450684` | 2 | 247 | 15 | 0.966 |
| Hca Houston Healthcare Southeast | `450097` | 2 | 243 | 31 | 0.995 |
| Hca Houston Healthcare Northwest | `450638` | 2 | 215 | 35 | 0.999 |
| Memorial Hermann Katy Hospital | `450847` | 2 | 213 | 20 | 0.956 |
| Hca Houston Healthcare West | `450644` | 2 | 199 | 22 | 0.973 |
| Houston Methodist West Hospital | `670077` | 2 | 196 | 21 | 0.990 |
| Memorial Hermann Sugar Land Hospital | `450848` | 2 | 163 | 14 | 0.978 |
| Houston Methodist Clear Lake Hospital | `450709` | 1 | 100 | 13 | 0.941 |
| Oakbend Medical Center | `450330` | 1 | 95 | 12 | 0.935 |
| St Luke'S Hospital At The Vintage | `670075` | 1 | 89 | 12 | 0.974 |
| St Luke'S Sugar Land Hospital | `670053` | 1 | 80 | 15 | 0.982 |
| St Luke'S Patients Medical Center | `670031` | 1 | 71 | 13 | 0.881 |
| Hca Houston Healthcare Medical Center | `450659` | 1 | 64 | 26 | 1.000 |
| Hca Houston Healthcare Pearland | `670106` | 1 | 57 | 8 | 1.000 |
| Texas Orthopedic Hospital | `450804` | 1 | 49 | 0 | 0.431 |
| Houston Physicians' Hospital | `670008` | 1 | 38 | 0 | 0.186 |
| United Memorial Medical Center | `450803` | 1 | 32 | 15 | 1.000 |

**Totals** — 8,176 inpatient beds, 1,029 staffed adult ICU beds, all as reported.

## 5. Scenarios are weeks, not dials

A synthetic scenario is made by turning a `surge_multiplier` up. A real scenario is made by choosing a week these hospitals actually lived through. Every week below was selected by observed network occupancy, restricted to weeks where at least 26 of 27 facilities reported bed counts and at least 22 reported ICU.

| Scenario | Week | What it is |
|---|---|---|
| `houston_calm` | 2020-08-23 | Calmest fully-reported week in the series |
| `houston_baseline` | 2021-01-10 | Reference week; also the HHS summary calibration week |
| `houston_typical` | 2022-05-08 | Median week across the whole series |
| `houston_surge` | 2021-07-25 | Delta wave peak, all 27 facilities reporting |
| `houston_post_pandemic` | 2023-12-10 | Post-pandemic capacity crisis |
| `houston_icu_scarce` | 2021-07-25 | ICU-scarcity **counterfactual** on real topology |
| `houston_smoke` | 2021-01-10 | Fast smoke test |

Worth stating plainly, because it inverts an assumption the synthetic scenarios encoded: **the busiest weeks in this network are not the COVID waves.** Houston ran fuller in late 2023 than in January 2021. The synthetic scenarios were tuned toward 0.70 occupancy; the real network sat between 0.75 and 0.96.

`houston_icu_scarce` is the one real-network scenario that is deliberately counterfactual: real hospitals and real geography, with capacity tightened to a critical-care-scarce regime. It is a *what-if*, not an observation of anywhere, and is labelled as such wherever it is reported.

## 6. What this data cannot establish

Stated here so it reaches a reader before a reviewer states it first.

1. **Self-reported.** Hospitals reported to HHS via TeleTracking or their state health department. Not independently audited.
2. **The mandate ended 2024-05-03.** There is no facility-level successor; CDC's current NHSN respiratory dataset is jurisdiction-level. The evaluation window is 2020–2024.
3. **Excludes VA, Indian Health Service, DoD, psychiatric and rehabilitation facilities** by dataset design. A real Houston transfer network includes VA hospitals; this one cannot.
4. **Bed counts are 7-day averages of *staffed* beds**, so they move week to week and can exceed licensed capacity during a surge.
5. **No behaviour data.** Nothing here observes how hospitals respond to a transfer request, whether they refuse, or whether they refuse honestly. The strategic-refusal model is a declared assumption whose parameters are swept, and it is the central thing this project simulates rather than measures.
6. **No clinical timing data.** Deterioration timing, safe windows and length-of-stay distributions are modelling assumptions. Length of stay is scaled to a typical US acute figure rather than measured; calibrating it against HCUP or MIMIC-IV is follow-on work.
7. **Travel times are straight-line**, derived from real coordinates with a fixed handover overhead, not road-network routing.

The claim this data *does* support is narrow and worth stating exactly: **the network's capacity, geography and load are real, and the simulation reproduces the occupancy these hospitals actually reported.** Everything about negotiation behaviour remains simulated.
