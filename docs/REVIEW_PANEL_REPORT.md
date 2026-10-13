# MAHROS — Hostile Review Panel Report

Written 2026-10-12 for the MAHROS team, about the code in this repository
(`Desktop/MAHROS`, branch `main`, commit `2de3ebb`). Every claim about the code
was checked against the code or by running it. Numbers marked **[measured
today]** came from runs made while writing this report.

> 🧒 **In one breath:** MAHROS is a clever, well-tested *simulation* about
> hospitals passing patients to each other and catching hospitals that fib
> about being full. The hospitals are real. Their behaviour is invented. There
> is no database. The "fib catcher" works, but mostly on fibs we invented.
> All three of those can be fixed, and none of the fixes needs ML.

---

## PHASE 1 — Understanding the project

### A. Project summary
MAHROS is a decentralised multi-agent system for **interhospital escalation
transfers**. These are patients already admitted who get worse and need care their
hospital cannot give right now (ICU bed, cath lab, neurosurgery). The origin
hospital asks up to 8 nearby hospitals **at the same time** using the Contract
Net Protocol (CNP). Each hospital's agents answer from private state. Any
refusal can be **challenged**: capability claims ("we don't do cardiac") are
checked against a public directory (CMS Provider of Services). Capacity claims
("we're full") are checked against the hospital's own past acceptances in a
tamper-evident hash-chain ledger. Disputes are settled with Dung's grounded
argumentation semantics. A fairness term spreads the burden, and an optional
LLM only writes explanations.

The network has **27 real Houston hospitals** (HHS facility reports, CMS
capability, OSRM road times). Patient arrivals, deterioration and hospital
*behaviour* are simulated.

### B. Problem statement
When an admitted patient deteriorates, finding a receiving hospital is done
today by a human who phones hospitals one at a time. It is slow, stale and
costly in staff time. Worse, receiving hospitals have an incentive to refuse
(a bed is scarce), and nobody can check whether a refusal is honest.
**Research question:** can autonomous hospital agents allocate transfers as
well as a central optimiser *without* pooling private bed data, and stay
robust when some hospitals refuse dishonestly?

### C. Current architecture

| Layer | Code | What it is really |
|---|---|---|
| Resource agents (6 per hospital) | `hospital/agents.py` | Feasibility + convex opportunity-cost pricing |
| Bidding policies | `hospital/behaviours.py` | honest / strategic (lies) / defensive (cautious) |
| Contract Net | `negotiation/cnp.py` (916 lines) | announce → bid → deliberate → award → confirm |
| Challenge engine + argumentation | `negotiation/challenge.py`, `argumentation.py` | the "lie detector" |
| Attestation, collusion | `negotiation/attestation.py`, `collusion.py` | signed refusals, cartel attack, spot checks |
| Fairness | `fairness/metrics.py` | Gini/Jain burden balance |
| Ledger | `ledger/interface.py` | **in-memory** Merkle hash chain; Solidity contract present but **not wired** |
| LLM | `llm/` | explanation only, off the decision path |
| Privacy | `privacy/` | Presidio/HMAC anonymiser, bit-level leakage metric |
| Baselines | `sim/strategies.py` | phone tree, central greedy, batched optimal (Hungarian), nearest, none |
| Real data | `calibration/`, `sim/real_network.py` | 27 Houston hospitals, 216 weeks |
| Server | `server/app.py`, `live.py` | FastAPI + WebSocket live console |
| Storage | — | **No database.** State in memory, results as JSON |

### D. Current workflow
1. A real week is picked (e.g. Delta peak, 2021-07-25). Each hospital's beds
   and ICU come from its HHS report.
2. Simulated patients arrive. Arrival rates are fitted so that simulated
   occupancy matches the reported occupancy (MAE 3.95 pts, R² 0.953).
3. A patient deteriorates and a transfer request is created.
4. The origin sends a CFP to up to 8 peers within reach of the clinical window.
5. Each peer computes the truth, then its *policy* decides what to say.
6. Refusals are screened. Contradicted ones are challenged. The hospital
   defends (or signs an attestation). The grounded extension decides what
   survives.
7. The best bid is scored (time 0.45, capability 0.25, strain 0.15,
   fairness 0.15), awarded and confirmed. The agreement or refusal is written
   to the ledger.
8. Metrics: success rate, time to care, coordinator minutes, Gini, leakage.

### E. Agent responsibilities

| Agent | Responsibility |
|---|---|
| Resource agent | Can I take this patient? What does it cost me? |
| Hospital agent (+ policy) | Decide what to *report*. Defend refusals when challenged |
| Origin / manager (CNP) | Announce, collect bids, award |
| Challenge engine | Find contradictions between refusals and the record (a component, **not** an agent today) |
| Fairness monitor | Track who carries the burden |
| LLM coordinator | Explain contested awards |

### F. Strengths (real ones)
1. **Real hospitals, real geography, real load**: 27 named facilities, OSRM
   routes, CMS capability, validated against reported occupancy.
2. **Statistical honesty well above student level**: paired seeds,
   Holm correction, TOST equivalence, ablations, sensitivity sweeps.
3. **164 tests pass [measured today].**
4. **Negative results are reported**: a plain shared registry ties MAHROS when
   everyone is honest; collusion breaks the challenge; spot checks once
   punished honest hospitals.
5. **The ledger is load-bearing**: removing it costs as much as removing the
   argumentation. That is a real design argument, not decoration.
6. **Genuine MAS features**: private state, point-to-point FIPA performatives,
   a message bus that tests "nobody sees everything".
7. **Live console** drives the real engine, not a JS re-implementation.

### G. Weaknesses
1. **No database.** The reviewer already asked for PostgreSQL. Nothing changed.
2. **The behaviour under study is invented.** Which hospitals lie, when, and
   whether a liar can defend itself are all author choices.
3. **The headline "lie detector" result rests on an assumption**:
   `BiddingPolicy.defend()` returns `False` for any lie
   (`behaviours.py:95`). The code's own docstring calls it "optimistic".
4. **"Zero false accusations" holds by construction.** A defensive hospital's
   refusals are true by definition, so it can always defend. A reviewer will
   call this a tautology, not a result.
5. **The audit is not a full audit.** Only agreements and refusals are
   recorded. Console actions that change state (`set_occupancy`,
   `set_policy`, fairness and argumentation toggles) leave **no trace**.
   Everything is lost on restart.
6. **The documentation contradicts itself** in at least 11 places (list in
   Phase 2, issue 1).
7. **One city only.** Generalisation is untested.
8. **Hospitals start empty** in every run (the README admits it).
9. **Feature sprawl**: LLM providers, Presidio, Solidity, bit-leakage,
   collusion, attestation. That is too many contributions for one paper.

### H. Academic contributions (what is actually new)
1. Treating a transfer refusal as a **falsifiable claim** checked against
   public capability plus the hospital's own commitment record. Accountability
   without pooling occupancy data. *This is the core novelty.*
2. Measured cost of decentralisation: about 1 point of success vs an
   omniscient greedy optimiser, equivalent within ±2.
3. Evidence that collusion defeats reactive checks and that **proactive spot
   checks** fix it.
4. A conservative real-network evaluation framework (observed / derived /
   assumed labels, generated provenance).

### I. Features weak or disconnected from the title
- **LLM coordinator**: no optimisation role. Its explanations are templates by default.
- **Presidio anonymiser**: there is no patient data to anonymise.
- **Solidity contract**: not wired; "blockchain" invites questions you can't win.
- **Bit-level privacy leakage**: interesting, but it is about privacy, not
  resource optimisation.
- **Real-time latency benchmark**: measures Python speed, not hospital reality.

---

## PHASE 2 — Brutal reviewer analysis

| # | Issue | Severity | Why a reviewer attacks it | Impact |
|---|---|---|---|---|
| 1 | **Docs contradict each other** (see list below) | **Critical** | One contradiction lets a reviewer distrust *every* number | Can sink the review alone |
| 2 | **No PostgreSQL / no persistence** | **Critical** | Explicitly requested last time and still absent; "backend not verifiable" | Seen as ignoring feedback |
| 3 | "Lie detector" proven on invented lies, with `defend()=False` assumed | **High** | Circular: "we built liars, then caught the liars we built" | Core contribution looks staged |
| 4 | "Zero false accusations" is true by construction | **High** | Stated as a result, but it is a definition | Credibility hit if presented as an achievement |
| 5 | Phone-tree 25% staleness is unsourced and drives part of the gap | **Medium** | "You made the baseline weak" | Survives (see Phase 4), *if* you show it |
| 6 | Unaudited user actions in the console | **High** | The demo's key moment (make a hospital lie) is not logged | Audit claim falls during the demo |
| 7 | MAHROS ≈ plain shared registry when honest (94.1 vs 94.6 [measured today]) | **Medium** | "Why not just build a registry?" | Fine *if* you say it first (README does) |
| 8 | Behaviour is simulated, with no real refusal data | **High** | "Simulated data" was criticised before | Needs real-world grounding (Phase 5) |
| 9 | LLM / Presidio / Solidity / leakage modules | **Medium** | "Added for buzzwords" | Dilutes focus; invites off-topic questions |
| 10 | Single city (Houston) | **Medium** | "Does it generalise?" | Easy to fix, see Phase 9 |
| 11 | Hospitals start empty | **Medium** | Inflates absolute success rates | Admitted, but unfixed while the data to fix it is vendored |
| 12 | Report title says "Orchestration"; registered title says "Optimization" | **Medium** | Title mismatch on the cover page | Easy to fix, but looks careless |
| 13 | No formal optimisation problem statement | **Medium** | "Where is the *optimization* in Resource Optimization?" | Add an objective + constraints section |
| 14 | 6 resource agents per hospital may be "objects called agents" | **Low–Med** | Classic MAS criticism | Defend with private state + messages |
| 15 | 3 assumed resource types (HDU, OR, ventilators) by ratio | **Low** | Honest labels already present | Low |

**Doc contradictions (issue 1). Each one is a free attack for a reviewer:**

| Topic | Says A | Says B |
|---|---|---|
| Travel time | OSRM routed (README §"Travel time is routed"; DATA_PROVENANCE §3) | "Travel times are straight-line" (README limitations; DATA_PROVENANCE §6.7) |
| Capability | Observed from CMS POS (README, DATA_PROVENANCE §3 table) | "Capability is assumed, not observed" (README limitations; DATA_PROVENANCE §3 note) |
| Collusion | Modelled, README §11 | "Collusion is not modelled" (README limitations; MODEL_ASSUMPTIONS §9) |
| Equivalence to batched optimal | EQUIVALENT, TOST p=0.0029 (README §3) | Not established, p=0.27, MAHROS +1.72 (README limitations); p=0.10 (MODEL_ASSUMPTIONS §11) |
| MAHROS vs nearest | +0.27, p=0.34 (README §3) | +0.68, p=0.24 (limitations, PANEL_GUIDE) |
| Effect size vs phone | dz = 12.09 (README) | dz = 3.80 (MODEL_ASSUMPTIONS) |
| Coordinator minutes | 48.3 vs 1.2 (Project-I report) | 57.5 vs 0.9 (PANEL_GUIDE); 68.3 (realtime.py) |
| Occupancy fit | MAE 3.95, R² 0.953 | MAE 4.2, R² 0.94 (MODEL_ASSUMPTIONS) |
| No-ledger ablation | 83.7 → 66.1 (README §5 table) | 69.6 → 61.0 (README design decisions) |
| Network | 27 real hospitals at 0.75–0.96 | MODEL_ASSUMPTIONS §2–4 still describe 12 synthetic hospitals and "0.70 VALIDATED" |
| Test count | 161 (README top), 110 (Quick start), 79 (dashboard) | **164 actually pass** |

---

## PHASE 3 — Dataset review

None of the datasets are real-time, and none need to be: the HHS facility feed
ended on 2024-05-03. Say "real-world historical data", never "real-time".

| Dataset | Purpose | Real or simulated | Risk | Reviewer concern |
|---|---|---|---|---|
| HHS *COVID-19 Reported Patient Impact and Hospital Capacity by Facility* (27 Houston hospitals × 216 weeks) | Beds, ICU, weekly occupancy | **Real**, self-reported, historical | Low | "COVID-era, ended 2024, self-reported" |
| CMS Provider of Services Q1 2021 | Capability (cardiac, neuro, trauma, cath labs) | **Real** | Low | "2021 snapshot" |
| OSRM over OpenStreetMap | Road travel times | **Real** (free-flow) | Low | "No traffic" |
| HHS national week (3,186 hospitals) | Structural sanity check | **Real** | Low | Fine as a supporting check |
| Patient arrivals, escalations, LOS (110 h), escalation delay (7 h) | Patient flow | **Simulated**, fitted to real occupancy | **Medium** | "LOS and deterioration are invented" |
| Hospital behaviour (honest / strategic / defensive) | The thing being studied | **Simulated, invented** | **High** | "You caught liars you created" |
| Phone-tree parameters (9 min/call, 25% stale) | Baseline | **Unsourced** | **Medium** | "Weak baseline by design" |
| HDU / OR / ventilator counts | Resources | **Assumed ratios** | Low–Med | Already labelled |
| Synthetic 12-hospital networks | Unit tests, counterfactuals | **Synthetic** | Low if kept out of the headline | Keep labelled |

### Proposed additions (ranked by value ÷ effort)

| Dataset | Why it fits | Integration | Publication impact | Reviewer acceptance |
|---|---|---|---|---|
| **Same HHS file, its anomalies** (already vendored) | Real evidence that capacity reports go wrong. Grounds the "lie detector" (Phase 5) | **Very easy**: data is on disk | High: turns invented lies into a real problem | High |
| **Same HHS file, a 2nd metro** (e.g. Phoenix, Boston, Atlanta) | Shows generalisation; `METROS` in `calibration/facilities.py` is already a dict | Easy: add one `MetroSpec`, re-fetch, re-route | Medium–High | High |
| **Warm start from vendored occupancy** | Fixes "hospitals start empty" | Easy | Medium | High |
| **MIMIC-IV Clinical Database Demo** (100 patients, open on PhysioNet, no credentialing) | Real ward→ICU timing and ICU LOS shape to replace assumed LOS / escalation delay | Medium | Medium | High (well-known name) |
| **MIMIC-IV full / eICU-CRD** (credentialed, CITI course) | Proper LOS and deterioration distributions; eICU covers 200+ hospitals | Medium–Hard (1–2 weeks to get access) | High | Very high |
| **CMS Care Compare — Timely & Effective Care** (ED throughput measures, joinable on CCN) | Real per-hospital ED/boarding times for the same 27 hospitals | Easy–Medium | Medium | High |
| **NHS England monthly per-trust data** (bed availability/occupancy KH03, critical-care beds, A&E 4-hour waits) | Second country, current and still published | Medium | Medium–High | High |
| **CDC NHSN Hospital Respiratory Data** (weekly, from Nov 2024) | Answers "your data ended in 2024". Jurisdiction-level only | Easy | Low–Medium | Medium |
| ~~Kaggle "hospital" CSVs~~ | Many are synthetic or undocumented | — | **Negative** | **Avoid** |

> Verify each dataset's licence and current URL before citing it. The list
> above is from my knowledge and was not re-fetched today.

---

## PHASE 4 — Phone tree analysis

**First, a misunderstanding to clear up.** The phone tree is **not a feature
of MAHROS**. It is the **baseline**: a model of what hospitals do today, which
MAHROS is compared against (`sim/strategies.py:177`). The reviewer's complaint
is really: *"Where do 9 minutes per call and a 25% stale-information rate come
from?"*

| Question | Answer |
|---|---|
| 1. Relevant to hospital resource optimisation? | **Yes.** It is the status quo, and a method must beat the status quo |
| 2. Academically defensible? | **Yes, as a rule-based serial-search baseline**, once the staleness issue below is handled |
| 3. Real data available? | **No public dataset of interhospital transfer phone calls exists** that I know of |
| 4. Healthcare call-routing dataset? | Only EMS dispatch data, e.g. NYC Open Data *EMS Incident Dispatch Data* (response and travel times). That is ambulance dispatch, not hospital-to-hospital transfer, so it is a weak fit |
| 5. Hospital triage call dataset? | None public at transfer-centre level. Published *summaries* exist: statewide transfer centres such as the Arizona Surge Line report a **median of ~59 min** to place a patient (Villarroel et al., *Front Public Health* 2023, found in an earlier search). Already cited here: Mueller et al. 2019 (transfer processes) and the UK 49-hospital study (median 22 h delay, organisational causes) |
| 6. Adaptable call-centre dataset? | The Technion "anonymous bank call-centre" data is a classic queueing dataset, but it is from banking. A reviewer will call it a stretch, so use it only as a sensitivity range for call duration, if at all |

### What I measured today (Houston surge week, 3 seeds)

| Arm | Success |
|---|---|
| MAHROS | **94.1%** |
| Phone tree, 25% stale (current headline) | 76.7% |
| Phone tree, **0% stale** | **81.8%** |
| Phone tree, 0% stale **and** 5-min calls | 85.4% |
| Nearest (shared registry) | 94.6% |

**Verdict:** MAHROS still wins by about 9–12 points with *no* staleness at all.
Of the headline 18 points, about 5 come from the unsourced 25%.

### Recommendation (no ML)
1. **Use staleness = 0 as the headline** ("even with perfect information")
   and keep 0.25 as a sensitivity row. You lose about 5 points of headline and
   gain an unbreakable argument.
2. **Frame the baseline as a queueing system**: coordinators are servers,
   calls are serial service, and the advantage is parallel vs serial search.
   Reviewers accept this framing immediately because it is textbook queueing.
3. **Anchor call duration to the literature** (published transfer-centre
   timings) and sweep 5–15 min, which the code already does.
4. **ML is not justified.** There is no labelled call data, and an ML phone
   tree would be a fake baseline built on fake data. Reviewers would
   (rightly) attack it.

| Option | Valid? | Supports optimisation? | Reviewer reaction |
|---|---|---|---|
| Rule-based serial search (current) | Yes | Yes, as a baseline | Accepted if parameters are anchored |
| Queue optimisation framing | Yes | Yes: coordinator utilisation | Positive |
| Graph-based routing (nearest-first on road graph) | Already used (OSRM order) | Yes | Neutral |
| Scheduling-based | Overkill for a baseline | — | Neutral |
| ML | **No data** | No | **Negative** |

---

## PHASE 5 — "Lie detector" analysis

What it is in code: `behaviours.py` (who lies), `challenge.py` (find
contradictions), `argumentation.py` (who wins), `attestation.py` (signed
refusals), `collusion.py` (cartels + spot checks).

| Question | Honest answer |
|---|---|
| 1. Scientifically defensible? | **Partly.** The *mechanism* is sound (falsifiable claims, grounded semantics, attestation sweep). The *evaluation* is circular: the authors decide who lies, and `defend()` assumes a liar cannot defend |
| 2. Contributes to resource optimisation? | **Yes, directly.** Under 50% dishonest hospitals, success drops 94→66% without it and stays at ~84% with it. A refused-but-available bed is a wasted resource |
| 3. Looks gimmicky? | **The *name* does.** "Lie detector" sounds like a polygraph. The mechanism is not gimmicky |
| 4. Improves research value? | **Yes. It is the main novelty.** Without it, MAHROS ties a plain shared registry |
| 5. Weakens the project? | Only through (a) the name, (b) the circular evaluation, (c) "zero false accusations" presented as a result |

**Do not remove it. Removing it removes the contribution.** Reframe and
ground it instead.

### Three changes, in order

**1. Rename it: "Capacity-Claim Verification" (or "Refusal Accountability").**
Ground it in an existing norm, not in suspicion. In the US, **EMTALA
(42 CFR 489.24(f))** says a hospital with specialised capabilities "may not
refuse to accept ... an appropriate transfer ... if the receiving hospital has
the capacity to treat the individual."

> **Correction (checked against the regulation text on 2026-10-13).** An
> earlier version of this report said MAHROS "makes a legal obligation
> checkable". That overstated it. Paragraph (f)(2) says the obligation does
> **not** apply to an individual already admitted to the referring hospital,
> and MAHROS's patients are exactly that: admitted, then deteriorating. So
> cite EMTALA as **precedent**: regulators already treat "refusing despite
> capacity" as unacceptable for emergency patients. MAHROS applies the same
> principle where no rule currently reaches. Do not say the law applies.

**2. Add real-data evidence that capacity reports go wrong (false-reporting
detection).** I scanned the vendored HHS data for the 27 Houston hospitals
**[measured today]**:
- **298 of 5,611** weekly reports repeat the *exact same* inpatient count for
  4+ weeks. 235 of these are frozen at **0**, across 7 hospitals (United
  Memorial alone: 90).
- **6** reports have ICU patients greater than ICU beds.

Build a small **Data Integrity Agent** with rule-based checks (frozen value,
impossible ratio, sudden jump), each with a plain reason. It flags which
hospitals' self-reports are unreliable *in the real data*. This answers
"there's no evidence hospitals misreport" with evidence from the very
hospitals you simulate. Be careful with wording: it shows that **reports are
unreliable**, not that hospitals **lie**.

**3. Make the attested protocol the headline, not `defend()=False`.** The
attestation sweep already replaces the assumption with a swept parameter
(deterrence 0 → 1). Report the *range* (66.6% → 83.9%) as the result and say
the old number was the optimistic end. Restate "zero false accusations" as a
**safety property that is tested, and that once failed and was fixed**, not as
a measured achievement.

| Alternative | Fits title | Ease | Publication value |
|---|---|---|---|
| **Capacity-claim verification (reframed current)** | ★★★★★ | ★★★★★ (rename + docs) | ★★★★ |
| **False-reporting detection on real HHS data** | ★★★★★ | ★★★★ (~150 lines, data on disk) | ★★★★★ (real data) |
| Resource misuse detection | ★★★ | ★★ (no data) | ★★ |
| Suspicious resource requests (origin over-requesting) | ★★★ | ★★★ | ★★★ (symmetric attack, nice extension) |
| Fraud detection | ★ (billing, off-topic) | ★ | ★ |
| Inconsistent patient reporting | ★★ (no patient data) | ★ | ★ |

**Best fit: verification. Easiest: rename. Highest publication value: real-data
false-reporting detection feeding the verifier.** Do all three; they are one
story.

---

## PHASE 6 — Database review

**Current state:** no database. The report itself says *"No database server.
Simulation state is held in memory."* The ledger lives in a Python list and
dies with the process.

### Why reviewers want PostgreSQL
1. **Verifiability.** They can query what happened instead of trusting a screen.
2. **Integrity by construction.** Foreign keys stop orphan records; `CHECK`
   constraints stop impossible states (occupied > capacity).
3. **Audit primitives.** Triggers record every change automatically, and
   permissions can make the audit table append-only.
4. **It is what real hospital systems use**, so the system looks deployable,
   not a toy.
5. **Academic value.** A normalised schema *is* a formal data model of the
   domain, which is a citable design artefact.

### Relationships missing today
Patient → admission → bed; request → every bid → challenge → award; award →
ledger entry; user action → state change; run → parameters → results;
predicted occupancy → reported occupancy. Today these live in separate Python
objects or not at all.

### Scope decision (important for a student project)
**Do not push every simulated event of 40-seed experiments into Postgres.**
That is millions of rows for no reviewer benefit, and it risks breaking the
byte-for-byte reproducibility you already have. Instead:
- **Experiments** stay in memory (fast, reproducible). Each run writes **one
  summary row** plus its parameters.
- **The live console** (what reviewers touch) reads and writes **everything**
  through Postgres.
- **Real data** (HHS weeks, CMS capability, routes) is loaded into Postgres
  tables so reviewers can query the source.

### Schema

```sql
-- ===== Reference data (REAL) =====
CREATE TABLE hospitals (
  hospital_id   VARCHAR(10) PRIMARY KEY,           -- CMS CCN, e.g. '450068'
  name          TEXT NOT NULL,
  lat           NUMERIC(9,6) NOT NULL,
  lon           NUMERIC(9,6) NOT NULL,
  tier          SMALLINT CHECK (tier BETWEEN 1 AND 3),
  provenance    TEXT NOT NULL DEFAULT 'observed'   -- observed / derived / assumed
);

CREATE TABLE hhs_weekly_reports (                   -- the real HHS series
  hospital_id   VARCHAR(10) REFERENCES hospitals,
  week_start    DATE,
  inpatient_beds NUMERIC, inpatient_used NUMERIC,
  icu_beds      NUMERIC, icu_used NUMERIC,
  PRIMARY KEY (hospital_id, week_start)
);

CREATE TABLE hospital_capabilities (                -- CMS Provider of Services
  hospital_id   VARCHAR(10) REFERENCES hospitals,
  specialty     TEXT,
  source        TEXT NOT NULL DEFAULT 'CMS POS 2021Q1',
  PRIMARY KEY (hospital_id, specialty)
);

CREATE TABLE travel_times (                          -- OSRM
  from_id VARCHAR(10) REFERENCES hospitals,
  to_id   VARCHAR(10) REFERENCES hospitals,
  minutes NUMERIC NOT NULL,
  PRIMARY KEY (from_id, to_id)
);

-- ===== Hospital structure =====
CREATE TABLE departments (                           -- one per resource pool
  department_id SERIAL PRIMARY KEY,
  hospital_id   VARCHAR(10) NOT NULL REFERENCES hospitals,
  kind          TEXT NOT NULL CHECK (kind IN ('icu','hdu','ward','or','cath_lab','ventilator')),
  UNIQUE (hospital_id, kind)
);

CREATE TABLE resources (
  resource_id   SERIAL PRIMARY KEY,
  department_id INT NOT NULL REFERENCES departments,
  capacity      INT NOT NULL CHECK (capacity >= 0),
  occupied      INT NOT NULL DEFAULT 0,
  provenance    TEXT NOT NULL,                     -- 'observed' (ICU) / 'assumed' (HDU)
  CHECK (occupied BETWEEN 0 AND capacity)          -- impossible states rejected
);

CREATE TABLE beds (
  bed_id        SERIAL PRIMARY KEY,
  department_id INT NOT NULL REFERENCES departments,
  label         TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('free','occupied','reserved','blocked'))
);

-- ===== Patients (SIMULATED, labelled) =====
CREATE TABLE patients (
  patient_id    UUID PRIMARY KEY,
  pseudonym     TEXT NOT NULL,                     -- no real identities, ever
  age_band      TEXT,
  data_origin   TEXT NOT NULL DEFAULT 'simulated' CHECK (data_origin = 'simulated')
);

CREATE TABLE admissions (
  admission_id  UUID PRIMARY KEY,
  patient_id    UUID NOT NULL REFERENCES patients,
  hospital_id   VARCHAR(10) NOT NULL REFERENCES hospitals,
  department_id INT REFERENCES departments,
  bed_id        INT REFERENCES beds,
  acuity        SMALLINT CHECK (acuity BETWEEN 1 AND 5),
  admitted_at   TIMESTAMPTZ NOT NULL,
  discharged_at TIMESTAMPTZ
);

-- ===== Runs and the negotiation =====
CREATE TABLE simulation_runs (
  run_id        UUID PRIMARY KEY,
  scenario      TEXT NOT NULL, strategy TEXT NOT NULL, seed INT NOT NULL,
  code_version  TEXT NOT NULL,                     -- git SHA: makes runs reproducible
  params        JSONB NOT NULL,
  results       JSONB,                             -- summary metrics
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE transfer_requests (
  request_id    UUID PRIMARY KEY,
  run_id        UUID REFERENCES simulation_runs,   -- NULL = live console
  admission_id  UUID NOT NULL REFERENCES admissions,
  origin_id     VARCHAR(10) NOT NULL REFERENCES hospitals,
  resource_kind TEXT NOT NULL, specialty TEXT NOT NULL,
  acuity        SMALLINT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE agent_decisions (                      -- every bid, refusal, challenge, defence
  decision_id   BIGSERIAL PRIMARY KEY,
  request_id    UUID NOT NULL REFERENCES transfer_requests,
  agent_id      TEXT NOT NULL,                     -- e.g. '450068/icu'
  hospital_id   VARCHAR(10) REFERENCES hospitals,
  action        TEXT NOT NULL CHECK (action IN
                  ('cfp','propose','refuse','challenge','defend','attest','overrule','award')),
  reason        TEXT,
  score         NUMERIC,
  payload       JSONB,
  decided_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE challenges (
  challenge_id  BIGSERIAL PRIMARY KEY,
  refusal_id    BIGINT NOT NULL REFERENCES agent_decisions,
  evidence      JSONB NOT NULL,                     -- ledger entry ids, directory facts
  outcome       TEXT CHECK (outcome IN ('upheld','struck_out','referred')),
  resolved_at   TIMESTAMPTZ
);

CREATE TABLE allocations (
  allocation_id UUID PRIMARY KEY,
  request_id    UUID UNIQUE NOT NULL REFERENCES transfer_requests,
  receiver_id   VARCHAR(10) NOT NULL REFERENCES hospitals,
  bed_id        INT REFERENCES beds,
  decided_by    TEXT NOT NULL,                     -- cnp / phone_tree / central ...
  agreed_at     TIMESTAMPTZ NOT NULL,
  care_start    TIMESTAMPTZ,
  rationale     TEXT,
  ledger_entry  TEXT                               -- hash of the ledger entry
);

CREATE TABLE phone_tree_records (                   -- baseline calls, one per call
  call_id       BIGSERIAL PRIMARY KEY,
  request_id    UUID NOT NULL REFERENCES transfer_requests,
  call_seq      SMALLINT NOT NULL,
  callee_id     VARCHAR(10) NOT NULL REFERENCES hospitals,
  started_at    TIMESTAMPTZ NOT NULL,
  duration_min  NUMERIC NOT NULL,
  answer        TEXT CHECK (answer IN ('yes','no','stale_discarded'))
);

CREATE TABLE prediction_records (                   -- simulated vs reported occupancy (no ML needed)
  prediction_id BIGSERIAL PRIMARY KEY,
  run_id        UUID REFERENCES simulation_runs,
  hospital_id   VARCHAR(10) NOT NULL REFERENCES hospitals,
  week_start    DATE NOT NULL,
  target        TEXT NOT NULL,                     -- 'inpatient_occupancy', 'icu_occupancy'
  predicted     NUMERIC NOT NULL,
  observed      NUMERIC,                            -- from hhs_weekly_reports
  method        TEXT NOT NULL                       -- 'erlang_b_fit', 'moving_avg' ...
);

-- ===== Ledger (moved out of memory) =====
CREATE TABLE ledger_blocks (
  block_index   INT PRIMARY KEY,
  prev_hash     CHAR(64) NOT NULL,
  merkle_root   CHAR(64) NOT NULL,
  block_hash    CHAR(64) NOT NULL UNIQUE,
  sealed_at     TIMESTAMPTZ NOT NULL
);
CREATE TABLE ledger_entries (
  entry_id      BIGSERIAL PRIMARY KEY,
  block_index   INT REFERENCES ledger_blocks,       -- NULL while pending
  kind          TEXT NOT NULL CHECK (kind IN ('agreement','refusal')),
  payload       JSONB NOT NULL,
  entry_hash    CHAR(64) NOT NULL
);

-- ===== Audit + events (see Phase 7) =====
CREATE TABLE actors (
  actor_id      TEXT PRIMARY KEY,                   -- 'user:reviewer', 'agent:450068/icu', 'system'
  kind          TEXT NOT NULL CHECK (kind IN ('user','agent','system'))
);
CREATE TABLE audit_log (
  audit_id      BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  actor_id      TEXT NOT NULL REFERENCES actors,
  action        TEXT NOT NULL,                      -- INSERT / UPDATE / DELETE / API verb
  table_name    TEXT, row_pk TEXT,
  before        JSONB, after JSONB,
  prev_hash     CHAR(64) NOT NULL,
  row_hash      CHAR(64) NOT NULL
);
CREATE TABLE system_events (
  event_id      BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  level         TEXT NOT NULL, source TEXT NOT NULL,
  event_type    TEXT NOT NULL,                      -- 'startup','verify_ok','tamper_detected','checkpoint'
  details       JSONB
);
```

**Relationship spine (say it out loud in the review):** hospital → departments
→ resources/beds; patient → admission → transfer_request → agent_decisions
(→ challenges) → allocation → ledger_entry; every write → audit_log.

### How to show it to reviewers
1. `docker compose up` starts Postgres, pgAdmin and the app.
2. Run a transfer in the console. In pgAdmin:
   `SELECT * FROM agent_decisions WHERE request_id = '…' ORDER BY decided_at;`
   shows every hospital's answer, the challenge and the award.
3. Change a patient's acuity in the UI. Then
   `SELECT before, after, actor_id FROM audit_log ORDER BY audit_id DESC LIMIT 1;`
   shows the old value, the new value, and who changed it.
4. Try `UPDATE resources SET occupied = capacity + 5` and watch the `CHECK`
   constraint reject it.
5. `SELECT * FROM prediction_records` puts simulated occupancy next to the real
   HHS-reported occupancy.

---

## PHASE 7 — Audit system

| Question | Answer today |
|---|---|
| 1. Merely logging? | **More than logging** for transfers: the ledger is evidence the challenge reasons over |
| 2. Truly auditing? | **No.** It covers agreements and refusals only. No user actions, no before/after, no actor identity, no persistence |
| 3. Agent decisions traceable? | Partly: refusals and awards yes, individual bids and scores no |
| 4. Tampering detectable? | **Yes, in memory**: Merkle + hash chain, `/api/integrity`. But the only tamper demo is the app tampering with itself (`/api/tamper`) |
| 5. Reviewer can verify from backend? | **No.** There is nothing to query, and it is gone after restart |

### Improved design (two layers, both in Postgres)
1. **Transfer ledger** (existing logic, persisted): blocks + entries tables. It
   stays the evidence base for challenges.
2. **Row-level audit log** for *every* table: a trigger captures before/after
   JSON, the actor, the time and a hash chain.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION audit_row() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  prev   CHAR(64);
  v_old  JSONB := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN to_jsonb(OLD) END;
  v_new  JSONB := CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN to_jsonb(NEW) END;
  v_who  TEXT  := coalesce(current_setting('mahros.actor', true), 'system');
  v_ts   TIMESTAMPTZ := clock_timestamp();
  v_pk   TEXT  := coalesce(v_new, v_old) ->> TG_ARGV[0];
BEGIN
  PERFORM pg_advisory_xact_lock(4242);                 -- one writer at a time: keeps the chain linear
  SELECT row_hash INTO prev FROM audit_log ORDER BY audit_id DESC LIMIT 1;
  prev := coalesce(prev, repeat('0', 64));
  INSERT INTO audit_log(ts, actor_id, action, table_name, row_pk, before, after, prev_hash, row_hash)
  VALUES (v_ts, v_who, TG_OP, TG_TABLE_NAME, v_pk, v_old, v_new, prev,
          encode(digest(prev || v_ts::text || v_who || TG_OP || TG_TABLE_NAME || coalesce(v_pk,'')
                        || coalesce(v_old::text,'') || coalesce(v_new::text,''), 'sha256'), 'hex'));
  RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER audit_admissions  AFTER INSERT OR UPDATE OR DELETE ON admissions
  FOR EACH ROW EXECUTE FUNCTION audit_row('admission_id');
-- … same for patients, resources, beds, allocations, agent_decisions, challenges

-- The app's database role may only append:
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM mahros_app;
```

The app sets `SET LOCAL mahros.actor = 'user:reviewer'` (or
`'agent:450068/icu'`) in each transaction, so every row knows who changed it.
API calls such as `set_policy` and `set_occupancy` write an `audit_log` row as
well. **This closes today's biggest audit hole.**

**Verification** (`audit_verify()` or Python): walk the rows in order,
recompute each hash and report the first row whose stored hash or `prev_hash`
does not match. `jsonb::text` output is canonical, so recomputation is stable.

**Honest limit (say it before they do):** a database superuser could rewrite
the *whole* chain consistently. The mitigation is a **checkpoint**: every N
minutes, write the latest hash to `system_events` *and* to a file outside the
database, and show it on screen. A rewritten chain no longer matches the
checkpoint. That is the same idea as "anchoring" in blockchains, without the
blockchain.

### API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/audit?table=&actor=&since=` | Browse the audit log |
| GET | `/api/audit/verify` | Recompute the chain; returns `ok` or the first broken `audit_id` |
| GET | `/api/audit/history/{table}/{pk}` | Full before/after history of one record |
| GET | `/api/allocations/{id}/trace` | Request → every bid → challenges → award → ledger entry → audit rows |
| POST | `/api/audit/checkpoint` | Anchor the current head hash |
| GET | `/api/ledger/verify` | Existing Merkle chain check, now read from Postgres |
| PATCH | `/api/patients/{id}` | Demo edit (acuity), recorded with the actor |

### Reviewer demo (proves the audit, about 4 minutes)
1. In the UI, change patient P-17's acuity from 3 to 5.
2. In pgAdmin: `SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT 3;` shows
   before = 3, after = 5, actor = `user:reviewer`, timestamp and hash.
3. Run a transfer, then open `/api/allocations/{id}/trace`: one click from
   decision back to every agent's answer.
4. **Tamper by hand in pgAdmin as superuser:**
   `UPDATE audit_log SET after = jsonb_set(after,'{acuity}','3') WHERE audit_id = …;`
5. Open `/api/audit/verify`. It answers *"broken at audit_id N, table
   admissions, row P-17"*, and a `tamper_detected` row appears in
   `system_events`.
6. Show that the app role cannot do step 4 at all: `permission denied`.

The difference from today: **the reviewer tampers, not the app.**

---

## PHASE 8 — Multi-agent analysis

| Agent | Purpose | Usefulness | Redundancy risk | Academic value | Optimisation contribution |
|---|---|---|---|---|---|
| Resource agents (×6 per hospital) | Feasibility + convex cost | High | **Medium–High**: "methods called agents" | Medium | Opportunity-cost pricing drives good awards |
| Hospital agent + policy | Report / defend | **Core** | Low | High | Who accepts what |
| Origin manager (CNP) | Announce, award | **Core** | Low | Medium (standard CNP) | Parallel search: the main speed gain |
| Challenge engine | Verify refusals | **Core** | Low, but **not an agent today** | **High** | Recovers wasted beds |
| Fairness monitor | Burden balance | Medium | Low | Medium | Gini −0.03 at a ~0.6-pt cost |
| LLM coordinator | Explain | Low | **High**: templates do the same job | Low | None |

**Defending "are these really agents?"** Each hospital has private state that
no one else reads, decides independently, communicates only through FIPA
performatives on a message bus, and the bus *tests* that no node sees
everyone's state (`MessageBus.audit_no_global_view`). That is Wooldridge's
definition. Say it in those words.

### Changes that keep the architecture multi-agent

| Change | Type | Why |
|---|---|---|
| **Verifier Agent**: promote `ChallengeEngine` to an agent with its own message type (`CHALLENGE`, `REQUEST_ATTESTATION`) | Restructure | Makes the core contribution an agent, not a helper function |
| **Data Integrity Agent** (new): audits each hospital's real HHS reports (frozen counts, impossible ratios, jumps) and publishes a reliability score | New, rule-based | Real-data grounding (Phase 5) |
| **Reputation inside the Verifier**: hospitals with overruled refusals or unreliable reports get **higher spot-check rates** (risk-based instead of uniform 5–30%) | Better coordination | Fixes the "no reputation" limitation. A new experiment: risk-based vs uniform spot checks against cartels |
| **Regional Monitor Agent** (new): watches outcomes and names bottlenecks ("70% of failures this week: cardiac ICU, north cluster") | New, rule-based | Puts "resource optimisation" visibly into the system |
| Collapse the 6 resource agents into one hospital agent with 6 pools | Optional simplification | Removes the "fake agents" attack. **Only if** a reviewer raises it; it changes many tests |
| LLM → optional plug-in in the paper | Demote | Removes the buzzword attack |

---

## PHASE 9 — High-impact features

Scores: Effort 1 = trivial, 10 = huge; the other three 10 = best.

| # | Feature | Effort | Research | Reviewer appeal | Publication | Notes |
|---|---|---|---|---|---|---|
| 1 | **PostgreSQL + trigger audit + pgAdmin tamper demo** | 6 | 4 | **10** | 4 | Demanded explicitly. Biggest review win |
| 2 | **Fix all doc contradictions, one source of truth** | 2 | 3 | **9** | 8 | Script-checked numbers from `results/*.json` |
| 3 | **Data Integrity Agent on real HHS reports** | 3 | **8** | 9 | **8** | Data already vendored |
| 4 | **Rename/reframe verification + EMTALA as precedent** | 1 | 6 | 9 | 7 | Words only |
| 5 | **Phone tree headline at staleness 0** | 1 | 5 | 8 | 7 | Measured today: still wins |
| 6 | **Second metro** | 4 | 7 | 8 | **8** | Generalisation |
| 7 | **Warm start at observed occupancy** | 3 | 5 | 6 | 6 | Removes an admitted bias |
| 8 | **Risk-based spot checks (reputation)** | 4 | **8** | 7 | **8** | New, clean experiment |
| 9 | **Bottleneck detection (Regional Monitor)** | 3 | 5 | 8 | 5 | Visible "optimisation" |
| 10 | **Allocation trace / explainability endpoint** | 2 | 4 | 9 | 4 | Falls out of the schema |
| 11 | Excess-mortality illustration (3%/h, clearly labelled) | 2 | 5 | 7 | 5 | Label as illustration, not outcome |
| 12 | Formal problem formulation (objective + constraints) | 2 | 6 | 8 | **8** | Answers "where's the optimisation?" |
| 13 | Bed occupancy forecasting (ML) | 6 | 3 | 5 | 3 | **Skip**: off the core story; a moving average is enough if needed |
| 14 | Predictive allocation (ML) | 8 | 4 | 5 | 3 | **Skip**: no data to learn from |

---

## PHASE 10 — Final review strategy

### A. Critical fixes (do these or expect trouble)
1. **One source of truth for numbers.** Regenerate every number in README,
   PANEL_GUIDE, MODEL_ASSUMPTIONS and the report from `results/*.json`. Delete
   the stale synthetic sections of MODEL_ASSUMPTIONS §2–5 or mark them
   "synthetic arm only". Fix test counts (164). Fix the title ("Optimization").
2. **PostgreSQL + persisted ledger + trigger audit + pgAdmin**, scoped to the
   live console and run summaries (Phases 6–7).
3. **Audit user actions.** `set_policy`, `set_occupancy` and the toggles must
   write audit rows.
4. **Reframe the "lie detector"** as capacity-claim verification, grounded in
   EMTALA as precedent (not as applicable law; see the correction in
   Phase 5). Lead with the attested sweep, not `defend()=False`.
5. **Phone tree headline at staleness 0.**

### B. High priority
1. Data Integrity Agent on the real HHS series.
2. Promote the challenge engine to a Verifier Agent.
3. Warm start.
4. Formal optimisation formulation in the paper.
5. Move LLM, Presidio, Solidity and leakage to "optional / future work" in the
   paper and the demo.

### C. Nice to have
Second metro · risk-based spot checks · Regional Monitor bottlenecks ·
MIMIC-IV demo LOS calibration · mortality illustration.

### 1. Final review demo plan (about 10 minutes)
| Min | Show | Proves |
|---|---|---|
| 0–1 | Map of 27 real Houston hospitals; HHS rows in pgAdmin | Real data, queryable |
| 1–3 | Cardiac acuity-5 transfer: parallel CFP, answers, award | Multi-agent negotiation |
| 3–5 | Switch a hospital to strategic; the refusal is challenged with evidence and struck out | Verification |
| 5–6 | Turn verification off; the same refusal succeeds | Ablation, live |
| 6–8 | pgAdmin: decisions, allocation trace, audit row of the policy switch | Backend verifiability |
| 8–9 | Reviewer edits `audit_log` by hand; `/api/audit/verify` names the row | Tamper detection |
| 9–10 | Results slide: phone tree at stale = 0, adversarial table, honest limits | Evidence |

Have a recorded video of the demo as a fallback.

### 2. Reviewer defence strategy
- **Say the weaknesses first.** "Behaviour is simulated; the network is real.
  Here is the line between them." The `DATA_PROVENANCE` labels are your
  strongest asset; point at them.
- **Answer with the conservative number.** Stale = 0, the attested range, a
  1-point cost vs omniscient.
- **Never say "real-time", "blockchain", "lie detector", or "saves lives".**
  Say "historical real data", "hash-chained audit ledger", "capacity-claim
  verification", "reduces time to care".

### 3–4. Likely questions and strong answers

| Question | Answer |
|---|---|
| "Your data is simulated." | "The hospitals, beds, capability, roads and weekly load are real federal data for 27 named hospitals. The simulation reproduces their reported occupancy within 3.95 points. What is simulated is behaviour, because no dataset records transfer refusals. That is labelled in every table." |
| "You caught liars you invented." | "Yes, so we don't rely on it. The attested version sweeps how often liars will sign, from 0 to 1, and we report the whole range. Separately, the real HHS data shows these same hospitals' reports were frozen or impossible in 298 weeks, so unreliable self-reports are an observed problem. For emergency patients, refusing despite capacity is already prohibited under EMTALA; we apply that principle to admitted patients, where no rule reaches yet." |
| "Why is zero false accusations meaningful?" | "It is a safety property we test, not a lucky outcome. It once failed: spot checks punished cautious hospitals, and we fixed the predicate. The test now guards it." |
| "The phone tree is a strawman." | "With perfect information and no staleness, MAHROS still wins 94% vs 82%. The gain is parallel vs serial search, which is queueing, not a parameter we chose." |
| "Why not a central database / optimiser?" | "We compared: omniscient greedy is 1 point better and statistically equivalent within ±2. Decentralisation costs about 1 point and keeps occupancy private." |
| "Then why not a simple registry?" | "When everyone is honest, a registry ties us. We say so. Under 50% dishonest hospitals, a registry has no defence; we keep ~84% vs 66%." |
| "Where is the optimisation?" | "Objective: maximise transfers inside the clinical window and minimise time to care, under capacity and capability constraints. Each award is a scored choice, and we benchmark against the Hungarian-optimal assignment." |
| "Show me it from the backend." | Open pgAdmin. Run the trace query. Let them tamper. |
| "Why an LLM?" | "It only writes explanations, it is off the decision path, and it is optional. Every number is reproducible without it." |
| "Does it generalise?" | (After fix 6) "A second city gives the same ranking." (Before) "One city, by design. The pipeline takes any metro in the HHS file." |

### 5. Publication-oriented enhancements
1. **One paper, one claim:** *"Verifiable refusals: accountable decentralised
   interhospital transfer without pooling capacity data."* Contribution 1:
   verification mechanism. 2: attested + spot-check robustness incl. collusion.
   3: real-network evaluation. Cut everything else to one sentence.
2. Add the formal model (problem, adversary, safety property).
3. Add a real-data motivation section (HHS anomalies + EMTALA as precedent).
4. Two cities. Warm start.
5. A systematic literature review (PRISMA-style) of MAS bed allocation,
   interhospital transfer and argumentation-based negotiation.
6. Venues: AAMAS workshops / EMAS / PRIMA (MAS side); *Health Care
   Management Science*, *Journal of Biomedical Informatics*, *IEEE Access*,
   *Artificial Intelligence in Medicine* (applied side). Check scope first.

### Probability estimates

| | Estimate | Reasoning |
|---|---|---|
| **Final review acceptance, today** | **~55%** | The technical depth is well above a typical 3-credit project and will impress a careful reader. But the three things the reviewer already flagged are unchanged (simulated behaviour, no PostgreSQL, the "lie detector"), and the docs contradict themselves. A reviewer who sees their feedback ignored tends to reject regardless of depth. |
| **Final review acceptance, after Critical + High fixes** | **~85%** | Every past criticism gets a concrete, backend-verifiable answer, and the remaining risk is mostly presentation. Not higher, because behaviour stays simulated and some reviewers will never accept that. |
| **Publication, today** | **~20%** (mid-tier venue) | The novel idea is real, but it is buried under too many sub-contributions, the evaluation is circular on its key claim, and the docs are inconsistent. |
| **Publication, after fixes** | **~50%** at a mid-tier journal or MAS workshop; **<10%** at a top venue (AAMAS main track, JAMIA) | A focused claim plus real-data motivation plus a second city is a credible applied paper. Top venues will want real behavioural (refusal) data, which does not exist publicly; partnering with a transfer centre is the only way past that ceiling. |

> These are judgement calls, not measurements. They assume the reviewer reads
> the material. A reviewer who only watches the demo is swayed mostly by
> fixes 2, 3 and the pgAdmin tamper moment.
