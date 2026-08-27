# MAHROS

**Multi-Agent Hospital Resource Optimization System** — decentralised negotiation
between hospitals for post-admission escalation transfers, in which a hospital's
refusal can be **challenged with evidence and must be justified**.

> **Scope.** This is *not* ambulance routing. It addresses what happens after a
> patient is already admitted and stabilised, and then deteriorates into needing a
> level of care their current hospital cannot provide right now — an ICU bed, a
> specific surgeon, a cath lab. Around 3.5% of US hospital admissions (~1.5M/year)
> are interhospital transfers driven by exactly this.

**Status:** working simulation + live demo console, 79 tests passing, TRL 3–4
(proof of concept validated in a simulated setting; not deployed, no real
patient data). Structural assumptions checked against **3,186 real hospitals**.

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

All numbers below are reproducible from the scripts named beside them.

### 1. Strategic refusal is a serious threat, and argument recovers much of it

`python experiments/adversarial.py` — surge + scarcity, 6 seeds. "Strategic"
hospitals fabricate a refusal when a patient would be expensive and they are
already uncomfortable.

| Hospitals fabricating refusals | Deliberation OFF | Deliberation ON | Loss recovered |
|---|---|---|---|
| 0% | 86.9% | 86.0% | — |
| 25% | 71.7% | **76.5%** | 32% |
| 50% | 62.4% | **71.2%** | 36% |
| 75% | 57.0% | **69.1%** | 40% |
| 100% | 47.0% | **65.0%** | 45% |

Paired t-tests on the difference: significant at 50% and above
(p = 0.012, 0.008, 0.0001; dz = 1.58, 1.77, 4.43), **marginal at 25%**
(p = 0.064, 6 seeds). Reported as measured.

### 2. Zero false accusations

A third hospital type — honest but cautious, holding a larger reserve than the
norm — was **never once overruled**, in any condition tested. This is the
property that decides whether the mechanism is deployable: one that punished
prudence rather than deceit would be worse than useless. It holds by
construction, and is asserted in `tests/test_argumentation.py`.

### 3. Decentralisation costs nothing detectable — and we can say why

`python experiments/run_all.py 5` (levels) and
`python experiments/significance.py 30` (tests), surge + scarcity:

| | MAHROS | Phone tree (today) | Centralized (greedy) | Batched optimal |
|---|---|---|---|---|
| Transfer success rate | 85.5% | 66.2% | 86.3% | 85.7% |
| Mean time to care | **68.8 min** | 88.0 min | 68.5 min | 71.3 min |
| Coordinator min / transfer | **1.1** | 47.3 | 0.2 | 0.2 |
| Capacity leaked per message | **2.04 bits** | 1.34 bits | 2.83 bits | 2.59 bits |
| …as a share of full transparency | **57.8%** | 36.7% | 87.3% | 81.7% |

Paired over **30 seeds**: MAHROS − centralized = **+0.31 pts (p = 0.67)**,
MAHROS − batched optimal = **+1.05 pts (p = 0.16)**. Neither difference is
detectable.

**And equivalence is now formally established** against the greedy centralised
optimiser: TOST against a ±2-point margin declared in advance gives
**p = 0.013**, 90% CI [−0.9, +1.5] points, comfortably inside the margin. That is
a positive claim — *these two perform the same* — not merely a failure to find a
difference.

Against the **batched optimal** arm, equivalence is *not* established
(TOST p = 0.10, 90% CI [−0.2, +2.3]). No difference is detectable there either,
but the interval is a fraction too wide to close the claim, and the paper says so
rather than rounding it up. Note the point estimate runs slightly in MAHROS's
favour, so this is a limit of power, not a hidden deficit.

**Why** it comes out this way is the more interesting finding: transfer requests
almost never collide. Even batching for a full hour, the average batch contains
**1.2 patients**. Joint optimisation is genuinely more powerful, but in a
realistic escalation stream it almost never gets the chance to use that power —
while its costs, pooling everyone's data and delaying every patient by the
batching window, are paid on every single transfer.

### 4. Against today's practice, the gap is large and survives its own stress test

MAHROS − phone tree = **+18.2 points**, p < 0.0001, dz = 3.80 (30 paired seeds,
Holm-corrected, 95% CI [+16.4, +20.0]). This is the only comparison that survives
correction, and it is overwhelming.

`docs/MODEL_ASSUMPTIONS.md` flags the phone tree's staleness rate as the least
defensible number in the model, so `experiments/sensitivity.py` sweeps it:

| Staleness | Call length | Phone tree | MAHROS wins? |
|---|---|---|---|
| 0.00 | 5 min | 79.5% | yes |
| 0.00 | 9 min | 73.4% | yes |
| 0.25 | 9 min | 66.4% | yes |
| 0.40 | 9 min | 63.2% | yes |

Even granting the phone tree perfect information and short calls, MAHROS wins.
The advantage comes from asking in parallel, not from the assumed staleness.

### 5. Ablations — all of them real

| Arm | Success | Burden Gini | What it shows |
|---|---|---|---|
| full | 85.5% | 0.215 | |
| − fairness | 86.2% | 0.300 | fairness costs 0.7 pts, improves Gini by 0.085 |
| − argumentation | 87.0% | 0.265 | deliberation costs 1.5 pts when nobody lies |
| **adversarial** (50% strategic) | 69.6% | 0.258 | |
| adversarial − argumentation | 61.6% | 0.318 | **deliberation is worth +8.0 pts under attack** |
| adversarial − audit ledger | 61.0% | 0.315 | **no ledger → no evidence → mechanism collapses** |

That last row matters. In the previous version of this project the no-audit
ablation was *bit-identical* to the full system, because the ledger touched no
decision — a no-op reported as a result. Now the ledger is the evidence base a
refusal is checked against, so removing it genuinely destroys the mechanism.

### 6. The fairness layer, which now works

At 12 seeds this was a null result and was reported as one. At **30 seeds it is
unambiguous**, and the correction is worth recording rather than quietly
overwriting:

| Fairness ON − OFF | Effect | p | |
|---|---|---|---|
| Burden Gini | **−0.073** [−0.097, −0.049] | **<0.0001** | dz = −1.13 |
| Transfer success rate | +0.05 pts [−1.6, +1.7] | 0.95 | no cost |
| Mean time to care | +0.43 min [−0.43, +1.28] | 0.31 | no cost |

So the fairness layer improves load balance by a large, highly significant
margin **at no measurable cost in speed or success**. Bootstrap CI on the Gini
difference [−0.095, −0.049] excludes zero.

What changed was not the mechanism but the statistics: the earlier 12-seed run
gave −0.044 at p = 0.13, which with dz = 1.13 was simply under-powered. The
substantive improvement came earlier, from moving fairness out of the score
tie-break (effective weight 0.0375, because 77% of transfers are acuity 4–5 where
the scorer collapses toward pure speed) and into the deliberation phase as a
**burden objection** — a hospital over its share may ask not to be picked, but
only for a non-critical patient and only when a comparable alternative exists.

### 7. What still does *not* work — reported as measured

- **Deliberation costs ~0.8 points of success on a fully honest network**, which
  is not statistically significant (p = 0.29). It does cost a real
  **+1.28 minutes** of mean time to care (p = 0.012). It is insurance, and
  insurance has a premium.
- **Equivalence to the fully joint optimiser is not established** — see above.
- **MAHROS discloses more capacity information in aggregate than a phone tree**
  (15.8 vs 1.9 bits per transfer), simply because it asks more hospitals. What
  it never does is transmit an occupancy figure, which is why each message says
  far less (57.8% vs 87.3% of full transparency). Both halves are reported.
- **Only a minority of individual lies are caught.** The recovery in success rate
  is much larger than the per-lie detection rate, because overruling a hospital
  once returns it to contention for many later patients.

---

## External calibration: 3,186 real hospitals

```bash
python experiments/calibrate.py            # check against the vendored reference
python experiments/calibrate.py --refetch  # re-pull from the live HHS API
```

Reference: *COVID-19 Reported Patient Impact and Hospital Capacity by Facility*
(US HHS / CDC NHSN, public domain, facility-level, no patient data), 3,186
short-term acute-care hospitals.

| Assumption | Model | Observed | |
|---|---|---|---|
| Network inpatient occupancy | 0.70 | **0.700** | PASS |
| ICU-minus-ward occupancy gap | 0.10 | 0.103 | PASS |
| Tier-3 ICU beds per inpatient bed | 0.102 | 0.127 | PASS |
| Tier-1 / tier-2 ICU depth | 0.056 / 0.080 | 0.108 / 0.118 | NOTED — deliberately 48% / 32% ICU-scarcer |

The arrival rates were tuned to a ~70% occupancy target *before* any real data
was consulted; the observed mean is 70.0%. That is a confirmation, not a fit.
The tier-1/2 divergence is intentional (an India-style critical-care-scarce
network) and the report quantifies it rather than burying it. 27.6% of the
smallest real hospitals have **no** staffed adult ICU beds at all — the
structural reason escalation transfers exist.

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
python -m mahros.cli run --scenario surge_scarcity       # one simulation
python -m pytest tests -q                                # 79 tests
```

Standard library plus `typer` and `rich`. No paid services, no API key, no
network access required.

### Reproducing every number

```bash
python experiments/calibrate.py          # external calibration table
python experiments/run_all.py 5          # 4 scenarios x 6 strategies x 5 seeds
python experiments/adversarial.py 8      # the headline experiment
python experiments/significance.py 30    # paired tests + TOST equivalence
python experiments/sensitivity.py        # sweeps every unsourced parameter
python experiments/build_dashboard.py    # -> results/dashboard.html
node dashboard/check.js                  # headless render validation
node dashboard/check_console.js          # console <-> server contract
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
  fairness/     Gini / Jain / equity gap, burden ledger
  privacy/      anonymiser, boundary audit, capacity-leakage measurement
  ledger/       hash chain (agreements + refusals), Solidity contract
  llm/          provider-agnostic client, coordinator
  eval/         paired tests, TOST, bootstrap, Hungarian assignment
  calibration/  external validation against real hospital data
  sim/          scenarios, strategies, runner, metrics
  server/       live demo: FastAPI + WebSocket + debate console
experiments/    run_all, adversarial, significance, sensitivity, calibrate
tests/          79 tests
docs/           PANEL_GUIDE, DEMO, MODEL_ASSUMPTIONS
```

---

## Known limitations

- **Simulated, not validated.** No real patient data, no clinical or prospective
  validation. Structural capacity assumptions are calibrated against real
  facility data; behaviour and timing assumptions are not.
- **Equivalence to the *fully joint* optimiser is not formally established.**
  No difference is detectable (p = 0.16) but the TOST interval [−0.2, +2.3] is a
  fraction wider than the ±2-point margin. Equivalence to the greedy centralised
  optimiser *is* established (p = 0.013).
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
