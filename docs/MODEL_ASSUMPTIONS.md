# Model assumptions

Every number in the simulation that is not derived from another number is listed
here, **with its provenance**. This document exists so a reviewer can check the
model rather than trust it, and so the sensitivity analysis has something concrete
to vary.

**None of these are measurements from a real hospital.** They are modelling
choices. What this document adds is an honest label on each one.

## Provenance tags

| Tag | Meaning |
|---|---|
| **[VALIDATED]** | Checked against a real facility-level dataset. See §0.5. |
| **[CITED]** | Traceable to a published figure. Reference listed in §10. |
| **[DERIVED]** | Computed from other values in this document. |
| **[TUNED]** | Chosen to put the model in a regime where the research question is answerable. Declared, not hidden. |
| **[UNSOURCED]** | Plausible but with no source found. **Every one of these must appear in the sensitivity analysis.** |

Counts as of this revision: **3 validated · 9 cited · 6 derived · 6 tuned ·
7 unsourced** (five original, plus the two adversary parameters introduced with
the deliberation phase).

The unsourced values are the paper's exposed surface, and §8 lists them together
so a reviewer does not have to hunt. **Every one is now actually swept** by
`experiments/sensitivity.py`, which prints any ranking that fails to survive.

---

## 0.5 What is now checked against real data

Three structural assumptions have been promoted from **[TUNED]** to
**[VALIDATED]** against the US HHS facility-level hospital capacity dataset
(CDC NHSN reporting, public domain, no patient-level data), 3,186 short-term
acute-care hospitals, collection week of 2021-01-10.

| Assumption | Model | Observed | Verdict |
|---|---|---|---|
| Network sits near 70% inpatient occupancy | 0.70 | **0.700** | **[VALIDATED]** |
| ICU runs hotter than the general ward | +0.10 | +0.103 | **[VALIDATED]** |
| ICU depth rises with hospital size | 0.056 / 0.080 / 0.102 | 0.108 / 0.118 / 0.127 | **[VALIDATED]** in shape; levels diverge by design |

The arrival rates in §4 were tuned to a ~70% occupancy target *before* any real
data was consulted. The observed mean across 3,186 hospitals is 70.0%. That is a
confirmation, not a fit — no parameter was adjusted to produce it.

**Declared divergence.** The modelled tier-1 and tier-2 hospitals carry roughly
half the ICU depth of their US counterparts. This is deliberate: the scenarios
target an India-style critical-care-scarce network. `mahros/calibration/hhs.py`
reports it as NOTED with the magnitude attached rather than passing it silently.

A further structural confirmation: **27.6% of the smallest real hospitals report
no staffed adult ICU beds at all.** The model encodes the same scarcity as
missing *specialties* rather than missing beds, but the premise — that a large
minority of hospitals simply cannot escalate — is observed, not assumed.

Reproduce with `python experiments/calibrate.py`. Re-pull the source data with
`--refetch`.

---

## 0. What the literature establishes

Three findings frame the whole model and should be stated before any parameter:

1. **Transfer delay is a coordination failure, not a distance problem.** Across
   49 UK hospitals, median delay to admission for deteriorating ward patients was
   **22 hours**, and delay correlated only weakly with distance travelled — the
   authors attribute it to organisational rather than geographical factors [R1].
   This is the single most important external justification for the project: the
   bottleneck MAHROS attacks is the one the literature identifies.

2. **Delay kills, and the gradient is measured.** In a multicentre study of 3,789
   patients, each additional hour of delay to ICU transfer carried an adjusted
   **3% increase in the odds of death**; delayed patients had 33.2% mortality
   against 24.5% for those transferred early [R2]. *(Note: [R2] measures
   ward-to-ICU transfer within a hospital, not interhospital transfer. It
   establishes that the time-to-care gradient is real and steep; it does not
   directly calibrate interhospital timings.)*

3. **Who gets transferred is already inequitable.** Interhospital transfer rates
   differ by race — 4.7% for White patients against 3.9% Black and 3.8% Hispanic
   [R3] — and uninsured critically ill patients have reduced odds of transfer
   [R4]. The fairness layer therefore addresses a documented disparity rather
   than a hypothetical one.

Also relevant: interhospital transfer currently has **no accepted standard
process** [R5], which is what makes a protocol contribution meaningful rather
than a reinvention of established practice.

---

## 1. Clinical safe windows

Time from deterioration to definitive care before outcomes degrade.

| Acuity | Name | Window | Tag |
|---|---|---|---|
| 1 | Routine | 720 min | **[TUNED]** |
| 2 | Urgent | 480 min | **[TUNED]** |
| 3 | Emergent | 240 min | **[TUNED]** |
| 4 | Critical | 150 min | **[TUNED]** |
| 5 | Life-threatening | 90 min | **[TUNED]** |

**Anchor.** The relevant benchmark is *interhospital* transfer, not in-hospital
response: door-in-door-out targets of <60 min plus transport, with total transfer
times of 90–180 min typical for time-critical cases. The published mortality
gradient of 3%/hour [R2] supports treating time as the dominant term but does not
fix a threshold — no source gives a per-acuity cutoff, so these remain tuned.

**Why these were revised.** An earlier draft used 45 min for acuity 5. With a
median inter-hospital travel time of ~45 min, that window was unreachable by *any*
strategy and produced a 0% success rate for the sickest patients across every arm.
That is a geometry artifact, not a finding. The revised values keep the constraint
genuinely binding (acuity 5 succeeds ~72% of the time under stress) without being
impossible.

**Sensitivity — mandatory.** Absolute success rates move substantially with these
values. The *ranking* of strategies is considerably more robust. Any claim in the
paper should be about the ranking and the relative gaps, and the sensitivity sweep
must show the ranking surviving across the plausible range.

---

## 2. Network topology

| Parameter | Value | Tag | Rationale |
|---|---|---|---|
| Hospitals | 12 (30 for scalability) | **[TUNED]** | A district/metropolitan referral cluster |
| Region radius | 35 km | **[TUNED]** | The scale at which escalation transfers are clinically viable |
| Ambulance speed | 50 km/h | **[TUNED]** | Blended road speed including traffic |
| Fixed transfer overhead | 12 min | **[UNSOURCED]** | Packaging and handover at both ends |

Resulting travel times: **13 min minimum, 45 min median, 71 min maximum.** **[DERIVED]**

Tier mix for 12 hospitals: 7 primary, 3 district, 2 tertiary. Tertiary centres are
placed centrally, smaller hospitals spread outward — the pattern that makes
escalation transfers necessary, since scarce specialty capability is concentrated.

**Supporting evidence.** [R1] found delay only weakly correlated with distance,
which means the model's exact geography matters less than its coordination layer.
This is convenient for the model's validity and should be stated as such rather
than relied on silently.

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

**[TUNED]** — ratios chosen to reflect a referral hierarchy. Each is jittered ±15%
per hospital per seed.

Specialties: tier 1 general only; tier 2 adds obstetric and trauma; tier 3 adds
cardiac, neuro, and paediatric. **This concentration is the reason transfers
happen at all** — a primary hospital with a free ICU bed still cannot take a
neurosurgical patient.

`capacity_scale` below 1.0 models India-style critical-care scarcity (≈0.22 ICU
beds per 1,000 population against a global average near 2.7–2.9 total beds).

---

## 4. Patient flow

| Parameter | Value | Tag | Note |
|---|---|---|---|
| Arrivals/hour, tier 1 | 0.54 | **[TUNED]** | To ~70% baseline occupancy |
| Arrivals/hour, tier 2 | 1.44 | **[TUNED]** | ″ |
| Arrivals/hour, tier 3 | 3.60 | **[TUNED]** | ″ |
| Diurnal variation | ±45%, peak ~14:00 | **[UNSOURCED]** | Sinusoidal |
| Initial admission mix | 90% ward / 7.5% HDU / 2.5% ICU | **[TUNED]** | |
| Initial LOS | lognormal, mean 43 h ward / 30 h HDU / 40 h ICU | **[TUNED]** | σ = 0.6 |
| Escalation probability | 0.18 of admissions | **[TUNED]** | Calibrated — see below |
| Escalation delay | lognormal, mean 7 h | **[UNSOURCED]** | Most deterioration within 24 h |

**On tuning.** The arrival rates were chosen so the network sits near 70%
occupancy: busy enough that escalations contend for capacity, slack enough that the
baseline is not already in permanent crisis. An earlier draft ran at an offered
load exceeding capacity, rejecting 20.6% of admissions before the experiment
started — which measures a bed shortage, not a coordination problem.

**Escalation probability is the one parameter calibrated against an external
figure.** **[CITED]** Most escalations are absorbed in-house; only those the
hospital cannot serve become transfer requests. At 0.18, the resulting *transfer*
rate is **3.43–4.61% of admissions** (measured across seeds 1, 2, 3, 7, 42 on
`surge_scarcity`; 217–306 transfers per 6,228–6,679 admissions), against a
published **4.5% of admissions** undergoing interhospital transfer [R3] — the
published figure falls inside the model's range. (A separate study reports 1.8%
of *ED patients* transferred [R6] — a different denominator, and not the
comparable figure.) This is the model's main external calibration check, and it
was not tuned for: the ratio falls out of the escalation model.

Reproduce with:

```python
from mahros.sim.runner import SimulationRunner, RunConfig
from mahros.sim.scenario import SCENARIOS
import dataclasses
sc = dataclasses.replace(SCENARIOS["surge_scarcity"], seed=42)
r = SimulationRunner(RunConfig(scenario=sc, strategy="mahros", seed=42)).run()
print(100 * len(r.requests) / r.total_admissions)
```

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

**[TUNED]** — the mix is a modelling choice reflecting a general referral cluster,
not a measured case-mix. LOS is lognormal about the mean, σ = 0.45.

---

## 6. Protocol parameters

| Parameter | Value | Tag | Note |
|---|---|---|---|
| Bid window | 2.0 min | **[TUNED]** | Bids collected **in parallel** — one window regardless of peer count. This parallelism is the claim being tested. |
| Max peers per round | 8 | **[TUNED]** | Widened on retry |
| Max rounds | 2 | **[TUNED]** | Retry only if clinical time remains |
| Reservation hold | 45 min | **[TUNED]** | Then reclaimed |
| Retry interval | 20 min | **[TUNED]** | Capacity frees continuously |
| Agent coordination cost | 0.5 min/request | **[UNSOURCED]** | MAHROS's staff-time cost |
| Contested band | 0.05 score gap | **[TUNED]** | Below this the LLM coordinator is consulted |

### Phone-tree baseline

| Parameter | Value | Tag | Note |
|---|---|---|---|
| Call duration | N(9, 3) min, min 2 | **[UNSOURCED]** | Serial, one at a time |
| Max calls | 8 | **[TUNED]** | |
| Coordinators | 1 / 2 / 3 by tier | **[TUNED]** | The resource that runs out during a surge |
| Stale-information probability | 0.25 | **[UNSOURCED]** | Coordinators work from whiteboards and hearsay |

Calls being serial is the crux: by call six, the situation has moved on, and the
bid is evaluated at a later clock time. **This is the mechanism by which MAHROS
wins, so it deserves the most scrutiny in the document.**

**Status of the evidence.** Transfer centres and their call workflows are
documented qualitatively [R5], [R7] — transfer coordinators do collect information
serially, and accepting physicians report time wasted on transfers that do not
proceed. But no source was found quantifying **minutes per call** or a **staleness
rate**. Both therefore remain unsourced, and the phone-tree comparison must be
reported as *directional* until the sensitivity analysis brackets them.

---

## 7. Scoring weights

| Term | Weight | Meaning |
|---|---|---|
| Time to care | 0.45 | Speed to definitive care |
| Capability match | 0.25 | Specialty fit |
| Post-accept strain | 0.15 | Prefer a peer who stays comfortable |
| Fairness | 0.15 | Burden balancing |

**[TUNED]** — the weighting is a design choice. Its *direction* is supported: time
dominates because the mortality gradient is measured in hours [R2].

For acuity ≥ 4, the score collapses toward speed and capability — equity must not
cost a critically ill patient minutes. Bids that cannot meet the clinical deadline
are **discarded before scoring**, never traded off. Both properties are tested.

Bidder opportunity cost is convex in strain (`strain³`) **[TUNED]**: the last free
ICU bed is worth far more than the fifth.

---

## 8. The five unsourced values

Collected here so a reviewer can find them in one place. Each must appear in the
sensitivity analysis with a stated range.

| # | Parameter | Value | Why it matters | Proposed sweep |
|---|---|---|---|---|
| 1 | Phone-tree staleness | 0.25 | **Drives a large share of the headline win.** The least defensible number in this document. | 0.05 – 0.40 |
| 2 | Call duration | N(9,3) min | Sets the phone tree's coordinator cost | 5 – 15 min |
| 3 | Agent coordination cost | 0.5 min | Sets MAHROS's staff-time saving | 0.25 – 2.0 min |
| 4 | Fixed transfer overhead | 12 min | Shifts every arrival time uniformly | 8 – 20 min |
| 5 | Escalation delay mean | 7 h | Shapes contention over the day | 4 – 12 h |
| 6 | Strategic comfort threshold | 0.55 | Sets how readily a hospital fabricates a refusal — i.e. how strong the attack is | 0.30 – 0.80 |
| 7 | Strategic shirk probability | 1.0 | Whether shirking is a consistent policy or a coin flip | 0.5 – 1.0 |

**On #6 and #7 specifically.** These govern the *strength of the adversary*, and
there is an obvious temptation to choose them so the defence looks good. Both are
swept in full in `experiments/sensitivity.py` §6 and `experiments/adversarial.py`,
and the deliberation phase helps across the entire range (+8.1 to +16.0 points at
50% strategic). Had it helped only at one setting, that would have been the
finding, and it would have had to be reported as such.

**Honest statement for the paper:** if the strategy *ranking* does not survive
sweep #1, the phone-tree comparison must be demoted from a headline claim to a
qualified observation. That outcome is possible and should be reported if it
occurs.

---

## 9. What is deliberately not modelled

- ~~**Strategic misreporting.**~~ **Now modelled** — see
  `mahros/hospital/behaviours.py` and §12. This was previously flagged here as
  the most important gap and the strongest available novelty contribution. The
  deliberation phase is the response to it.
- **Collusion.** Two hospitals coordinating their refusals, or corroborating each
  other's false defences, would defeat the current challenge mechanism, which
  assumes each hospital argues alone. This is now the most important gap.
- **Reputation over time.** A hospital caught fabricating a refusal is overruled
  for that patient and nothing more. A persistent reputation term — where a
  history of broken commitments changes how future claims are weighed — is the
  obvious next mechanism, and is not implemented.
- **Ambulance fleet as a constraint.** Transport is assumed available. In a real
  surge it is not.
- **Patient deterioration during transport.**
- **Clinical outcomes.** Breach of the safe window is a proxy for harm, not a
  mortality model. The 3%/hour gradient [R2] could convert breaches into an
  estimated excess-mortality figure — currently not done, and it would strengthen
  the paper.
- **Repatriation.** Patients do not return to the origin hospital, which
  understates long-run burden on tertiary centres.
- **Cost, billing, insurance.** Real transfer decisions are not free of these,
  and insurance status demonstrably affects transfer odds [R4].

---

## 10. References

| # | Source |
|---|---|
| R1 | Interhospital critical care transfer delays result from organisational not geographical factors: secondary analysis of deteriorating ward patients in 49 UK hospitals. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4472710/ |
| R2 | Association between intensive care unit transfer delay and hospital mortality: a multicenter investigation. https://pubmed.ncbi.nlm.nih.gov/27352032/ |
| R3 | Identifying Racial/Ethnic Disparities in Interhospital Transfer: an Observational Study. https://pubmed.ncbi.nlm.nih.gov/32700216/ |
| R4 | Interhospital transfers occur less frequently for uninsured patients. https://www.ajmc.com/view/interhospital-transfers-occur-less-frequently-for-uninsured-patients |
| R5 | Interhospital Transfers: The Need for Standards. https://pmc.ncbi.nlm.nih.gov/articles/PMC11094628/ |
| R6 | Interhospital transfers from U.S. emergency departments: implications for resource utilization, patient safety, and regionalization. https://pubmed.ncbi.nlm.nih.gov/24033705/ |
| R7 | Mueller et al. Interhospital Transfer: Transfer Processes and Patient Outcomes. J Hosp Med 2019. https://pubmed.ncbi.nlm.nih.gov/30986189/ |
| R8 | Interhospital Facility Transfers in the United States: A Nationwide Outcomes Study. https://pubmed.ncbi.nlm.nih.gov/25397857/ |

**Citation hygiene.** These were located by targeted search, not a systematic
review. Before submission every one must be read in full and its figure verified
in context — particularly [R2], which measures intra-hospital ward-to-ICU transfer
and is used here only to establish that the time-to-care gradient is real and
steep, not to calibrate interhospital timings.

---

## 11. Reproducibility

Every run is fully determined by `(scenario_name, seed)`. All randomness flows
through seeded `random.Random` instances. The LLM is outside the decision path by
default, so no reported number depends on a model generation.

Headline figures are the mean of 5+ seeds with **paired** tests over common
random numbers (`mahros/eval/stats.py`, validated against scipy where installed).
Pairing matters: each seed produces matched runs on the same patient stream, so
the large seed-to-seed variance cancels.

The central "MAHROS ≈ centralized" claim is a null result, and a non-significant
t-test does not establish equivalence — it is equally consistent with a small
sample or a noisy metric. It is therefore reported with a **TOST equivalence
test** against a margin of 2 percentage points of transfer success rate, declared
before the analysis.

At **30 seeds** (`python experiments/significance.py 30`):

| Claim | Result |
|---|---|
| MAHROS > phone tree | +18.2 pts, p < 0.0001, dz = 3.80 |
| MAHROS ≡ centralized (greedy) | **equivalent within ±2 pts, TOST p = 0.013** |
| MAHROS ≡ batched optimal | not established, TOST p = 0.10 (no difference detected, p = 0.16) |
| Fairness layer on burden Gini | −0.073, p < 0.0001, dz = −1.13 |
| Fairness cost in success / wait | +0.05 pts (p = 0.95) / +0.43 min (p = 0.31) |
| Deliberation cost, honest network | −0.84 pts (p = 0.29) / +1.28 min (p = 0.012) |

Multiple comparisons across arms are Holm-corrected; only the phone-tree
comparison survives correction, which is the correct and expected outcome.

**A recorded correction.** At 12 seeds the fairness effect was −0.044 at p = 0.13
and was written up as a null result. At 30 seeds it is −0.073 at p < 0.0001 with
dz = 1.13 — it was under-powered, not absent. The 12-seed figure and this
correction are both retained here deliberately: a reader should be able to see
that the seed count was raised for a stated reason and what changed as a result,
rather than finding only the number that survived.

`tests/test_core.py` asserts that the same seed reproduces identical results and
that different seeds do not.


---

## 12. The adversary model

Introduced with the deliberation phase. Three hospital behaviours, all in
`mahros/hospital/behaviours.py`:

| Policy | Behaviour | Role in the evaluation |
|---|---|---|
| `honest` | Reports its assessment unchanged | Control, and the original model |
| `strategic` | Fabricates a refusal when a patient is expensive and it is already uncomfortable | The attack |
| `defensive` | Never lies, but holds a larger reserve than the network norm | **The false-positive control** |

**Why `defensive` exists.** Without it the adversarial experiment would be a
strawman: a mechanism that simply overruled every refusal would score perfectly
against `strategic` alone. `defensive` refuses more often than average and every
one of its refusals is genuine, so a mechanism that punishes it is punishing
caution rather than deceit. Measured false-accusation rate against this arm:
**zero, in every condition tested.**

**Assignment.** Strategic behaviour is assigned in descending order of tier, not
uniformly at random. The tertiary centre holding the region's scarce capability
is asked for everything, sits permanently near capacity, and gains the most from
refusing — so that is where the risk actually lies. Assigning it uniformly would
understate the threat by putting it on small hospitals nobody asks. Assignment is
also *nested* in the fraction: raising the dose adds liars rather than
reshuffling which hospitals lie, so the dose-response curve is not confounded
with identity.

**What a strategic hospital will not do.** It never fabricates a refusal for an
acuity-5 patient. This is an ethical floor in the model, asserted in
`tests/test_argumentation.py::test_nobody_games_a_crash_call`, and it makes every
measured harm figure a *lower bound* on what an unconstrained adversary could do.

**The detection asymmetry is not an assumption.** A refusal is defensible exactly
when the hospital's own truthful assessment supports it. That is not a modelling
convenience chosen to make the mechanism work — it is what "defensible" means.
The hospital is asked to show that its own state supports the claim; a fabricated
claim has no such state to show. Everything the mechanism achieves follows from
that one asymmetry, which is why the zero-false-accusation result holds by
construction rather than by tuning.
