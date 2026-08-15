# Model assumptions

Every number in the simulation that is not derived from another number is listed
here. This document exists so a reviewer can check the model rather than trust it,
and so the sensitivity analysis has something concrete to vary.

**None of these are measurements from a real hospital.** They are modelling
choices, some anchored to published figures, others chosen to put the system in a
regime where the research question is answerable. Where a value was picked to make
the simulation behave, this document says so.

---

## 1. Clinical safe windows

Time from deterioration to definitive care before outcomes degrade.

| Acuity | Name | Window |
|---|---|---|
| 1 | Routine | 720 min |
| 2 | Urgent | 480 min |
| 3 | Emergent | 240 min |
| 4 | Critical | 150 min |
| 5 | Life-threatening | 90 min |

**Anchor.** The relevant benchmark is *interhospital* transfer, not in-hospital
response: door-in-door-out targets of <60 min plus transport, with total transfer
times of 90–180 min typical for time-critical cases.

**Why these were revised.** An earlier draft used 45 min for acuity 5. With a
median inter-hospital travel time of ~45 min, that window was unreachable by *any*
strategy — road, air, or otherwise — and produced a 0% success rate for the sickest
patients across every arm. That is a geometry artifact, not a finding. The revised
values keep the constraint genuinely binding (acuity 5 succeeds ~72% of the time
under stress) without being impossible.

**Sensitivity.** Absolute success rates move substantially with these values. The
*ranking* of strategies is considerably more robust. Any claim in the paper should
be about the ranking and the relative gaps.

---

## 2. Network topology

| Parameter | Value | Rationale |
|---|---|---|
| Hospitals | 12 (30 for scalability) | A district/metropolitan referral cluster |
| Region radius | 35 km | Not a whole state — the scale at which escalation transfers are clinically viable |
| Ambulance speed | 50 km/h | Blended road speed including traffic |
| Fixed transfer overhead | 12 min | Patient packaging and crew handover at both ends; why short hops are not free |

Resulting travel times: **13 min minimum, 45 min median, 71 min maximum.**

Tier mix for 12 hospitals: 7 primary, 3 district, 2 tertiary. Tertiary centres are
placed centrally, smaller hospitals spread outward — the pattern that makes
escalation transfers necessary, since scarce specialty capability is concentrated.

---

## 3. Capacity per tier

| Resource | Tier 1 (primary) | Tier 2 (district) | Tier 3 (tertiary) |
|---|---|---|---|
| Ward beds | 30 | 80 | 200 |
| HDU beds | 4 | 12 | 30 |
| ICU beds | 2 | 8 | 26 |
| OR slots | 1 | 3 | 8 |
| Ventilators | 2 | 6 | 22 |
| Cath labs | — | — | 2 |

Each is jittered ±15% per hospital per seed.

Specialties: tier 1 general only; tier 2 adds obstetric and trauma; tier 3 adds
cardiac, neuro, and paediatric. **This concentration is the reason transfers
happen at all** — a primary hospital with a free ICU bed still cannot take a
neurosurgical patient.

`capacity_scale` below 1.0 models India-style critical-care scarcity (≈0.22 ICU
beds per 1,000 population against a global average near 2.7–2.9 total beds).

---

## 4. Patient flow

| Parameter | Value | Note |
|---|---|---|
| Arrivals/hour, tier 1 | 0.54 | **Tuned** to ~70% baseline occupancy |
| Arrivals/hour, tier 2 | 1.44 | ″ |
| Arrivals/hour, tier 3 | 3.60 | ″ |
| Diurnal variation | ±45%, peak ~14:00 | Sinusoidal |
| Initial admission mix | 90% ward / 7.5% HDU / 2.5% ICU | Critical beds kept lightly loaded at admission |
| Initial LOS | lognormal, mean 43 h ward / 30 h HDU / 40 h ICU | σ = 0.6 |
| Escalation probability | 0.18 of admissions | **Tuned** — see below |
| Escalation delay | lognormal, mean 7 h | Most deterioration within the first 24 h |

**On tuning.** The arrival rates were chosen so the network sits near 70%
occupancy: busy enough that escalations contend for capacity, slack enough that the
baseline is not already in permanent crisis. An earlier draft ran at an offered
load exceeding capacity, rejecting 20.6% of admissions before the experiment
started — which measures a bed shortage, not a coordination problem.

**Escalation probability is the one parameter tuned against an external figure.**
Most escalations are absorbed in-house; only those the hospital cannot serve become
transfer requests. At 0.18, the resulting *transfer* rate is 3.7–4.3% of
admissions, against the ~3.5% reported for real interhospital transfers. This is
the model's main external calibration check.

---

## 5. Escalation profiles

| Resource | Specialty | Acuity range | Mean LOS | Weight |
|---|---|---|---|---|
| ICU bed | general | 3–5 | 60 h | 0.20 |
| HDU bed | general | 2–3 | 36 h | 0.15 |
| OR slot | trauma | 4–5 | 5 h | 0.12 |
| Ventilator | general | 4–5 | 48 h | 0.11 |
| OR slot | general | 2–4 | 4 h | 0.10 |
| Cath lab | cardiac | 4–5 | 3 h | 0.09 |
| ICU bed | neuro | 4–5 | 72 h | 0.08 |
| HDU bed | obstetric | 3–4 | 24 h | 0.08 |
| ICU bed | paediatric | 3–5 | 50 h | 0.07 |

LOS is lognormal about the mean, σ = 0.45.

---

## 6. Protocol parameters

| Parameter | Value | Note |
|---|---|---|
| Bid window | 2.0 min | Bids are collected **in parallel** — one window regardless of peer count. This parallelism is the claim being tested against the phone tree. |
| Max peers per round | 8 | Widened on retry |
| Max rounds | 2 | Retry only if clinical time remains |
| Reservation hold | 45 min | Then reclaimed |
| Retry interval | 20 min | Capacity frees continuously, so retrying is realistic |
| Agent coordination cost | 0.5 min/request | MAHROS's staff-time cost |
| Contested band | 0.05 score gap | Below this, the LLM coordinator is consulted |

### Phone-tree baseline

| Parameter | Value | Note |
|---|---|---|
| Call duration | N(9, 3) min, min 2 | Serial, one at a time |
| Max calls | 8 | |
| Coordinators | 1 / 2 / 3 by tier | The resource that runs out during a surge |
| Stale-information probability | 0.25 | Coordinators work from whiteboards and hearsay |

Calls being serial is the crux: by call six, the situation has moved on, and the
bid is evaluated at a later clock time. **This is the mechanism by which MAHROS
wins, so it deserves scrutiny.** The 25% stale-information rate is the least
defensible number in this document — it is plausible but unsourced. Treat the
phone-tree comparison as directional, and run the sensitivity analysis on it.

---

## 7. Scoring weights

| Term | Weight | Meaning |
|---|---|---|
| Time to care | 0.45 | Speed to definitive care |
| Capability match | 0.25 | Specialty fit |
| Post-accept strain | 0.15 | Prefer a peer who stays comfortable |
| Fairness | 0.15 | Burden balancing |

For acuity ≥ 4, the score collapses toward speed and capability — equity must not
cost a critically ill patient minutes. Bids that cannot meet the clinical deadline
are **discarded before scoring**, never traded off. Both properties are tested.

Bidder opportunity cost is convex in strain (`strain³`): the last free ICU bed is
worth far more than the fifth.

---

## 8. What is deliberately not modelled

- **Strategic misreporting.** Hospitals bid honestly. A hospital that
  under-reports capacity to avoid receiving transfers would defeat the current
  design; the ledger deters denial after the fact, not misreporting before it.
  This is the most important gap.
- **Ambulance fleet as a constraint.** Transport is assumed available. In a real
  surge it is not.
- **Patient deterioration during transport.**
- **Clinical outcomes.** Breach of the safe window is a proxy for harm, not a
  mortality model.
- **Repatriation.** Patients do not return to the origin hospital, which
  understates long-run burden on tertiary centres.
- **Cost, billing, insurance.** Real transfer decisions are not free of these.

---

## 9. Reproducibility

Every run is fully determined by `(scenario_name, seed)`. All randomness flows
through seeded `random.Random` instances. Headline figures are the mean of 5 seeds
with 95% confidence intervals. The LLM is outside the decision path by default, so
no reported number depends on a model generation.

`tests/test_core.py` asserts that the same seed reproduces identical results and
that different seeds do not.
