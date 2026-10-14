# Results

> **Generated** by `python experiments/build_results.py` from `results/*.json`. Do not edit by hand; re-run the experiments and this script.

Network: 27 real Houston hospitals (headline) and 30 real Phoenix hospitals (replication). Behaviour is simulated. Every run starts from the occupancy the hospitals reported that week, uses the real California 2021 transfer case-mix, and is reproducible from `(scenario, seed)`.

## 1. Does the simulation reproduce real occupancy?

Cold start (empty network), so this tests where the fitted model goes, not where it was put. Phoenix was fitted by the same code with nothing tuned.

| Scenario | Hospitals | Error vs reported | R² | Error vs fitted target | Reported above 0.95 ceiling | Verdict |
|---|---|---|---|---|---|---|
| `houston_baseline` (2021-01-10) | 27 | 3.83 pts | 0.954 | 2.17 pts | 15 | GOOD |
| `houston_surge` (2021-07-25) | 27 | 5.64 pts | 0.813 | 3.54 pts | 19 | FAIR |
| `phoenix_baseline` (2021-01-10) | 30 | 3.03 pts | 0.966 | 2.36 pts | 4 | GOOD |

## 2. How reliable are the hospitals' own capacity reports?

The Data Integrity Agent's rules over every weekly report the hospitals sent to HHS. **Red** = cannot be right as reported. **Amber** = looks copied or stale. A flag means unreliable, not dishonest.

| City | Reports | Flagged | Red | Hospitals with a red |
|---|---|---|---|---|
| Houston | 5,673 | 306 (5.4%) | 223 (3.9%) | 20 of 27 |
| Phoenix | 6,169 | 10 (0.2%) | 5 (0.1%) | 3 of 30 |

| Rule | Houston | Phoenix |
|---|---|---|
| bed count collapse | 71 | 1 |
| frozen count | 66 | 3 |
| icu beds exceed inpatient beds | 1 | 0 |
| icu patients exceed icu beds | 1 | 0 |
| one week spike | 17 | 2 |
| patients exceed beds | 1 | 4 |
| zero patients in staffed hospital | 149 | 0 |

Reliability varies more than tenfold between the two regions, so neither rate is a national estimate.

## 3. Coordination on an honest network, Houston

*Not built yet: run `python experiments/run_all.py 5`.*


## 4. What may be claimed (paired tests)

`houston_surge`, 12 paired seeds, Holm-corrected across four comparisons; equivalence margin ±2 points, declared before the analysis.

| MAHROS minus | Difference | 95% CI | Holm-adjusted | Equivalent (TOST)? |
|---|---|---|---|---|
| Phone tree | +13.0 pts | [+11.9, +14.1] | p < 0.0001 | — |
| Central greedy | -0.3 pts | [-1.0, +0.4] | p = 0.7438 | **yes**, p = 0.0001 |
| Batched optimal | +0.4 pts | [-0.2, +1.1] | p = 0.4965 | **yes**, p = 0.0001 |
| Shared registry | -0.0 pts | [-0.6, +0.6] | p = 0.8980 | — |

**Fairness layer** (on minus off): burden Gini -0.034 (p = 0.0031); success +0.1 pts (p = 0.7926); time to care +1.2 min (p = 0.0791).

**Cost of checking refusals on an honest network**: success -0.7 pts (p = 0.1469); time to care +2.0 min (p = 0.0129).

## 5. When hospitals refuse falsely

`houston_surge`, 6 seeds. Protective hospitals say "full" for patients they do not want (never for acuity 5). Checking ON is the unattested protocol, which assumes a liar cannot defend itself; see section 7 for the version without that assumption.

| Protective | Checking OFF | Checking ON | Share of the loss recovered | False refusals | Overruled | False accusations |
|---|---|---|---|---|---|---|
| 0% | 96.4% | 95.5% | — | 0 | 0.0 | 0.0 |
| 25% | 83.8% | 91.6% | 62% | 3657 | 342.0 | 0.0 |
| 50% | 63.7% | 86.5% | 70% | 7898 | 520.8 | 0.0 |
| 75% | 46.8% | 83.1% | 73% | 10700 | 648.2 | 0.0 |
| 100% | 26.6% | 80.5% | 77% | 14864 | 784.5 | 0.0 |

Safety check (cautious hospitals refuse more, but truthfully):

| Network | Challenges | Overruled | False accusations |
|---|---|---|---|
| all honest | 4272 | 0.0 | 0.0 |
| 50% defensive | 9915 | 0.0 | 0.0 |
| 25% strategic + 25% defensive | 9888 | 413.2 | 0.0 |

## 6. Why not a shared registry? The same lies, both methods

*Not built yet: run `python experiments/registry_attack.py 6`.*


## 7. Signed refusals: removing the "liars can't defend" assumption

`houston_surge`, 50% protective, 6 seeds; signatures: ed25519. Deterrence = probability a lying hospital declines to sign. Nothing measures it, so the whole range is the result.

| Arm | Success | False accusations |
|---|---|---|
| No checking (plain Contract Net) | 63.7% | 0.0 |
| Unattested (liar assumed unable to defend) | 86.5% | 0.0 |
| Signed, deterrence 0.00 | 62.3% | 0.0 |
| Signed, deterrence 0.25 | 82.0% | 0.0 |
| Signed, deterrence 0.50 | 85.6% | 0.0 |
| Signed, deterrence 0.75 | 86.6% | 0.0 |
| Signed, deterrence 0.90 | 86.7% | 0.0 |
| Signed, deterrence 1.00 | 86.5% | 0.0 |

Endpoints: deterrence 1.0 vs unattested +0.0 pts (p = 1.0000); deterrence 0.0 vs no checking -1.4 pts (p = 0.2252). Audit-referral false-positive rate: 1.10%.

## 8. Collusion, and the checks that catch it

A cartel abstains together, so no single refusal is ever contradicted.

| Cartel size | Success |
|---|---|
| 0 | 95.5% |
| 3 | 89.1% |
| 6 | 79.2% |
| 9 | 70.5% |

Random spot checks (signed protocol):

| Spot-check rate | Honest network | 6 colluding | Checks | Cartel overruled | False accusations |
|---|---|---|---|---|---|
| 0% | 95.5% | 79.2% | 0 | 61.0 | 0.0 |
| 5% | 95.5% | 88.6% | 1523 | 345.7 | 0.0 |
| 15% | 95.5% | 90.8% | 3589 | 453.0 | 0.0 |
| 30% | 95.5% | 91.4% | 6489 | 544.2 | 0.0 |

Risk-based spot checks (hospitals already caught are checked more often):

| Arm | Honest network | 6 colluding | Checks spent | Cartel overruled | False accusations |
|---|---|---|---|---|---|
| no spot checks | 95.5% | 80.1% | 0 | 65.7 | 0.0 |
| uniform 5% | 95.5% | 88.6% | 1523 | 345.7 | 0.0 |
| uniform 10% | 95.5% | 89.9% | 2571 | 414.8 | 0.0 |
| uniform 15% | 95.5% | 90.8% | 3589 | 453.0 | 0.0 |
| 5%, 50% once caught | 95.5% | 91.3% | 3976 | 616.3 | 0.0 |
| 5%, 100% once caught | 95.5% | 91.3% | 7010 | 738.2 | 0.0 |

- **5%, 50% once caught** vs uniform 15%: +0.5 pts (p = 0.6581), using 111% of the checks.
- **5%, 100% once caught** vs uniform 15%: +0.5 pts (p = 0.5882), using 195% of the checks.

## 9. Replication: Phoenix, 30 real hospitals, nothing re-tuned

*Not built yet: run `python experiments/replication.py 5`.*


## 10. Sensitivity: does the ranking survive every unsourced value?

*Not built yet: run `python experiments/sensitivity.py 5`.*


## 11. Decision time for one transfer (the protocol, not a deployment)

*Not built yet: run `python experiments/realtime.py`.*

