# MAHROS

**Multi-Agent Hospital Resource Optimization System** — decentralised negotiation
between hospitals for post-admission escalation transfers, with explicit fairness
accounting, a tamper-evident audit ledger, and plain-English decision explanations.

> **Scope.** This is *not* ambulance routing. It addresses what happens after a
> patient is already admitted and stabilised, and then deteriorates into needing a
> level of care their current hospital cannot provide right now — an ICU bed, a
> specific surgeon, a cath lab. Around 3.5% of US hospital admissions (~1.5M/year)
> are interhospital transfers driven by exactly this.

**Status:** working simulation, 27 tests passing, TRL 3–4 (proof of concept
validated in a simulated setting; not deployed, no real patient data).

---

## Quick start

```bash
pip install -e .
python -m mahros.cli doctor                              # what's available on this machine
python -m mahros.cli run --scenario surge_scarcity       # one simulation
python -m mahros.cli compare --scenario surge_scarcity --seeds 5   # all strategies
python -m pytest tests -q                                # 27 tests
```

Runs on the standard library plus `typer` and `rich`. No paid services, no API
key, no network access required.

### Reproducing every number in the dashboard

```bash
python experiments/run_all.py 5          # 4 scenarios x 5 strategies x 5 seeds
python experiments/build_dashboard.py    # -> results/dashboard.html
node dashboard/check.js                  # headless render validation (needs: npm i jsdom)
```

---

## Headline results

Surge + scarcity scenario, 5 seeds, 95% CI. Full results in `results/`.

| | MAHROS | Phone tree (today) | Centralized (omniscient) |
|---|---|---|---|
| Transfer success rate | **87.0%** | 66.2% | 86.3% |
| Mean time to care | **66.5 min** | 88.0 min | 68.5 min |
| P90 time to care | **85.0 min** | 122.3 min | 88.5 min |
| Safe-window breach | **12.7%** | 33.5% | 13.4% |
| Burden Gini (lower = fairer) | 0.265 | 0.356 | 0.256 |
| Coordinator min / transfer | **1.1** | 47.3 | 0.0 |
| Decisions brokered by one node | **14.7%** | 13.6% | **100%** |
| Private state seen by one node | **14.6%** | 13.5% | **100%** |

Three findings worth stating plainly:

1. **MAHROS matches the omniscient central optimiser** (87.0% vs 86.3% success,
   66.5 vs 68.5 min) while no node ever holds the network's joint state. Closing
   that gap without centralising data is the contribution.
2. **The fairness layer improves efficiency, not just equity.** Spreading load
   avoids congesting the single best hospital, so MAHROS slightly *exceeds* the
   pure-efficiency centralized arm on success rate in the baseline scenario.
3. **Against today's practice the gap is large**: +31% relative success rate and
   a 97.8% reduction in human coordinator time per transfer.

**Calibration check.** The simulation generates transfer requests at 3.7–4.3% of
admissions, against the ~3.5% real-world interhospital transfer rate. That ratio
was not tuned — it falls out of the escalation model, and it is the main external
check available without real hospital data.

---

## Architecture

| Layer | What it does | Where |
|---|---|---|
| 1. Resource agents | One agent per resource type per hospital (ICU, HDU, ward, OR, ventilator, cath lab). Assess feasibility; price the opportunity cost of giving a unit up. | `mahros/hospital/agents.py` |
| 2. Negotiation | Contract Net Protocol: announce → bid → award → confirm. Bids collected in parallel; winner's resource reserved at award time. | `mahros/negotiation/cnp.py` |
| 3. LLM coordinator | Invoked only on contested decisions. Explains; does not decide (arbitration is an ablation flag). | `mahros/llm/coordinator.py` |
| 4. Fairness | Burden vs capacity share (Gini, Jain), patient-group equity gap. Reorders comparable options; never overrides a clinical constraint. | `mahros/fairness/metrics.py` |
| 5. Audit ledger | Merkle-linked append-only chain; optional Solidity contract for a permissioned consortium chain. | `mahros/ledger/` |
| Privacy boundary | HMAC pseudonymisation + identifier scrubbing on everything crossing a hospital boundary. Presidio used if installed. | `mahros/privacy/anonymizer.py` |

### Design decisions that matter for the paper

**The LLM is outside the decision path by default.** `CNPConfig.enable_llm_arbitration`
is `False`. The deterministic scorer picks the winner; the LLM writes the rationale
that goes on the ledger. If the LLM chose winners, no headline number would be
reproducible, and a reviewer would be right to reject the evaluation. Running it as
arbiter is available as an ablation — and finding that it does *not* beat the
deterministic rule is a perfectly good result.

**"Blockchain" is scoped honestly.** The trust model is a *permissioned consortium
ledger* among hospitals in one network or health system — not a public
permissionless chain. What is actually required is append-only, tamper-evident,
independently verifiable, and no unilateral rewriting. The default hash chain
provides all four with zero dependencies; the Solidity contract adds multi-party
consensus and two-sided attestation for deployment.

**Decentralisation is measured, not asserted.** `MessageBus.audit_no_global_view()`
computes what share of negotiations any single node brokers and what share of
private-state disclosures it receives. Centralized scores 100% on both; MAHROS
scores ~15%. This is a testable property, and `tests/test_core.py` tests it.

**The clinical safe window is a hard constraint.** A bid that cannot deliver the
patient in time is discarded before scoring, never traded off against fairness.
For acuity 4–5 the score collapses toward pure speed. Both are tested.

---

## Baselines

| Strategy | Why it's in the comparison |
|---|---|
| `phone` | What actually happens today: a coordinator ringing hospitals serially, ~9 min per call, limited coordinators, information stale by call six. The honest comparator. |
| `central` | Omniscient central authority — reads every hospital's private state, optimises globally. The efficiency **ceiling**, not a straw man. |
| `nearest` | Greedy first-fit against a shared registry. Fast, myopic, starves the nearest tertiary centre. |
| `none` | No coordination; patient waits locally. Floor condition. |

> Reading `none`'s mean wait alone is misleading — it completes the easy cases and
> abandons the rest. Always read it with its success rate.

---

## Optional layers

All fall back gracefully; none is required.

```bash
pip install -e ".[privacy]"     # Microsoft Presidio  (else: HMAC + regex)
pip install -e ".[chain]"       # web3 + local Anvil   (else: hash chain)
pip install -e ".[negotiation]" # NegMAS               (else: built-in Contract Net)
pip install -e ".[analysis]"    # numpy/pandas/matplotlib for plots
```

**LLM providers — free options only:**

```bash
# local, free, offline, no key (recommended)
ollama pull llama3.2:3b
$env:MAHROS_LLM_PROVIDER="ollama"

# or a free API tier
$env:MAHROS_LLM_PROVIDER="groq";   $env:GROQ_API_KEY="..."
$env:MAHROS_LLM_PROVIDER="gemini"; $env:GEMINI_API_KEY="..."
```

With no provider set, a deterministic template explainer is used — which is what
CI and the headline experiments run on.

---

## Layout

```
mahros/
  core/         domain types, discrete-event clock
  hospital/     resource pools, agents, hospital
  negotiation/  Contract Net, scoring, message bus
  fairness/     Gini / Jain / equity gap, burden ledger
  privacy/      anonymiser + boundary audit
  ledger/       hash chain, Solidity contract
  llm/          provider-agnostic client, coordinator
  sim/          scenarios, strategies, runner, metrics
experiments/    run_all, build_dashboard, diagnose
dashboard/      template + headless render check
tests/          27 tests
results/        generated output (git-ignored)
docs/           model assumptions
```

`experiments/diagnose.py` is the tool to reach for whenever a success rate looks
wrong — it breaks failures down by resource, acuity, and refusal reason. A
simulation that silently fails most transfers produces a beautiful, meaningless
results table.

---

## Known limitations

- **Simulated, not validated.** No real hospital data, no clinical or prospective
  validation. Arrival rates, lengths of stay, travel times, and safe windows are
  documented modelling assumptions (`docs/MODEL_ASSUMPTIONS.md`).
- **Communication cost is real.** ~41 messages per transfer vs ~5 for a phone
  tree. The saving is in human coordinator time, not bytes.
- **Results are sensitive to the assumed safe windows.** The *ranking* of
  strategies is more robust than the absolute numbers.
- **Hospitals are modelled as honest.** Strategic under-reporting of capacity to
  avoid receiving transfers is not yet modelled. The ledger deters denial after
  the fact, not misreporting before it.
- **Fairness gains are modest**, not dramatic: Gini 0.287 → 0.265 for ~0.3 min of
  added mean wait. Reported as measured.

## License

MIT. Built on open-source components; the negotiation design, fairness metric,
and evaluation are the contribution.
