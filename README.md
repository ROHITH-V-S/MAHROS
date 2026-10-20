# MAHROS

**Multi-Agent Hospital Resource Optimization System.**

When an admitted patient gets worse and needs care their hospital cannot give
right now (an ICU bed, a cath lab, a neurosurgeon), MAHROS finds a receiving
hospital. It asks every capable hospital nearby at once, each hospital's agent
answers for itself, and a refusal ("we're full") can be **checked**, without
any hospital revealing its bed count.

> **Scope:** transfers of patients already admitted. Not ambulance routing, not
> emergency-department triage.

---

## Run it

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
running.

```bash
git clone https://github.com/ROHITH-V-S/MAHROS.git
cd MAHROS
docker compose up -d --build
```

| Open | What |
|---|---|
| http://localhost:8090 | MAHROS: **Live negotiation**, **Hospital data**, **Results** |
| http://localhost:5051 | pgAdmin, to look inside PostgreSQL (password if asked: `mahros-admin-local`) |

- Stop: `docker compose down` (data is kept). Start again: `docker compose up -d`.
- Wipe the database and reload the real data: `docker compose down -v`.
- Port in use? Copy `.env.example` to `.env` and change the ports.
- Top bar says "not saving"? Run `docker compose logs app`.

---

## How a transfer works

1. **Ask everyone at once.** The hospital with the patient sends the request
   to the 8 nearest hospitals that list the needed specialty (Contract Net).
2. **Each hospital answers for itself** from its own, private bed state: yes
   (with how soon) or no (with a reason).
3. **Check the refusals.** "We don't treat that" is checked against the public
   CMS register. "We're full" is checked against what the same hospital
   agreed to take in the last hour, on a ledger it cannot edit.
4. **Settle disputes.** A contradicted refusal is challenged; the hospital
   defends or signs it, or it is struck out (Dung's grounded semantics).
5. **Award** to the best bid: time to care first, then specialty fit, strain,
   and fairness.
6. **Record everything** in PostgreSQL in one transaction, with a
   hash-chained audit log.

| Agent | Decides |
|---|---|
| **Hospital agent** (one per hospital, with a resource agent per bed pool) | whether it can take the patient, what it *says*, and how it defends a refusal |
| **Transfer manager** (one per request) | who to ask, how to score the bids, who wins |
| **Verifier** | which refusals are contradicted by the record; runs random spot checks against collusion |
| **Data Integrity Agent** | flags impossible or copied weekly reports in the real HHS data |
| **Regional Monitor** | which needs go unmet and which bed pools are fullest |

**The optimisation problem.** Assign each transfer request $q$ a receiving
hospital $h_q$ to (1) maximise patients who reach care inside their clinical
window, $\sum_q \mathbf{1}[\Delta_q + \tau_{o_q,h_q} + p_{h_q} \le W(a_q)]$,
then (2) minimise time to care, then (3) balance the burden across hospitals,
subject to capacity, capability, **privacy** (no hospital's occupancy is ever
sent) and **self-interest** (a hospital may say something other than the
truth). MAHROS is compared with a phone tree (today's practice), a shared bed
registry, and two optimisers that see every hospital's true occupancy
(central greedy and batched Hungarian assignment).

---

## What is real, and what is simulated

| | Source | |
|---|---|---|
| 27 Houston and 30 Phoenix hospitals: beds, staffed ICU beds, weekly occupancy 2020–2024 | US HHS / CDC NHSN | **Real** |
| What each hospital can treat; cath-lab rooms | CMS Provider of Services | **Real** |
| Road travel times | OSRM / OpenStreetMap | **Real** |
| Which patients need transfer, and for what | 37,439 California transfers, 2021 (HCAI) | **Real** |
| Step-down beds, OR slots, ventilators | declared ratios | Assumed |
| Arrivals, deterioration, length of stay | fitted to the real occupancy | Simulated |
| **How hospitals answer, and whether honestly** | no public data exists | **Simulated** (the thing under study) |

For the Houston network the simulator's inputs are 8 observed, 4 derived,
6 assumed. Every one is listed in [docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md),
which is generated from the code.

---

## Results

<!-- results:start -->
- MAHROS places 13.0 percentage points more patients in time than a phone tree given perfect information (p < 0.0001).
- Against optimisers that see every hospital's true occupancy: -0.3 pts vs central greedy, +0.4 pts vs batched optimal; equivalent within ±2 points: yes / yes.
- On an honest network a shared registry ties MAHROS (-0.0 pts, Holm p = 0.8980).
- With half the hospitals refusing falsely, a shared registry places 68.6% and MAHROS 86.5% (+17.9 pts).
- With half the hospitals refusing falsely, success falls to 63.7% without checking and holds at 86.5% with it.
- With half the hospitals protective, checking refusals keeps success at 86.2%; without checking it falls to 64.5%, and without the ledger to 62.4%.
- Requiring hospitals to sign their refusals gives 62.3% to 86.5% success depending on how often a liar refuses to sign (no checking: 63.7%).
- A cartel of 6 cuts success to 79.2%; 15% random spot checks bring it back to 90.8%.
- Checking hospitals already caught more often is no better than uniform 15% spot checks at similar effort (+0.5 pts, p = 0.6581, 111% of the checks).
- Phoenix (30 hospitals, not re-tuned): MAHROS beats the phone tree by +9.9 to +14.3 points across four real weeks.
- Simulated occupancy is within 3.8 points of what Houston's hospitals reported (R² 0.95), and 3.0 points for Phoenix (R² 0.97), which was not tuned.
- 5.4% of Houston's weekly capacity reports and 0.2% of Phoenix's are impossible or look copied (20 of 27 and 3 of 30 hospitals have at least one report that cannot be right).
- Coordinator time per transfer: 1.1 min with MAHROS, 50.3 min by phone.
- MAHROS beat the phone tree under every value of every unsourced parameter. Checking refusals adds 21.8 points when protective hospitals lie consistently; when they lie only half the time a second round of asking already recovers almost everything, and checking costs up to 0.4 points.

Full tables, and every caveat: [docs/RESULTS.md](docs/RESULTS.md).
<!-- results:end -->

### Limits we state first

- **Behaviour is simulated.** No public data records transfer refusals; every
  behavioural setting is swept.
- **When every hospital is honest, a shared registry does as well.** MAHROS
  matters when they are not.
- **An optimiser that sees every hospital's true occupancy is slightly
  better.** That is the measured cost of keeping occupancy private.
- **Collusion defeats reactive checks.** Only random spot checks recover it.
- **The database owner can always bypass a trigger.** The locks stop ordinary
  edits; the audit chain and its checkpoints are what *detect* tampering.
- **Validation is of occupancy, not behaviour.** Missing the clinical window
  is a proxy for harm, not a mortality model.

More in [docs/MODEL_ASSUMPTIONS.md](docs/MODEL_ASSUMPTIONS.md).

---

## Reproduce and test

```bash
pip install -e ".[demo,db,analysis,attest,dev]"

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
python experiments/build_results.py   # writes docs/RESULTS.md and the block above
```

Every run is fixed by `(scenario, seed)`. The real data is vendored, so no
network access is needed (the `fetch_*.py` scripts re-download it).

```bash
python -m pytest     # database tests run when MAHROS_TEST_ADMIN_URL is set:
# MAHROS_TEST_ADMIN_URL=postgresql://mahros_admin:mahros-admin-local@localhost:5440/mahros
python tests/api_db_smoke.py          # every database API of a running app
```

---

## Layout

```
mahros/
  hospital/      hospital and resource agents, behaviour policies
  negotiation/   Contract Net, refusal checks, argumentation, signed refusals,
                 collusion, scoring, message bus
  db/            PostgreSQL schema (locks, audit triggers), store, loader
  server/        FastAPI app and the browser console
  sim/           real network builder, scenarios, baselines, runner
  calibration/   vendored real data (HHS, CMS, OSRM, California transfers)
  integrity/  ledger/  fairness/  privacy/  eval/  llm/
experiments/     one script per result
tests/           pytest suite and smoke tests
docs/            DEMO, PANEL_GUIDE, MODEL_ASSUMPTIONS, RESULTS*, DATA_PROVENANCE*
                 (* generated)
```

The optional LLM (`mahros/llm/`) only writes explanations. It is off by
default and never makes a decision.

## Licence

MIT. Data: US HHS and CMS (public domain), OpenStreetMap via OSRM (ODbL),
California HCAI (CC-BY).
