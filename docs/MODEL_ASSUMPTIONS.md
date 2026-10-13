# Model assumptions

Every number the simulation uses that is **not** read from a dataset, with
where it came from and how far it was tested.

- **What is real** (hospitals, beds, specialties, roads, occupancy, case-mix)
  is in [DATA_PROVENANCE.md](DATA_PROVENANCE.md), generated from the code.
- **What the model produces** (every result) is in [RESULTS.md](RESULTS.md),
  generated from `results/*.json`. This file quotes no results on purpose:
  numbers copied by hand into several documents are how they used to
  contradict each other.

## Tags

| Tag | Meaning |
|---|---|
| **[OBSERVED]** | Read from a named public dataset |
| **[VALIDATED]** | Checked against real data and survived (see RESULTS.md) |
| **[CITED]** | Taken from a published figure (references at the end) |
| **[DERIVED]** | Computed from other values by a stated rule |
| **[TUNED]** | Chosen to put the model in a regime where the question is answerable; declared |
| **[UNSOURCED]** | No source found. Swept in `experiments/sensitivity.py` unless marked otherwise |

---

## 1. The network: real

27 Houston hospitals (headline) and 30 Phoenix hospitals (replication), each
with the beds, staffed ICU beds, specialties, cath-lab rooms and road times it
really had. **[OBSERVED]**. Details, and the three pools no dataset reports
(step-down beds, OR slots, ventilators: **[ASSUMED]** ratios), are in
DATA_PROVENANCE.md.

A real scenario is a week the hospitals lived through, chosen by observed
network occupancy, not a dial.

**The synthetic network** (12 hospitals in three tiers, ~70% occupancy) is
kept only for the unit tests and the ICU-scarcity counterfactual. It is never
a headline result. Its tier tables are in `mahros/sim/scenario.py`.

## 2. Patient flow

| Parameter | Value | Tag | Note |
|---|---|---|---|
| Arrival rate per hospital and pool | fitted | **[DERIVED]** | Erlang-B inversion of each hospital's reported occupancy (`real_network.py`) |
| Starting occupancy | reported week | **[OBSERVED]** / **[DERIVED]** | Warm start: ward, step-down and ICU filled to the reported week; procedure rooms empty (`warm_start.py`) |
| Remaining stay of starting patients | length-biased lognormal | **[DERIVED]** | lognormal(μ+σ², σ) × U(0,1); tested in `tests/test_warm_start.py` |
| Mean length of stay | ~4.6 days | **[CITED]** | US acute average; LOS is scaled to it, not measured per hospital |
| Escalation probability | 0.14 of admissions | **[CITED]**, with a caveat | Produces 4.5% of admissions as transfer requests on the real Houston network, the published US figure [R3]. California 2021 shows **1.6%** of discharges transferred, so the model is on the high side; swept 0.07–0.21. (The scenario used to *declare* 0.18 while real networks silently ran on 0.14; the value in use is now the value declared.) |
| Escalation delay | lognormal, mean 7 h | **[UNSOURCED]** | Swept 4–12 h |
| Diurnal arrival curve | ±45%, peak 14:00 | **[UNSOURCED]** | Not swept: it shapes when requests arrive, equally for every method |

**Validation runs cold.** The occupancy check starts the network empty, so it
tests whether the fitted rates *reach* the reported occupancy, not where the
run was put. It is run for Houston (two weeks) and Phoenix (fitted
identically, nothing tuned).

## 3. Which patients need moving (case-mix)

**[OBSERVED]**, with two **[TUNED]** mapping decisions.

The share of transfer requests needing each resource comes from **37,439 real
interhospital transfers**, California 2021 (HCAI Patient Discharge Data,
CC-BY), by diagnostic group (`mahros/calibration/case_mix.py`):

- each diagnostic group is mapped to one escalation profile (table in the
  code; e.g. circulatory → cardiac, nervous system → neuro ICU, injuries →
  trauma theatre, everything else medical → step-down bed). **[TUNED]**
- circulatory transfers are split 50/50 between cath lab and cardiac ICU,
  because the data does not say which. **[TUNED]**
- the children's share (1.1%) comes from the data's own age breakdown.

What the real mix changed from the old guess: cardiac 9% → 25%, trauma
12% → 2.5%, paediatric 7% → 1.1%.

Acuity range and length of stay per profile remain **[TUNED]**.

## 4. Clinical safe windows **[TUNED]**

| Acuity | Name | Window |
|---|---|---|
| 1 | Routine | 720 min |
| 2 | Urgent | 480 min |
| 3 | Emergent | 240 min |
| 4 | Critical | 150 min |
| 5 | Life-threatening | 90 min |

No source gives a per-acuity cut-off. Time-to-care matters (each hour of ICU
transfer delay adds about 3% to the odds of death [R2], measured *within*
hospitals). Absolute success rates move with these windows; the **ranking** of
methods is what the claims rest on, and the sweep reports whether it holds.

## 5. Protocol parameters **[TUNED]**

| Parameter | Value | Note |
|---|---|---|
| Bid window | 2 min | All peers asked at once; one window regardless of how many |
| Peers per round | 8 nearest capable | Widened on the second round |
| Rounds | 2 | Retry only while clinical time remains |
| Reservation hold | 45 min | |
| Retry interval | 20 min | |
| MAHROS staff time per request | 0.5 min | **[UNSOURCED]**, swept 0.25–2 |
| Reserve window for a capacity challenge | 60 min | A refusal is contradicted only by an acceptance this close in time |

## 6. The phone-tree baseline

What happens today: a coordinator rings hospitals one at a time, nearest first.

| Parameter | Value | Tag |
|---|---|---|
| Call duration | N(9, 3) min, minimum 2 | **[UNSOURCED]**, swept 5–15 |
| Calls before giving up | 8 | **[TUNED]** |
| Coordinators | 1 per 150 inpatient beds | **[ASSUMED]** |
| **Stale information** | **0.0** (headline) | **[UNSOURCED]**; 0.05–0.40 swept |

**Why staleness is zero in the headline.** It used to be 0.25: a quarter of
the hospitals that said "yes" were lost because the coordinator was working
from stale information. No source gives that rate, and it made the baseline
weaker by choice. The headline now gives the phone tree perfect information,
the strongest version we can defend; 0.25 is reported as a sensitivity row.

## 7. Scoring weights **[TUNED]**

Time to care 0.45, capability match 0.25, strain after accepting 0.15,
fairness 0.15. For acuity ≥ 4 the score collapses to speed and capability.
Bids that cannot meet the clinical window are discarded before scoring, never
traded off. Bidder opportunity cost is convex in strain (strain³).

## 8. The adversary model

The behaviour under study. **No dataset records transfer refusals**, so this
is simulated, and stated as such everywhere.

| Policy | Behaviour | Role |
|---|---|---|
| honest | Reports its assessment | Control |
| protective ("strategic") | Says "we're full" or "we don't do that" when a patient is expensive (stay ≥ 24 h) or it is already ≥ 55% strained; never for acuity 5 | The attack |
| cautious ("defensive") | Never lies; holds 2 extra units in reserve | False-accusation control: its refusals are all true |
| colluding | Protective, and abstains whenever another cartel member just refused the same resource | The attack that defeats reactive checks |

| Parameter | Value | Tag |
|---|---|---|
| Comfort threshold | 0.55 | **[UNSOURCED]**, swept 0.30–0.80 |
| Shirk probability | 1.0 | **[UNSOURCED]**, swept 0.5–1.0 |
| Attestation deterrence (P a liar declines to sign) | 0.8 | **[UNSOURCED]**, swept 0–1 |
| Which hospitals lie | highest tier first, nested by dose | **[TUNED]**: the tertiary centre is asked most and gains most |

**Two assumptions to state plainly.**

1. *Unattested protocol:* a challenged liar is assumed unable to defend
   itself (`BiddingPolicy.defend`). That is optimistic: a real bed manager
   could repeat the lie. The attested protocol replaces it with a swept
   question, "will a liar sign?", and its whole range is reported. Lead with
   the attested range.
2. *"Zero false accusations"* is a **safety property that is tested**, not a
   measurement. An honest refusal is backed by the hospital's real state, so it
   can always be defended or signed. The property has failed once: spot checks
   on capability refusals struck out honest hospitals until the predicate was
   split. Tests guard it now.

**Real-world grounding.** No data shows hospitals lying about capacity. Two
things are observed. First, the hospitals' own capacity reports are often
unreliable: the Data Integrity Agent flags a share of the real HHS reports as
impossible or copied (see RESULTS.md; a flag means unreliable, not dishonest).
Second, US law already treats "refusing despite capacity" as unacceptable
for emergency patients: EMTALA, 42 CFR 489.24(f). It does **not** apply to
patients already admitted, which is MAHROS's scope (489.24(f)(2)), so it is
cited as precedent, not as applicable law.

## 9. Still not modelled

- **Behaviour from data.** The single most important gap; needs transfer-centre
  logs, which are not public (Ontario, Korea and US centres hold them).
- **Ambulance availability**, deterioration in transit, repatriation, billing.
- **Clinical outcomes.** Missing the window is a proxy for harm, not a
  mortality model.
- **Texas case-mix.** The case-mix is Californian; no Texas source exists.

Modelled since earlier versions: collusion (`experiments/collusion.py`) and a
reputation term, risk-based spot checks (`experiments/reputation.py`).

## 10. Reproducibility

Every run is determined by `(scenario, seed)` and gives the same result in any
process. That was **not** true for runs with attestation until 2026-10-13: two
bugs (request ids scrubbed by the privacy layer, a hospital RNG seeded with
Python's per-process `hash()`) made them vary. Both are fixed and guarded by
`tests/test_core.py::test_same_seed_same_result_in_a_fresh_process`. Every
number in RESULTS.md was produced after the fix.

## References

| # | Source |
|---|---|
| R1 | Interhospital critical care transfer delays result from organisational not geographical factors (49 UK hospitals). https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4472710/ |
| R2 | Association between ICU transfer delay and hospital mortality. https://pubmed.ncbi.nlm.nih.gov/27352032/ |
| R3 | Identifying racial/ethnic disparities in interhospital transfer. https://pubmed.ncbi.nlm.nih.gov/32700216/ |
| R4 | Interhospital transfers occur less frequently for uninsured patients. https://www.ajmc.com/view/interhospital-transfers-occur-less-frequently-for-uninsured-patients |
| R5 | Interhospital transfers: the need for standards. https://pmc.ncbi.nlm.nih.gov/articles/PMC11094628/ |
| R7 | Mueller et al., Interhospital transfer: transfer processes and patient outcomes, J Hosp Med 2019. https://pubmed.ncbi.nlm.nih.gov/30986189/ |
| D1 | HCAI, Acute Care Hospital Transfers by MDC, 2021. https://data.chhs.ca.gov/dataset/acute-care-hospital-transfers-by-major-diagnostic-category-mdc |
| L1 | 42 CFR 489.24(f), recipient hospital responsibilities (EMTALA). https://www.law.cornell.edu/cfr/text/42/489.24 |

These were found by targeted search, not a systematic review. Read each in
full before citing it in the paper.
