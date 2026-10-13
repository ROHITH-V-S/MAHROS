# MAHROS

**Multi-Agent Hospital Resource Optimization System.** When an admitted patient
deteriorates and needs care their hospital cannot give right now (an ICU bed,
a cath lab, a neurosurgeon), MAHROS finds a receiving hospital: every capable
hospital in reach is asked at once, each answers for itself, and a refusal
can be **checked** against public records and the hospital's own past
commitments, without anyone revealing a bed count.

> **Scope.** Escalation transfers of admitted patients between hospitals.
> Not ambulance routing, not emergency-department triage. Around 1.5 million
> US admissions a year end in an interhospital transfer.

New here? Read **[docs/PANEL_GUIDE.md](docs/PANEL_GUIDE.md)** (plain English,
the likely questions, the answers) and **[docs/DEMO.md](docs/DEMO.md)** (the
10-minute demo).

---

## What is real, and what is simulated

| | Source | Status |
|---|---|---|
| 27 Houston hospitals, 30 Phoenix hospitals: identity, location, beds, staffed ICU beds, weekly occupancy 2020–2024 | US HHS / CDC NHSN facility reports | **Real** |
| Who can treat what (cardiac, neuro, trauma, obstetric, burns, paediatric), cath-lab rooms | CMS Provider of Services | **Real** |
| Road travel times between every pair | OSRM over OpenStreetMap | **Real** (free-flow) |
| Which patients need transfer, and for what (the case-mix) | 37,439 interhospital transfers, California 2021 (HCAI) | **Real**, with a declared mapping |
| Occupancy each run starts from | the hospitals' reported week | **Real** |
| Step-down beds, OR slots, ventilators per hospital | declared ratios | Assumed (no dataset reports them) |
| Patient arrivals, deterioration, length of stay | fitted to the real occupancy | Simulated, validated |
| **How hospitals answer a transfer request, and whether honestly** | none exists publicly | **Simulated**: the thing under study |

Every field is labelled observed / derived / assumed in
[docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md) (generated from the code),
and every modelling choice is in [docs/MODEL_ASSUMPTIONS.md](docs/MODEL_ASSUMPTIONS.md).

---

## Run it

### With the database (recommended; what reviewers should see)

```bash
docker compose up -d --build
```

| Open | What |
|---|---|
| http://localhost:8090 | MAHROS: **Live negotiation**, **Hospital data**, **Results** |
| http://localhost:5051 | pgAdmin: every table (server "MAHROS database", opens without a password) |

The first start creates the schema and loads the real data (about 2 seconds).
Ports are deliberately unusual so the stack does not collide with other
projects; change them in `.env` (copy `.env.example`). `docker compose down`
stops it; `docker compose down -v` also deletes the database.

### Without Docker

```bash
pip install -e ".[demo,db]"
python -m mahros.server            # http://127.0.0.1:8000, in memory: nothing saved
```

The top bar always says whether changes are **saved to PostgreSQL** or not.

---

## The Hospital data tab

All 27 real hospitals, as the live system sees them.

- **Real data is locked.** Bed capacity and specialties show a lock. The
  database refuses changes to them, even from the database owner in pgAdmin;
  only the data loader can rewrite them.
- **Editable:** occupancy per pool, and each hospital's behaviour (honest,
  protective, cautious). **Save** writes to PostgreSQL; the next transfer
  uses it. Edited values get a yellow **edited** badge next to the real value.
- **Restore** one hospital, or **Restore all to real data** (two clicks).
  Occupancy returns to what each hospital reported; behaviour back to honest;
  patients moved in this session are discharged. Nothing is deleted: a new
  session starts and the history stays.
- **History** per hospital: every change from the audit log (before → after,
  who, when), and the hospital's own flagged HHS reports.
- Also on the tab: transfers saved in the database with a full **trace**,
  **where the network is running out** (Regional Monitor), the **Data
  Integrity Agent**'s findings, the **audit log**, and **Verify now**.

Every transfer run in the console is written to the database in one
transaction: the patient (pseudonymised), the request, every agent's decision,
any challenge, the allocation, the admission, the bed taken, and the ledger
block. If that write fails, the engine reloads from the database, so the
screen never shows a transfer the database does not have.

---

## The optimisation problem

**Given** hospitals $H$ with resource pools $r$ of capacity $c_{h,r}$ (public,
real) and occupancy $x_{h,r}(t)$ (**private**), public capabilities $S_h$, road
times $\tau_{o,h}$, and transfer requests $q$ (origin, resource, specialty,
acuity $a_q$, clinical window $W(a_q)$).

**Decide** a receiving hospital $h_q$ for each request, after negotiation
delay $\Delta_q$.

**Objective**, in order: (1) maximise patients who reach care inside their
window, $\sum_q \mathbf{1}[\Delta_q + \tau_{o_q,h_q} + p_{h_q} \le W(a_q)]$;
(2) minimise time to care; (3) balance the burden across hospitals.

**Subject to** capacity ($x_{h,r}+1 \le c_{h,r}$), capability ($s_q \in S_h$),
**privacy** (no $x_{h,r}$ is ever sent), and **self-interest** (each hospital
*reports* an answer that may differ from the truth).

| Method | Information it uses | Decision rule |
|---|---|---|
| Phone tree (today) | one hospital at a time | first "yes", nearest first |
| Shared registry | everyone's declared availability, believed | nearest hospital that says it has room |
| Central greedy | everyone's **true** occupancy | best score, one request at a time |
| Batched optimal | everyone's **true** occupancy | minimum-cost assignment of a batch (Hungarian): the efficiency ceiling |
| **MAHROS** | only what hospitals say, plus a record they cannot edit | ask the 8 nearest capable hospitals at once, verify refusals, award by score |

The two omniscient methods break the privacy constraint; they are upper
bounds. The evaluation asks how close MAHROS gets **without** pooling private
occupancy, and what happens when hospitals do not tell the truth.

---

## The agents

| Agent | One per | Decides |
|---|---|---|
| **Hospital agent** (with resource agents per pool) | hospital | whether it can take a patient, what that costs it, and what to *say*; defends or signs its refusals |
| **Transfer manager** | request | asks the nearest capable hospitals at once (Contract Net), scores the bids, awards |
| **Verifier** | network | turns refusals into checkable claims: capability against the CMS register, capacity against the hospital's own recent acceptances on the ledger; settles disputes with Dung's grounded semantics; runs spot checks |
| **Data Integrity Agent** | network | checks every real weekly HHS report with plain rules (impossible, frozen, collapsed) |
| **Regional Monitor** | network | reports which needs go unmet and which pools are fullest |

Each hospital agent holds private state nobody else reads, decides
independently, and talks only through FIPA-style messages (call for
proposals, propose, refuse, accept). A test asserts that no node ever
received every hospital's private state. Every agent decision is a row in
`agent_decisions` with the agent that made it.

---

## Capacity-claim verification (what used to be called the lie detector)

Accepting a transfer costs a hospital a scarce bed. The cheapest way out is
to say "we're full". A plain auction cannot check that. MAHROS turns each
refusal into a claim that can be tested:

- **"We don't treat that"** is checked against the public CMS register.
- **"We're full"** is checked against what the same hospital agreed to take in
  the last hour, on a ledger it cannot edit. Nobody reveals a bed count.
- A contradicted refusal is challenged. In the **signed** version the hospital
  must sign its claim with a key it does not solely control; whether a liar
  will sign is a swept parameter, not an assumption.
- **Spot checks** ask a sample of refusals to certify themselves with no
  evidence against them, because a coordinated cartel never triggers a
  reactive check. **Risk-based** spot checks check hospitals already caught
  more often.

**Grounding.** No public dataset records transfer refusals, so the behaviour is
simulated. Two things are real: hospitals' own capacity reports are often
unreliable (the Data Integrity Agent flags real HHS reports as impossible or
copied, at very different rates in the two cities), and US law already
treats "refusing despite capacity" as unacceptable for emergency patients
(EMTALA, 42 CFR 489.24(f)). EMTALA does not cover patients already admitted
(489.24(f)(2)), which is MAHROS's scope: it is precedent, not applicable law.

**"Zero false accusations"** is a tested safety property, not a lucky
measurement: an honest refusal is backed by the hospital's real state, so it
can always be defended or signed. It has failed once (spot checks on
capability refusals struck out honest hospitals) and was fixed; tests guard it.

---

## Results

Every number below is generated from `results/*.json` by
`python experiments/build_results.py`, which rewrites this block and
[docs/RESULTS.md](docs/RESULTS.md). Do not edit it by hand.

<!-- results:start -->
*(not built yet: run the experiments, then `python experiments/build_results.py`)*
<!-- results:end -->

### Say these before a reviewer does

- **Behaviour is simulated.** The network is real; how hospitals answer is
  modelled, with every behavioural parameter swept.
- **A shared registry matches MAHROS when everyone is honest.** MAHROS's case
  is what happens when they are not.
- **The omniscient optimiser is slightly better.** Decentralisation has a
  measurable cost; RESULTS.md gives its size.
- **Collusion defeats reactive checking.** Only proactive spot checks recover
  it, and they cost checking effort.
- **Report reliability varies by region.** Houston's reports are flagged far
  more often than Phoenix's; neither is a national estimate.
- **Validation is of occupancy, not of behaviour.** The model reproduces the
  occupancy hospitals reported, from a cold start. Nothing validates how they
  negotiate.

---

## The audit, and how to check it from the backend

Two layers, both in PostgreSQL:

1. **The transfer ledger**: a Merkle-linked hash chain of every agreement and
   every capacity refusal. It is the evidence the Verifier reasons over, so
   removing it removes the defence (see the ablation in RESULTS.md).
2. **The audit log**: database triggers record every insert, update and delete
   on the live tables (before, after, who, when) in a SHA-256 hash chain. The
   application cannot write it directly and cannot edit or delete any record;
   it can only add rows. User actions that are not row changes (toggles,
   restores) are recorded too.

`audit_verify()` (also **Verify now** and `GET /api/audit/verify`) recomputes
the chain and names the first broken row. **Checkpoints** write down the
chain head, so even a database superuser who rewrote the whole chain
consistently would no longer match a checkpoint taken before. That is the
honest limit: without an external copy of the checkpoints, a superuser could
rewrite everything after the last one.

Try it: in pgAdmin, change any value in `audit_log`, then press **Verify now**.

| API | |
|---|---|
| `GET /api/hospitals`, `PATCH /api/hospitals/{id}` | the tab's data; edit occupancy or behaviour |
| `POST /api/hospitals/{id}/restore`, `POST /api/restore` | back to real data |
| `GET /api/hospitals/{id}/history` | one hospital's changes |
| `GET /api/transfers`, `GET /api/transfers/{id}` | saved transfers; full trace |
| `GET /api/audit`, `GET /api/audit/verify`, `POST /api/audit/checkpoint` | audit log |
| `GET /api/ledger/verify` | recompute the ledger from the stored rows |
| `GET /api/data-integrity`, `GET /api/bottlenecks` | the two monitoring agents |
| `/api/docs` | everything, interactive |

---

## Reproduce every number

```bash
pip install -e ".[demo,db,analysis,attest]"
python experiments/validate_occupancy.py houston_baseline 5
python experiments/validate_occupancy.py houston_surge 5
python experiments/validate_occupancy.py phoenix_baseline 5
python experiments/data_integrity.py
python experiments/run_all.py 5
python experiments/significance.py 12
python experiments/adversarial.py 6
python experiments/registry_attack.py 6
python experiments/attestation.py 6
python experiments/collusion.py 6
python experiments/reputation.py 6
python experiments/replication.py 5
python experiments/sensitivity.py 5
python experiments/realtime.py
python experiments/build_provenance.py
python experiments/build_dashboard.py
python experiments/build_results.py          # -> docs/RESULTS.md and the README block
```

Every run is fixed by `(scenario, seed)` and gives the same result in any
process (`tests/test_core.py::test_same_seed_same_result_in_a_fresh_process`).
The real data is vendored, so no network access is needed except to re-fetch
it (`experiments/fetch_network.py`, `fetch_capability.py`, `fetch_routes.py`,
`fetch_transfer_mix.py`).

Tests:

```bash
python -m pytest                                           # database tests skip without a database
MAHROS_TEST_ADMIN_URL=postgresql://mahros_admin:mahros-admin-local@localhost:5440/mahros python -m pytest
python tests/api_db_smoke.py                               # every database API of a running app
node tests/ui_hospital_tab.js                              # the Hospital data tab, headless (needs jsdom)
node dashboard/check_console.js                            # console structure (needs jsdom)
```

---

## Layout

```
mahros/
  hospital/      hospital agents, resource agents, behaviour policies
  negotiation/   Contract Net, verification (challenge engine, argumentation,
                 attestation, collusion), scoring, message bus
  integrity/     Data Integrity Agent rules
  ledger/        hash-chain ledger
  db/            PostgreSQL: schema (locks, audit triggers), store, loader,
                 live sync, defaults
  sim/           real network builder, warm start, scenarios, strategies, runner
  calibration/   vendored real data: HHS, CMS POS, OSRM, California transfers
  fairness/ privacy/ eval/ llm/   burden balance, pseudonymisation, statistics,
                                  optional explanation-only LLM
  server/        FastAPI app and the console
experiments/     every result (see above)
tests/           pytest suite, API smoke test, UI check
docs/            PANEL_GUIDE, DEMO, RESULTS (generated), DATA_PROVENANCE
                 (generated), MODEL_ASSUMPTIONS, REVIEW_PANEL_REPORT
docker-compose.yml, Dockerfile, docker/pgadmin/
```

## Known limitations

- Negotiation behaviour is simulated; no public transfer-request logs exist.
- Facility reports are self-reported and COVID-era (collection ended
  2024-05-03); VA, IHS and DoD hospitals are excluded by the dataset.
- The case-mix is Californian (no Texas source); the transfer rate the model
  produces (4.5% of admissions) is above California's 1.6% of discharges, and
  transfer volume is swept.
- Step-down beds, OR slots and ventilators are assumed ratios.
- Road times are free-flow; the 12-minute handover is assumed and swept.
- Missing the clinical window is a proxy for harm, not a mortality model.
- The LLM explainer is optional and outside every decision.

## Licence

MIT. Data: US HHS and CMS (public domain), OpenStreetMap via OSRM (ODbL),
California HCAI (CC-BY).
