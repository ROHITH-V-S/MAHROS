# MAHROS

**Multi-Agent Hospital Resource Optimization System** — decentralised negotiation
between hospitals for post-admission escalation transfers, in which a hospital's
refusal can be **challenged with evidence and must be justified**.

> **Scope.** This is *not* ambulance routing. It addresses what happens after a
> patient is already admitted and stabilised, and then deteriorates into needing a
> level of care their current hospital cannot provide right now — an ICU bed, a
> specific surgeon, a cath lab. Around 3.5% of US hospital admissions (~1.5M/year)
> are interhospital transfers driven by exactly this.

**Status:** working simulation + live demo console, 161 tests passing, TRL 3–4
(proof of concept validated in a simulated setting; not deployed, no real
patient data).

**Every headline result runs on 27 real hospitals.** Not a synthetic network
checked against real statistics — the actual facilities. Memorial Hermann Texas
Medical Center, Houston Methodist, MD Anderson and 24 others, each with the
inpatient bed count, staffed ICU count, coordinates and week-by-week occupancy
it reported to the US federal government. The simulation reproduces the
occupancy those hospitals actually ran at to within **3.95 occupancy points
(R² = 0.95)**.

Every parameter is classified **observed**, *derived* or _assumed_ in
**[docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md)**, which is generated from
the code rather than written by hand. For this network: **8 observed, 4 derived,
6 assumed.** What is assumed is named, and the largest gap — hospital
*capability* beyond paediatric intensive care — is stated plainly rather than
implied away.

New here? Read **[docs/PANEL_GUIDE.md](docs/PANEL_GUIDE.md)** first — the same
material in plain English, with the questions a reviewer or examiner will
actually ask, and the answers.

---

## The contribution in one paragraph

A textbook Contract Net auction assumes every participant bids honestly.
Hospitals have a standing reason not to: accepting a transfer costs a scarce bed,
and the cheapest way to avoid one is to claim you haven't got it. In an ordinary
auction nobody can check. MAHROS adds a **deliberation phase**: a refusal is
tested against a shared, tamper-evident record, contradicted refusals are
challenged, and the hospital must justify itself or have the refusal struck out.
Resolution uses Dung's grounded argumentation semantics, so the rule deciding who
wins an argument is a standard formalism rather than a bespoke heuristic.

The property that makes it work:

> **An honest refusal can always be defended. A fabricated one cannot.**

And the design principle that keeps it decentralised:

> **Capability is public. Capacity is private.**
> Whether you run a cardiac unit is in the service directory, so that claim is
> checkable by anyone at zero cost to privacy. How many beds are free is
> genuinely private and stays private — so a capacity claim is checked instead
> against *what you have already agreed to take*. Accountability without a data
> monopoly.

---

## Headline results

Every number below is from the **real 27-hospital Houston network**, reproducible
from the script named beside it. The synthetic network's results are in
`results/` and reproducible with `--synthetic`; they are no longer the headline.

### 1. Strategic refusal is a serious threat, and argument recovers most of it

`python experiments/adversarial.py 6` — `houston_surge` (Delta peak, real
occupancy), 6 seeds. "Strategic" hospitals fabricate a refusal when a patient
would be expensive and they are already uncomfortable.

| Hospitals fabricating refusals | Deliberation OFF | Deliberation ON | Loss recovered |
|---|---|---|---|
| 0% | 95.3% | 94.2% | — |
| 25% | 85.1% | **90.5%** | 53% |
| 50% | 65.5% | **83.9%** | 62% |
| 75% | 48.7% | **78.1%** | 63% |
| 100% | 32.9% | **74.6%** | 67% |

At 100% strategic the network collapses to 32.9% without deliberation and holds
at 74.6% with it. The mechanism recovers between half and two thirds of what
deceit costs, at every dose.

### 2. Zero false accusations

An honest-but-cautious hospital — holding a larger reserve than the norm — was
**never once overruled**, in any condition tested, across every experiment in
this repository including the adversarial sweep, the attestation sweep and the
collusion defence. The mechanism overrules hundreds of refusals per run and gets
none of them wrong.

This is the property that decides whether it is deployable: one that punished
prudence rather than deceit would be worse than useless. It is not luck, and it
is not purely structural either — see §7, where a proactive spot check *did*
start punishing cautious hospitals until the attestation predicate was split.

### 3. Decentralisation: equivalent to both centralised arms

`python experiments/run_all.py 5` (levels) and
`python experiments/significance.py 12` (tests), on `houston_surge`:

| | MAHROS | Phone tree (today) | Centralized (greedy) | Batched optimal | Nearest |
|---|---|---|---|---|---|
| Transfer success rate | 94.1% | 76.6% | 95.4% | 93.6% | 94.6% |
| Mean time to care | 64.3 min | 94.3 min | 63.5 min | 66.7 min | 63.3 min |
| Burden Gini | **0.295** | 0.347 | 0.328 | 0.333 | 0.334 |

Paired over **12 seeds**, Holm-corrected across four comparisons:

| Comparison | Difference | Significant? | TOST equivalence (±2 pts) |
|---|---|---|---|
| vs phone tree | **+18.2 pts** | p_adj < 0.0001 | — |
| vs centralized | **−1.03 pts** | p_adj = 0.0091 **yes** | **p = 0.0023 → EQUIVALENT** |
| vs batched optimal | +0.95 pts | p_adj = 0.0217 yes | **p = 0.0029 → EQUIVALENT** |
| vs nearest-available | +0.27 pts | p_adj = 0.34 no | — |

**Read the centralized row carefully, and state it exactly this way.** With
real capability data the omniscient centralised optimiser is now *detectably*
better than MAHROS — by one percentage point. It is also *statistically
equivalent* within the ±2-point margin declared in advance. Both are true, and
reporting only the second would be the more flattering half of a two-sided
result.

The claim that survives is therefore narrower than "no difference" and stronger
than a null: **giving up the central data monopoly costs about one point of
transfer success, and that cost is smaller than the margin we declared as
practically meaningful before looking.** Equivalence to the *batched optimal*
arm is now established too, which it was not on the synthetic network.

### 4. Against today's practice, the gap is large

**MAHROS − phone tree = +18.2 points**, p_adj < 0.0001, dz = 12.09
(12 paired seeds, 95% CI [+17.3, +19.2]).

The gap holds across every real week:

| Scenario | Week | Observed occupancy | MAHROS | Phone tree | Gap |
|---|---|---|---|---|---|
| `houston_calm` | 2020-08-23 | 0.749 | 97.4% | 86.9% | +10.5 |
| `houston_baseline` | 2021-01-10 | 0.823 | 97.3% | 83.3% | +14.0 |
| `houston_typical` | 2022-05-08 | 0.907 | 94.6% | 76.3% | +18.3 |
| `houston_surge` | 2021-07-25 | 0.951 | 94.1% | 76.6% | +17.5 |
| `houston_post_pandemic` | 2023-12-10 | 0.955 | 94.7% | 76.0% | +18.7 |

The gap widens as the network fills: +10.5 points on the calmest week, +18.7 on
the busiest. Coordination matters least when there is slack and most when there
is none.

### 5. Ablations — the ledger is load-bearing

`houston_surge`, 5 seeds. Adversarial arms run at 50% strategic.

| Arm | Success | Burden Gini | What it shows |
|---|---|---|---|
| full | 94.1% | 0.295 | |
| − fairness | 95.6% | 0.334 | fairness costs 1.5 pts, improves Gini by 0.039 |
| − argumentation | 95.4% | 0.311 | deliberation costs 1.3 pts when nobody lies |
| **adversarial** (50% strategic) | 83.7% | 0.478 | |
| adversarial − argumentation | 66.1% | 0.570 | **deliberation is worth +17.6 pts under attack** |
| adversarial − audit ledger | 66.1% | 0.558 | **no ledger → no evidence → +17.6 pts lost** |

Removing the ledger costs exactly as much as removing argumentation altogether,
which is the cleanest statement of the design: *the argument is only as good as
the evidence behind it.*

### 6. The fairness layer

12 seeds, `houston_surge`:

| Fairness ON − OFF | Effect | p | |
|---|---|---|---|
| Burden Gini | **−0.030** [−0.049, −0.011] | **0.0057** | dz = −0.99 |
| Transfer success rate | −0.59 pts [−1.21, +0.03] | 0.061 | marginal cost |
| Mean time to care | +0.66 min [−0.67, +1.99] | 0.30 | no cost |

Bootstrap CI on the Gini difference [−0.047, −0.015] excludes zero. Note the
honest change from the synthetic result: fairness now carries a **marginal
success-rate cost** (p = 0.061) rather than being free. It is just outside
significance, and is reported as what it is rather than rounded to "no cost".

### 7. What still does *not* work — reported as measured

- **Deliberation now costs a detectable 0.64 points on an honest network**
  (p = 0.040) and 1.44 minutes of mean time to care (p = 0.018). On the
  synthetic network the success cost was not detectable. It is insurance, and
  the premium is real.
- **The centralised optimiser is genuinely, if slightly, better** (−1.03 pts,
  p_adj = 0.0091). Equivalent within ±2 points, but not identical.
- **MAHROS is not distinguishable from a plain shared registry** under honest
  behaviour (+0.27 pts vs `nearest`, p_adj = 0.34). The case for MAHROS rests
  entirely on §1 and §5 — what happens when hospitals lie — not on §3.
- **Coordinated abstention still costs 12.6 points** and is not detectable from
  the public record; only proactive spot checks recover it. See §11.
- **A proactive spot check punished honest caution until it was fixed.** With a
  single attestation predicate, a hospital holding a declared reserve had to
  either certify something false or decline and be overruled. Two false
  accusations appeared in testing before the predicate was split into
  `no_unit_available` and `at_declared_reserve`. The property held afterwards,
  but it was not free.
- **Only a minority of individual lies are caught.** At 50% strategic, 19,144
  fabricated refusals become 8,559 with deliberation on, and 353 are formally
  overruled. Success recovers far more than the per-lie detection rate, because
  overruling a hospital once returns it to contention for many later patients.

### 8. Attested refusal — removing an assumption instead of relying on it

`python experiments/attestation.py 6`

The unattested protocol contains an assumption worth being uncomfortable about.
When a refusal is challenged, `StrategicPolicy.defend()` returns `False` — the
simulator simply *assumes* a lying hospital cannot defend itself. A real bed
manager would repeat the lie.

The attested protocol removes the assumption. To discharge a challenge a
hospital must **sign** the contested predicate, co-signed by a key it does not
solely control. The question stops being *"can a liar defend?"* and becomes
*"will a liar sign?"* — an institutional question with a swept parameter.

| Deterrence | Success | Signed | Declined | Referrals | False accusations |
|---|---|---|---|---|---|
| 0.00 | 66.6% | 20,378 | 97 | 60 | **0.0** |
| 0.25 | 81.3% | 13,660 | 330 | 107 | **0.0** |
| 0.50 | 82.3% | 12,596 | 430 | 94 | **0.0** |
| 0.75 | 82.8% | 11,929 | 500 | 83 | **0.0** |
| 0.90 | 84.0% | 11,075 | 523 | 66 | **0.0** |
| 1.00 | 83.9% | 10,789 | 518 | 66 | **0.0** |

Both endpoints behave exactly as the theory predicts, and both were tested:

- **Deterrence 1.0 − unattested: d = +0.0000, p = 1.0000.** At full deterrence
  the attested protocol reproduces the old assumption *precisely*. Every earlier
  result in this project was the optimistic end of this sweep all along.
- **Deterrence 0.0 − plain Contract Net: d = +0.011, p = 0.31.** When a
  signature deters nobody, the mechanism falls back to plain Contract Net, as
  it must.

Three design properties, each tested:

1. **Contest-triggered, never continuous.** A hospital is never asked for its
   capacity. Only a *challenged* refusal may be certified.
2. **Minimal disclosure — the predicate, not the quantity.** It signs
   `no_unit_available` or `at_declared_reserve`, never a bed count. A test
   asserts the only number in the signed payload is the timestamp.
3. **Locally verifiable — 17.8 µs**, no authority round trip, which is what
   keeps it inside the clinical window.

**Audit referrals are not findings of dishonesty.** Public data cannot prove a
capacity attestation false: a hospital full at T that admits at T+3 after a
discharge looks identical to one that lied. Measured against attestations that
are true by construction, the referral false-positive rate is **0.61%**.
Referrals are advisory, never overrule anyone, and exist to hand a regulator one
specific signed proposition to check against records it may already inspect —
rather than standing access to every hospital's live bed state.

Cryptography, stated plainly: true non-repudiation needs asymmetric signatures.
The layer uses **Ed25519** when `cryptography` is installed (`pip install -e
".[attest]"`) and falls back to HMAC-SHA256 otherwise. The fallback models the
protocol faithfully but does **not** provide non-repudiation, and every record
carries which backend produced it.

### 9. Real-time: measured, not asserted

`python experiments/realtime.py`

Everything else here is a batch simulation over a fortnight, which says nothing
about whether this could run in a hospital. Wall-clock time to resolve **one**
transfer request, end to end:

| Configuration | p50 | p95 | p99 |
|---|---|---|---|
| Plain Contract Net | 0.45 ms | 1.11 ms | 1.83 ms |
| + ledger-backed challenge | 0.68 ms | 2.11 ms | 2.79 ms |
| + attestation | 1.56 ms | 5.39 ms | 7.14 ms |
| Adversarial, 50% strategic | 4.50 ms | 9.64 ms | **12.31 ms** |

**Worst p99 is 12.31 ms — 81× inside a 1-second budget**, against a human
alternative that spends 68.3 *minutes* of coordinator time per transfer.

Getting there required fixing a real defect: the ledger's read path rebuilt the
entire chain on every challenge, making deliberation quadratic in run length. On
a 4-day run, enabling argumentation cost 69.5 s; after indexing writes it costs
9.0 s, with twelve tests pinning that the fast path returns byte-identical
results to the full scan.

Stated rather than buried: single process, in memory, no network, no TLS, no
persistence. This shows the *protocol* is not the bottleneck. It does not show a
deployment would be fine.

### 10. Validation against reported occupancy

| Scenario | MAE vs reported | MAE vs fitted target | R² | Facilities at ceiling |
|---|---|---|---|---|
| `houston_baseline` | **3.95 pts** | 2.31 pts | **0.953** | 15 of 27 |
| `houston_surge` | 5.5 pts | 3.4 pts | 0.85 | 19 of 27 |

The surge week fits less well, and for a stateable reason: 19 of 27 facilities
reported occupancy above the 0.95 fitting ceiling that week, so more of the
error is the deliberate ceiling gap rather than model misfit. Both figures are
printed by `python experiments/validate_occupancy.py`.

### 11. Collusion — the attack that breaks it, and the one thing that stops it

`python experiments/collusion.py 6`

The limitations section used to name collusion the most important unmodelled
gap. It was right. A cartel does not lie better — it **stops producing
evidence**. The ledger challenge looks for one hospital that refused a resource
and then accepted it; if members abstain in concert, that pair never appears and
every refusal is individually unimpeachable.

| Cartel size (of 27) | Success | Refusals overruled | Abstentions |
|---|---|---|---|
| 0 | 94.2% | 0 | 0 |
| 3 | 90.0% | 17 | 362 |
| **6** | **81.7%** | 33 | 1,272 |
| 9 | 72.8% | 32 | 2,162 |

Collusion is not free to the cartel: those abstentions are patients its members
could have taken and turned away to protect the story.

**Three defences failed before one worked, and the failures are the substance.**

| Statistic | Honest network | 6 colluding |
|---|---|---|
| Max same-request co-refusal lift | 1.475 | **1.137** |
| Max temporal follow-on lift | 1.240 | **1.061** |

Both statistics score the *colluding* network **lower** than the honest one.
Three structural reasons, none fixable by tuning: lift divides out the marginal
refusal rates, which is exactly where the cartel's signature lives; at the
occupancy these hospitals really run at every hospital refuses most of the time
and the statistic saturates; and the cartel congests its neighbours, raising
*their* refusal rates too, so **it hides inside the damage it causes**.

Attestation fails against it as well, and that failure is the diagnosis: every
check in the protocol is **reactive**, examining a refusal only once something
flagged it. Attestation fires on a challenge, and the cartel's whole design is
to never provoke one.

**What works: make a sample of the checking proactive.** Ask a random fraction
of capacity refusals to certify themselves under signature with no evidence
against them at all.

| Spot-check rate | Honest network | 6 colluding | Checks | Cartel overruled | False accusations |
|---|---|---|---|---|---|
| 0% | 94.2% | 81.7% | 0 | 33 | **0.0** |
| 5% | 94.2% | 89.0% | 1,110 | 271 | **0.0** |
| 15% | 94.2% | 90.9% | 2,587 | 340 | **0.0** |
| 30% | 94.2% | 91.7% | 4,632 | 432 | **0.0** |

The honest-network column does not move at all. Two reasons, and the second had
to be found by a test failing: striking out a refusal substitutes the hospital's
*truthful* bid, and a genuinely full hospital's truthful bid is still a refusal;
and spot checks demand certification only for **capacity** claims, because a
capability refusal is already checkable against the public directory and is
settled before a hospital's bidding policy runs, leaving no statement to sign.
An earlier version checked those too and honest hospitals were struck out for
being unable to produce a signature they never made.

**Caveat worth stating before a reviewer does.** The tier-based capability guess
this project used to run on *overstated* this attack, reporting a 30-point loss
where the real certification record gives 12.6. Capability is far more widely
distributed than hospital size suggests — 24 of 27 Houston hospitals hold a cath
lab, 21 hold neurosurgery — so a cartel of six leaves many more alternatives
than the guess implied.

---

## The network is real

```bash
python experiments/fetch_network.py --list          # the 27 hospitals
python experiments/fetch_network.py --metro houston # re-pull from the HHS API
python experiments/validate_occupancy.py            # does the sim match reality?
python experiments/build_provenance.py              # regenerate the provenance doc
```

Source: *COVID-19 Reported Patient Impact and Hospital Capacity by Facility*
(US HHS / CDC NHSN). US public domain, dataset provenance flag `official`,
facility-level operational reporting, **no patient-level data**.

Hospitals within 40 km of Texas Medical Center, short-term acute care, at least
25 inpatient beds. 27 facilities, **8,176 inpatient beds, 1,029 staffed adult
ICU beds**, and 216 weeks of reported occupancy per facility spanning
2020-03-08 to 2024-04-21.

| | |
|---|---|
| Hospital identity, name, coordinates | **observed** (real CCN, real geocoded address) |
| Inpatient beds, staffed adult ICU beds | **observed** |
| Week-by-week occupancy | **observed** |
| Inter-hospital travel time | **observed** — OSRM routed road times over OpenStreetMap |
| Cath labs (with room counts) | **observed** — CMS Provider of Services |
| Cardiac / neuro / trauma / obstetric / paediatric / burns | **observed** — CMS Provider of Services service certifications, joined on CCN |
| Tier, ward split, arrival rate, admission mix | *derived* from the above |
| Step-down beds, OR slot counts, ventilators | _assumed_ — **not reported for hospitals in any public dataset** |
| Handover overhead (12 min/journey) | _assumed_ — not reported anywhere; swept |

**Capability is now observed, and it changed the results.** It used to be
guessed from hospital size: every tier-3 facility got cardiac, neuro, trauma and
obstetrics, every tier-1 got none. The CMS certification record shows that guess
was badly wrong. Trauma is certified at only **8 of 27** hospitals and does not
track size at all — Oakbend Medical Center has it at 95 beds while CHI St Luke's
Baylor, at 474 beds, does not. Meanwhile **24 of 27** hold a cath lab and 21
hold neurosurgery, so scarce capability is far more widely distributed than size
implies. See `python experiments/fetch_capability.py --list`.

Still assumed, and checked rather than assumed to be assumed: `VNTLTR_BED_CNT`
exists in the POS layout but is populated for **0 of 27** hospitals, and
`OPRTG_ROOM_SRVC_CD` records that an operating room exists without saying how
many. Trauma *level* (I vs III) is not in the file either.

### Scenarios are weeks, not dials

A synthetic scenario is made by turning a `surge_multiplier` up. A real scenario
is a week these hospitals actually lived through, selected by observed occupancy
from weeks where at least 26 of 27 facilities reported.

| Scenario | Week | Observed inpatient occupancy |
|---|---|---|
| `houston_calm` | 2020-08-23 | 0.749 |
| `houston_baseline` | 2021-01-10 | 0.823 |
| `houston_typical` | 2022-05-08 | 0.907 |
| `houston_surge` | 2021-07-25 | 0.951 — Delta peak, all 27 reporting |
| `houston_post_pandemic` | 2023-12-10 | 0.955 |

Two things this exposed that the synthetic scenarios had wrong:

- **The busiest weeks are not the COVID waves.** Houston ran fuller in late 2023
  than in January 2021. The post-pandemic capacity crisis is the real stress case.
- **The network never sat at 0.70.** The synthetic scenarios were tuned toward
  70% occupancy. The real network ran between 0.75 and 0.96 — a materially
  tighter regime than the one previously simulated.

### Travel time is routed, not assumed

Travel time decides which bids are *feasible at all* — a bid that cannot deliver
the patient inside the clinical safe window is discarded before scoring, never
traded off. So an error here does not shade a result, it changes which hospital
wins. It used to be straight-line distance at a blended 50 km/h: two stacked
assumptions.

`python experiments/fetch_routes.py` replaces both with a routed 27×27 matrix
from OSRM over OpenStreetMap, vendored so runs stay offline.

| | min | median | mean | max |
|---|---|---|---|---|
| Routed road + handover | 12.7 | **45.1** | 45.1 | 82.5 |
| Straight line @ 50 km/h | 12.2 | 49.5 | 49.0 | 99.1 |

The detour assumption was about right — road distance is a median **1.20×** the
straight line. The *speed* assumption was not: the old model **over-estimated**
travel by 3.9 min per transfer on average, because metro inter-hospital trips run
mostly on freeways. Every strategy uses the same matrix, so comparisons are
unaffected; absolute times shift.

Still assumed: free-flow routing (no live traffic, no time of day, no
blue-light privilege) and the 12-minute handover overhead.

### Validation: does the simulation reproduce reality?

The old calibration compared four aggregate ratios against summary statistics,
*after* the arrival rates had been tuned to hit them. It could not fail in an
interesting way. This one can, and did — three times, each failure fixing a real
modelling error:

| | MAE vs reported occupancy | R² |
|---|---|---|
| Global admission mix, naive Little's Law | 11.5 pts | 0.33 |
| + per-hospital admission mix | 7.4 pts | 0.82 |
| + Erlang-B blocking correction | 4.2 pts | 0.94 |
| + observed capability (CMS POS) | **3.95 pts** | **0.953** |

The first failure was diagnostic: per-facility error ranked almost perfectly
with the facility's ICU share. A global mix sending 90% of admissions to ward
beds starved the ICU and flooded the ward at hospitals reporting 40%+ of their
beds as ICU. The fix — fitting the admission mix per hospital, per bed pool —
is a genuine model correction that the synthetic network could never have
surfaced, because every synthetic hospital had the same bed composition.

15 of 27 facilities report weekly-average occupancy above 0.95. A stationary
loss system cannot sit at 100%, so those are fitted to a declared 0.95 ceiling
and the residual gap is reported rather than tuned away: **2.6 points against
the fitted target, 4.2 points against what the hospitals actually reported.**
Both numbers are printed.

### The synthetic network is still here

`baseline`, `surge`, `scarcity`, `surge_scarcity` are unchanged and
bit-identical — they are what the unit tests run on, and the ICU-scarcity
counterfactual is a deliberate departure from any observed network.
`houston_icu_scarce` now runs that counterfactual on **real** topology: real
hospitals, real geography, capacity tightened to a critical-care-scarce regime.
It is labelled a what-if wherever it appears.

The older summary-statistics calibration against 3,186 national facilities also
still runs (`python experiments/calibrate.py`). It is a supporting check on
structural ratios, not the validation — that distinction is the one this
project previously got wrong.

---

## Live demo console

```bash
pip install -e ".[demo]"
python -m mahros.server            # http://127.0.0.1:8000
```

Drives the **real** engine — the same `Hospital`, `ContractNetNegotiator`,
`ArgumentationFramework` and `HashChainLedger` objects the experiments use.
Nothing is pre-recorded or reimplemented in JS.

The console shows the negotiation as a **conversation**: the call going out, each
hospital answering in its own words, any doubtful refusal challenged with its
evidence, the hospital defending itself or failing to, and an argument map of
what survived.

**The three-minute demo** (scripted in [docs/DEMO.md](docs/DEMO.md)):

1. Run a **cath lab / cardiac / acuity 5** case — most hospitals refuse for lack
   of *capability*, which is why transfers exist at all.
2. Under **Are they telling the truth?**, switch a tertiary hospital to
   **protective**. Run the same case. It refuses.
3. Watch the challenge appear with its evidence card, and the refusal struck out.
4. Turn **off** "Check refusals against the shared record" and run it again — the
   same lie now succeeds. That is the paper's ablation, live.
5. Press **Fake a record** — the ledger check fails instantly.

Keep an eye on the **"0 wrongly accused"** counter throughout.

> A protective hospital only lies when it genuinely has a bed. If the network is
> very full its refusals are simply true — turn the busyness slider down to see
> one caught.

---

## Quick start

```bash
pip install -e .
python -m mahros.cli doctor                              # what's available here
python -m mahros.cli run --scenario houston_surge        # one simulation, real hospitals
python -m pytest tests -q                                # 110 tests
```

Standard library plus `typer` and `rich`. No paid services, no API key. The
real-hospital data is vendored, so **no network access is required** — it is
only needed to re-pull the facility data from HHS.

### Reproducing every number

```bash
python experiments/fetch_network.py --list   # the 27 real hospitals
python experiments/fetch_routes.py --compare # road vs straight-line travel
python experiments/fetch_capability.py --list # who can treat what (CMS POS)
python experiments/validate_occupancy.py     # simulated vs reported occupancy
python experiments/build_provenance.py       # -> docs/DATA_PROVENANCE.md
python experiments/run_all.py 5              # 5 real scenarios x 6 strategies
python experiments/adversarial.py 8          # the headline experiment
python experiments/significance.py 30        # paired tests + TOST equivalence
python experiments/sensitivity.py            # sweeps every unsourced parameter
python experiments/attestation.py 6          # signed refusals, deterrence swept
python experiments/collusion.py 6            # cartels, and what stops them
python experiments/realtime.py               # decision latency percentiles
python experiments/calibrate.py              # national summary-stat check
python experiments/build_dashboard.py        # -> results/dashboard.html
node dashboard/check.js                      # headless render validation
node dashboard/check_console.js              # console <-> server contract
```

Every experiment takes a scenario name, so the synthetic arm stays one argument
away:

```bash
python experiments/adversarial.py 8 surge_scarcity   # synthetic counterfactual
python experiments/run_all.py 5 --synthetic          # the old 4-scenario ladder
python experiments/fetch_network.py --metro houston  # re-pull from the HHS API
```

---

## Architecture

| Layer | What it does | Where |
|---|---|---|
| 1. Resource agents | One agent per resource type per hospital. Assess feasibility; price the private opportunity cost of giving a unit up. | `mahros/hospital/agents.py` |
| 1b. Behaviour policies | What a hospital *says*, which is not always what is true: honest / strategic / defensive. | `mahros/hospital/behaviours.py` |
| 2. Negotiation | Contract Net: announce → bid → **deliberate** → award → confirm. | `mahros/negotiation/cnp.py` |
| 2b. Argumentation | Dung framework + grounded semantics. Decides which claims survive. | `mahros/negotiation/argumentation.py` |
| 2c. Challenge engine | Turns the public directory and the ledger into evidence. | `mahros/negotiation/challenge.py` |
| 3. LLM coordinator | Invoked only on contested decisions. Explains; does not decide. | `mahros/llm/coordinator.py` |
| 4. Fairness | Burden vs capacity share (Gini, Jain), patient-group equity gap. | `mahros/fairness/metrics.py` |
| 5. Audit ledger | Merkle-linked append-only chain of agreements **and refusals**. | `mahros/ledger/` |
| Evaluation | Paired tests, TOST, bootstrap, Holm, Hungarian assignment. | `mahros/eval/` |
| Calibration | Checks the model against real facility-level hospital data. | `mahros/calibration/` |
| Privacy | Pseudonymisation, boundary audit, and bit-level capacity-leakage measurement. | `mahros/privacy/` |

### Design decisions that matter for the paper

**The LLM is outside the decision path by default.** The deterministic scorer
picks the winner; the LLM writes the rationale that goes on the ledger. If the
LLM chose winners, no headline number would be reproducible.

**The ledger is load-bearing, not decorative.** It used to be write-only, with a
tamper demo disconnected from the protocol. It is now the evidence base a refusal
is checked against — which is why the no-audit ablation finally means something
(69.6% → 61.0% under attack).

**Privacy is measured in bits, not in message counts.** The old decentralisation
metric counted what share of capacity messages any one node received — which is
roughly 1/n by construction, and scored a phone tree the same as MAHROS.
`mahros/privacy/leakage.py` instead measures how much each message narrows an
observer's belief about the sender's occupancy. It also surfaced an
instrumentation bug: the baselines were not logging their own answers, which had
been flattering them.

**"Blockchain" is scoped honestly.** A *permissioned consortium ledger* among
hospitals in one network — not a public permissionless chain. Append-only,
tamper-evident, independently verifiable, no unilateral rewriting.

**The clinical safe window is a hard constraint.** A bid that cannot deliver the
patient in time is discarded before scoring, never traded off. Acuity 5 is never
refused strategically, and burden objections are barred for critical patients and
whenever no comparable alternative exists. All tested.

---

## Baselines

| Strategy | Why it's in the comparison |
|---|---|
| `phone` | What happens today: a coordinator ringing hospitals serially, ~9 min per call, limited coordinators, information stale by call six. |
| `central` | Omniscient but **greedy** — full private state, one request at a time. |
| `optimal` | Omniscient **and joint** — batches requests and solves a minimum-cost assignment (Hungarian). The true efficiency ceiling. |
| `nearest` | Greedy first-fit against a shared registry. |
| `none` | No coordination; patient waits locally. Floor condition. |

> `central` alone is not a ceiling — it uses MAHROS's own scoring function with
> the fairness term removed, so matching it is close to circular. `optimal`
> exists because a reviewer will make exactly that objection.

---

## Optional layers

```bash
pip install -e ".[privacy]"     # Microsoft Presidio  (else: HMAC + regex)
pip install -e ".[chain]"       # web3 + local Anvil   (else: hash chain)
pip install -e ".[analysis]"    # numpy/pandas/matplotlib for plots
pip install -e ".[verify]"      # scipy, to cross-check mahros.eval
```

**LLM providers — free options only:**

```bash
ollama pull llama3.2:3b ; $env:MAHROS_LLM_PROVIDER="ollama"     # local, free
$env:MAHROS_LLM_PROVIDER="groq";   $env:GROQ_API_KEY="..."       # free tier
$env:MAHROS_LLM_PROVIDER="gemini"; $env:GEMINI_API_KEY="..."     # free tier
```

With no provider set, a deterministic template explainer is used — which is what
CI and the headline experiments run on.

---

## Layout

```
mahros/
  core/         domain types, discrete-event clock
  hospital/     resource pools, agents, behaviour policies, hospital
  negotiation/  Contract Net, argumentation, challenge engine, scoring, bus
                attestation.py -> co-signed, minimal-disclosure refusals
                collusion.py   -> cartel attack + co-refusal detection
  fairness/     Gini / Jain / equity gap, burden ledger
  privacy/      anonymiser, boundary audit, capacity-leakage measurement
  ledger/       hash chain (agreements + refusals), Solidity contract
  llm/          provider-agnostic client, coordinator
  eval/         paired tests, TOST, bootstrap, Hungarian assignment
  calibration/  facilities.py  -> fetch/vendor REAL facility records
                capability.py  -> CMS Provider of Services capability join
                routing.py     -> OSRM road travel times
                hhs.py         -> national summary-stat check
                data/          -> 27 vendored Houston facilities, 216 weeks
  sim/          real_network.py -> real facilities  -> HospitalConfig, with
                                   per-field observed/derived/assumed provenance
                scenario.py     -> real + synthetic network generation
                strategies, runner, metrics
  server/       live demo: FastAPI + WebSocket + debate console
experiments/    fetch_network, fetch_routes, fetch_capability,
                validate_occupancy,
                build_provenance, run_all, adversarial, significance,
                sensitivity, calibrate, attestation, collusion, realtime
tests/          161 tests (real_network, ledger_index, attestation,
                collusion)
docs/           DATA_PROVENANCE (generated), PANEL_GUIDE, DEMO,
                MODEL_ASSUMPTIONS
```

---

## Known limitations

- **Real capacity, simulated behaviour.** The hospitals, their beds, their
  geography and their load are real and observed. *Nothing about how they
  negotiate is.* No dataset records whether a hospital refused a transfer, let
  alone whether it refused honestly, so the strategic-refusal model is a
  declared assumption whose parameters are swept. This is the single most
  important thing to be clear about: the substrate is real, the mechanism under
  study is simulated. No real patient data, no clinical or prospective
  validation.
- **Capability is assumed, not observed.** HHS reports beds, ICU beds and
  occupancy. It reports no operating rooms, ventilators, step-down beds or cath
  labs, and no specialty except paediatric intensive care. Since capability is
  precisely what makes an escalation transfer necessary, this is the largest
  remaining gap in the data. Closing it means joining CMS Care Compare on the
  CCN, which every facility record already carries.
- **The facility data is self-reported and COVID-era.** Hospitals reported to
  HHS via TeleTracking or their state health department; not independently
  audited. Collection ended 2024-05-03 and has no facility-level successor, so
  the evaluation window is 2020–2024. VA, Indian Health Service, DoD,
  psychiatric and rehabilitation facilities are excluded by dataset design — a
  real Houston transfer network includes VA hospitals; this one cannot.
- **Length of stay is scaled, not measured.** Scaled to a typical US acute
  figure (~4.6 days) so that fitted arrival rates imply realistic throughput.
  Calibrating it against HCUP or MIMIC-IV is follow-on work.
- **Hospitals start empty.** Every run warms up from zero occupancy, which
  inflates absolute success rates for early transfers. It affects all strategies
  identically, so comparisons hold, but the absolute numbers are optimistic.
  Warm-starting each hospital at its observed occupancy would remove this, and
  the data to do it is already vendored.
- **Travel times are straight-line**, from real coordinates plus a fixed 12-min
  handover overhead — not road-network routing.
- **Two facilities were excluded by judgement**, not by a mechanical rule, as
  multi-site system aggregates. Both are named with reasons in
  [docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md) and can be re-included.
- **Equivalence to the *fully joint* optimiser is not established** (TOST
  p = 0.27) — but on the real network that is because the two arms genuinely
  differ, in MAHROS's favour (+1.72 pts, p_adj = 0.0066), not because the test
  lacks power. Equivalence to the greedy centralised optimiser *is* established
  (TOST p = 0.0041).
- **MAHROS is not distinguishable from a plain shared registry** under honest
  behaviour (+0.68 pts vs `nearest`, p_adj = 0.24). The contribution is what
  happens under deceit, not coordination per se.
- **The adversary model is a declared assumption.** Both of its parameters are
  swept, and its strength was never tuned to flatter the defence.
- **Collusion is not modelled.** Two hospitals coordinating their refusals, or
  corroborating each other's false defences, would defeat the current challenge
  mechanism. This is now the most important gap.
- **No reputation term.** A hospital caught fabricating a refusal is overruled
  for that patient and nothing more.
- **Communication cost is real.** ~41 messages per transfer vs ~5 for a phone
  tree. The saving is in human coordinator time, not bytes.

## License

MIT. Built on open-source components; the negotiation protocol, the challenge
mechanism, the fairness accounting, the leakage measure, and the evaluation are
the contribution.
