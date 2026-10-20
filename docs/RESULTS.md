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

Transfer success rate, 5 seeds per cell. The phone tree has perfect information (no stale answers).

| Week | MAHROS | Phone tree | Central greedy | Batched optimal | Shared registry | No transfers |
|---|---|---|---|---|---|---|
| `houston_calm` | 98.4% | 93.8% | 98.8% | 98.5% | 98.4% | 42.4% |
| `houston_baseline` | 97.8% | 89.4% | 98.0% | 97.2% | 97.1% | 45.7% |
| `houston_typical` | 95.1% | 82.8% | 95.6% | 94.6% | 95.6% | 44.2% |
| `houston_surge` | 95.7% | 82.7% | 95.8% | 95.1% | 95.7% | 47.8% |
| `houston_post_pandemic` | 95.5% | 82.9% | 95.9% | 95.0% | 95.6% | 50.5% |

Surge week, other measures:

| | MAHROS | Phone tree | Central greedy | Batched optimal | Shared registry |
|---|---|---|---|---|---|
| Mean time to care (min) | 65.3 | 87.2 | 60.4 | 65.1 | 64.0 |
| Coordinator minutes per transfer | 1.1 | 50.3 | 0.0 | 0.0 | 0.0 |
| Burden Gini (0 = even) | 0.274 | 0.317 | 0.277 | 0.301 | 0.302 |

### Ablation (`houston_surge`)

| Arm | Success | Burden Gini | Mean time to care |
|---|---|---|---|
| full | 95.7% | 0.274 | 65.3 min |
| no fairness | 95.5% | 0.310 | 62.3 min |
| no argumentation | 96.3% | 0.300 | 61.9 min |
| adversarial full | 86.2% | 0.462 | 82.1 min |
| adversarial no argumentation | 64.5% | 0.585 | 82.5 min |
| adversarial no audit | 62.4% | 0.586 | 85.4 min |

The adversarial arms have 50% protective hospitals. Removing the ledger removes the evidence a refusal is checked against, which is why it costs as much as removing the checking itself.


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

| Network | Protective | Registry | MAHROS | MAHROS minus registry |
|---|---|---|---|---|
| `houston_surge` | 0% | 95.6% | 95.5% | -0.1 pts (p = 0.7567) |
| `houston_surge` | 25% | 88.0% | 91.6% | +3.7 pts (p = 0.0156) |
| `houston_surge` | 50% | 68.6% | 86.5% | +17.9 pts (p = 0.0002) |
| `phoenix_surge` | 0% | 96.8% | 96.9% | +0.1 pts (p = 0.8392) |
| `phoenix_surge` | 25% | 93.2% | 94.9% | +1.8 pts (p = 0.0991) |
| `phoenix_surge` | 50% | 83.5% | 92.2% | +8.7 pts (p = 0.0016) |

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

| Week | MAHROS | Phone tree | Shared registry | Central greedy | MAHROS minus phone |
|---|---|---|---|---|---|
| `phoenix_calm` (2021-03-14) | 98.8% | 88.6% | 98.9% | 98.8% | +10.2 pts (p < 0.0001) |
| `phoenix_baseline` (2021-01-10) | 98.7% | 88.8% | 98.3% | 98.4% | +9.9 pts (p < 0.0001) |
| `phoenix_typical` (2022-07-17) | 98.6% | 85.9% | 98.2% | 97.9% | +12.6 pts (p < 0.0001) |
| `phoenix_surge` (2024-01-14) | 96.9% | 82.6% | 96.7% | 97.1% | +14.3 pts (p < 0.0001) |

| Phoenix surge week, 50% protective | Success | False accusations |
|---|---|---|
| honest network | 96.9% | 0.0 |
| 50% protective, checking OFF | 79.7% | 0.0 |
| 50% protective, checking ON | 92.5% | 0.0 |
| 50% protective, signed refusals | 91.6% | 0.0 |

Checking ON minus OFF: +12.8 pts (p = 0.0019); recovers 75% of the loss.

## 10. Sensitivity: does the ranking survive every unsourced value?

**Rankings that did not survive** (these claims are demoted):
- argumentation harmful at threshold=0.3, shirk=0.5
- argumentation harmful at threshold=0.55, shirk=0.5
- argumentation harmful at threshold=0.8, shirk=0.5

<details><summary>safe window scale</summary>

```
{
 "0.70": {
  "mahros": 0.8628185824307554,
  "phone": 0.6680395496278371,
  "central": 0.86541100268625
 },
 "0.85": {
  "mahros": 0.9374634176836683,
  "phone": 0.7779493235617801,
  "central": 0.9290815611323611
 },
 "1.00": {
  "mahros": 0.9564978246037192,
  "phone": 0.8268227438766009,
  "central": 0.9577634785780063
 },
 "1.25": {
  "mahros": 0.9717858426026351,
  "phone": 0.8708498767501165,
  "central": 0.974608639809001
 },
 "1.50": {
  "mahros": 0.9754148161241603,
  "phone": 0.9062765670366597,
  "central": 0.9755152917636896
 }
}
```
</details>

<details><summary>phone staleness</summary>

```
{
 "0.00": 0.8268227438766009,
 "0.05": 0.8244445336984813,
 "0.10": 0.8101330282176455,
 "0.25": 0.7821001391622169,
 "0.40": 0.7348879455569888
}
```
</details>

<details><summary>call duration</summary>

```
{
 "5": 0.8645523591838469,
 "7": 0.8462684863874237,
 "9": 0.8268227438766009,
 "12": 0.7941034267553084,
 "15": 0.7703138444915074
}
```
</details>

<details><summary>agent coordination cost</summary>

```
{
 "0.25": {
  "success": 0.9563636363636364,
  "coordinator_minutes": 0.9681818181818181
 },
 "0.50": {
  "success": 0.9563636363636364,
  "coordinator_minutes": 0.9681818181818181
 },
 "1.00": {
  "success": 0.9563636363636364,
  "coordinator_minutes": 0.9681818181818181
 },
 "2.00": {
  "success": 0.9563636363636364,
  "coordinator_minutes": 0.9681818181818181
 }
}
```
</details>

<details><summary>deliberation cost</summary>

```
{
 "0.00": {
  "honest": 0.9564978246037192,
  "adversarial": 0.8624009974081224
 },
 "0.75": {
  "honest": 0.9564978246037192,
  "adversarial": 0.8624009974081224
 },
 "2.00": {
  "honest": 0.9564978246037192,
  "adversarial": 0.8624009974081224
 },
 "5.00": {
  "honest": 0.9564978246037192,
  "adversarial": 0.8624009974081224
 }
}
```
</details>

<details><summary>adversary strength</summary>

```
{
 "t0.3_p0.5": {
  "off": 0.9566850558282919,
  "on": 0.9566308825303942
 },
 "t0.3_p1.0": {
  "off": 0.6448972766505425,
  "on": 0.8624331077726344
 },
 "t0.55_p0.5": {
  "off": 0.9589399801609858,
  "on": 0.9552046983620969
 },
 "t0.55_p1.0": {
  "off": 0.6448972766505425,
  "on": 0.8624009974081224
 },
 "t0.8_p0.5": {
  "off": 0.9585612353422931,
  "on": 0.9581316491403065
 },
 "t0.8_p1.0": {
  "off": 0.6448972766505425,
  "on": 0.8624009974081224
 }
}
```
</details>

<details><summary>escalation delay</summary>

```
{
 "240": {
  "mahros": 0.9547181506810137,
  "phone": 0.8172543739163668
 },
 "420": {
  "mahros": 0.9564978246037192,
  "phone": 0.8268227438766009
 },
 "720": {
  "mahros": 0.9577873772140839,
  "phone": 0.8379426560529979
 }
}
```
</details>

<details><summary>handover minutes</summary>

```
{
 "8": {
  "mahros": 0.9699712645167038,
  "phone": 0.8363541681216404
 },
 "12": {
  "mahros": 0.9564978246037192,
  "phone": 0.8268227438766009
 },
 "20": {
  "mahros": 0.925892734811742,
  "phone": 0.7948534491850738
 }
}
```
</details>

<details><summary>escalation prob</summary>

```
{
 "0.07": {
  "mahros": 0.984625410019653,
  "phone": 0.8970217807546013
 },
 "0.14": {
  "mahros": 0.9564978246037192,
  "phone": 0.8268227438766009
 },
 "0.21": {
  "mahros": 0.8931103611606568,
  "phone": 0.7474822315231842
 }
}
```
</details>

<details><summary>cardiac cath share</summary>

```
{
 "0.25": {
  "mahros": 0.9370806155009441,
  "phone": 0.8040949694053937
 },
 "0.5": {
  "mahros": 0.9564978246037192,
  "phone": 0.8268227438766009
 },
 "0.75": {
  "mahros": 0.9663974964052064,
  "phone": 0.8419691622000851
 }
}
```
</details>


**In words:** MAHROS beat the phone tree under every value of every unsourced parameter. Checking refusals adds 21.8 points when protective hospitals lie consistently; when they lie only half the time a second round of asking already recovers almost everything, and checking costs up to 0.4 points.

## 11. Decision time for one transfer (the protocol, not a deployment)

| Configuration | p50 | p95 | p99 |
|---|---|---|---|
| plain contract net | 0.62 ms | 2.04 ms | 3.31 ms |
| + ledger-backed challenge | 3.55 ms | 12.52 ms | 17.25 ms |
| + attestation (deterrence .8) | 4.26 ms | 15.53 ms | 25.80 ms |
| adversarial, 50% strategic | 13.90 ms | 31.14 ms | 42.51 ms |

Single process, in memory, no network: shows the protocol is not the bottleneck, not that a deployment would be fast.
