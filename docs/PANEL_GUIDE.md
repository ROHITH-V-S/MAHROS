# Explaining MAHROS

Everything below is in plain language. If you can say these things in your own
words, you can defend the project. Nothing here requires you to remember a
formula.

---

## 1. The one-paragraph version

> When a patient is already in hospital and suddenly needs care that hospital
> cannot give — an ICU bed, a cardiac cath lab, a neurosurgeon — someone has to
> find them a bed somewhere else. Today that is a human being on a telephone,
> calling hospitals one at a time. MAHROS replaces those calls with software
> agents, one per hospital, that all get asked at once and each answer for
> themselves. The new part is what happens when a hospital says no: the system
> checks that "no" against a shared, tamper-proof record of what that hospital
> has already agreed to, and if the record contradicts it, the hospital is asked
> to justify itself. A hospital that was telling the truth justifies it in one
> step. A hospital that was not, cannot.

## 2. The problem, in three sentences

1. About 3.5% of hospital admissions end up being transferred to another
   hospital because the first one cannot provide the level of care needed.
2. Finding the receiving hospital is done by phone, serially, and the published
   evidence says the delay comes from **organisation, not distance** — in 49 UK
   hospitals the median delay was 22 hours, and it barely correlated with how
   far the patient travelled.
3. Delay kills: each extra hour before ICU care carries roughly a 3% increase in
   the odds of death.

So this is a coordination problem, and coordination problems are what multi-agent
systems are for.

## 3. What is actually new here

Say this exactly:

> A standard Contract Net auction assumes everybody bids honestly. Hospitals have
> a clear reason not to: accepting a transfer costs you a bed you may need. The
> cheapest way to avoid one is to say "sorry, we're full", and in an ordinary
> auction nobody can check. **Our contribution is a negotiation protocol where a
> refusal can be challenged with evidence and has to be justified — and we show
> it recovers a large part of the damage that dishonest refusals cause.**

If someone asks "isn't Contract Net from 1980?" — yes, and say so. Contract Net
is the *starting point*, not the contribution. The contribution is the
deliberation phase on top of it.

## 4. How it works — six steps

| Step | What happens | Why it matters |
|---|---|---|
| 1 | The hospital with the patient asks **every** nearby hospital at once | A phone call can only ask one at a time |
| 2 | Each hospital checks **its own** beds privately and answers yes or no | No central computer holds everyone's data |
| 3 | Any "no" is checked against the shared record | This is the new part |
| 4 | A challenged hospital must justify its refusal, or the refusal is struck out | Honest refusals survive; fabricated ones do not |
| 5 | The best remaining offer wins on speed, specialty fit, and fair share | Deterministic and reproducible |
| 6 | The agreement, the reasoning, **and every refusal** are sealed into a chain | So it can be checked later, and so refusals are commitments |

### The two kinds of evidence — the key idea

This is the sentence to have ready, because it is the cleverest part:

> **Capability is public. Capacity is private.**

- Whether a hospital runs a cardiac unit is published in the service directory.
  So "we don't do cardiac" can be checked instantly against public information —
  nobody has to reveal anything.
- How many beds are free right now is genuinely private, and we keep it private.
  So "we're full" is checked a different way: against **what that hospital has
  already agreed to take**. If you declined an ICU patient and accepted a
  different ICU patient twenty minutes later, you had a bed. Both halves are
  your own public commitments. Nobody's bed count was ever revealed.

That is why the system adds accountability **without** creating the central
database it was designed to avoid.

## 5. The numbers, and what each one means

Run `python experiments/adversarial.py` and `python experiments/run_all.py 5`.

### The headline result

| Hospitals that fabricate refusals | Without challenge | With challenge | Recovered |
|---|---|---|---|
| 0% (everyone honest) | 95.3% | 94.2% | — (costs ~1 pt) |
| 25% | 85.1% | **90.5%** | 53% of the loss |
| 50% | 65.5% | **83.9%** | 62% of the loss |
| 75% | 48.7% | **78.1%** | 63% of the loss |
| 100% | 32.9% | **74.6%** | 67% of the loss |

*(transfer success rate, 27 real Houston hospitals at the July 2021 Delta-peak
occupancy they actually reported, with capability read from the CMS
certification record. 6 seeds.)*

**Say it like this:** "If half the hospitals start protecting their beds by
claiming they have none, the network's success rate falls from 95% to 66%.
Turning on the challenge mechanism recovers about two thirds of that. It does
not fix the problem completely, and we say so."

**If someone remembers older numbers, the honest answer is that they moved
twice, and both times because the data got better.** The synthetic network
recovered 32-45%. With real hospitals but capability guessed from size it looked
like 73-79%. With capability read from the CMS record it settles at 53-67%. The
middle figure was flattered by an assumption: guessing capability from hospital
size concentrated cardiac, neuro and trauma in six large hospitals, which made
every fabricated refusal more damaging than it really is.

### The safety result — arguably the most important

**Zero false accusations, in every condition tested.**

We deliberately included a third kind of hospital: honest but cautious, keeping
a bigger reserve than the norm. It refuses more often than average and every
refusal is genuine. It was **never once overruled**. That matters because a
mechanism that punished caution instead of deceit would be worse than useless.

The number that makes this land: in the mixed condition the system raised
**9,100 challenges and overruled 562 refusals — and got none of them wrong.**
Across every condition, between 4,678 and 10,706 challenges, false accusations
stayed at exactly zero.

### Against today's practice

MAHROS beats the phone tree by **18.2 percentage points** (p < 0.0001,
dz = 12.09, 12 paired seeds), using **0.9 minutes of coordinator time per
transfer instead of 57.5**.

The gap holds in every real week, and widens as the hospitals fill up:

| Real week | How full they were | MAHROS | Phone tree |
|---|---|---|---|
| Aug 2020 (calmest) | 75% | 97.4% | 86.9% |
| Jan 2021 (COVID winter) | 82% | 97.3% | 83.3% |
| May 2022 (median week) | 91% | 94.6% | 76.3% |
| Jul 2021 (Delta peak) | 95% | 94.1% | 76.6% |
| Dec 2023 (post-pandemic) | 96% | 94.7% | 76.0% |

That widening is the bit to point at: **+10.5 points when the network has slack,
+18.7 when it has none.** Coordination matters least when it is easy.

We stress-tested this: even giving the phone tree its best possible case
(perfect information, short calls) it still loses. The advantage comes from
asking everyone at once, not from any assumption we chose.

**One honest caveat to volunteer.** A simple shared registry with greedy
first-fit (`nearest`) does about as well as MAHROS when everyone is honest
(+0.68 pts, not significant). The case for MAHROS is not that it beats other
*coordinated* systems on a good day — it is what happens when hospitals lie.
Point at the table above this one.

### Why decentralised barely loses anything — the explanation

This one earns respect, because it explains a null result instead of hiding it.

This is the question where you must give both halves of the answer, because
there are two and they point different ways.

> "The omniscient centralised optimiser is better than us — by one percentage
> point, and that difference is statistically real (p = 0.0091). It is *also*
> statistically equivalent to us within the two-point margin we declared before
> looking at the data (TOST p = 0.0023). Both are true. What we claim is the
> narrow version: **giving up the central data monopoly costs about one point of
> transfer success, and that is less than the margin we called practically
> meaningful in advance.**"

Say it that way round. Reporting only the equivalence would be picking the
flattering half of a two-sided result, and a good reviewer will find the other
half.

Equivalence to the **batched joint optimiser** is also established now
(TOST p = 0.0029), which it was not on the synthetic network.

If asked why a *decentralised* system holds up at all against one that sees
everything: escalation requests almost never collide, so joint optimisation
rarely gets to use the power it pays for, while its costs — pooling everyone's
data, and delaying every patient by a batching window — are paid every time.

### The honest negatives — say these before you are asked

- **The fairness layer improves balance but is no longer free.** It improves
  load balance by 0.030 Gini (p = 0.0057), and now carries a **marginal
  success-rate cost** of 0.59 points (p = 0.061). That is just outside
  significance and we report it as such rather than rounding it to "no cost".
  If asked why fairness only acts in the deliberation phase: most transfers are
  critical patients, and for those we deliberately switch fairness almost off in
  the scoring, because equity must not cost a critically ill patient minutes. So
  a hospital over its share instead *asks not to be picked* — and only for a
  non-critical patient, and only when someone equally close can take them.
- **The deliberation phase costs a detectable 0.64 points on a fully honest
  network** (p = 0.040) and **1.44 minutes** of mean time to care (p = 0.018).
  On the synthetic network the success cost was not detectable; with real data
  it is. It is insurance, and the premium is real.
- **The centralised optimiser is genuinely, if slightly, better** — by 1.0
  point (p = 0.0091), while still being statistically equivalent within our
  declared two-point margin.
- **A plain shared registry does about as well when everyone is honest.**
  Greedy first-fit against a shared bed registry is within 0.27 points of MAHROS
  (not significant). Our case rests entirely on what happens when hospitals lie.
- **Coordinated collusion still costs 12.6 points** and cannot be detected from
  the public record at all. Only proactive spot checks recover it.
- **The hospitals are real; the behaviour is not.** Real facilities, real beds,
  real geography, real occupancy, real capability — but no dataset records
  whether a hospital refused a transfer or whether it refused honestly, so the
  dishonesty model is ours. No real patients, no clinical validation.
- **We only detect a minority of individual lies.** At 50% strategic, 19,144
  fabricated refusals become 8,559 with deliberation on and 353 are formally
  overruled. The success-rate recovery is far larger than the per-lie detection
  rate, because catching a hospital once puts it back in the running for many
  later patients.

### Two more results worth having ready

**The audit ledger is doing real work.** Under attack, removing it drops the
network from 83.7% to 66.1% — a **17.6 point** fall, because with no shared
record there is no evidence and every challenge fails. Removing the ledger costs
exactly as much as removing argumentation altogether, which is the cleanest
statement of the design: *the argument is only as good as the evidence behind
it.* In the previous version of this project that ablation did literally
nothing, and we say so.

**Decision-making really is decentralised, and it is measurable.** MAHROS's
decision concentration is **0.086** — no single node decides. Both centralised
arms score exactly **1.000** by construction. The honest other half: MAHROS
sends more messages per transfer (38 vs 13 for a phone tree), because it asks
everyone at once. The saving is in scarce human coordinator time — **0.9
minutes per transfer against 57.5** — not in bytes.

## 6. Why anyone should believe the simulation

**The hospitals are real.** This is the first thing to say, and say it plainly.

> Every result runs on 27 real hospitals in Houston. Memorial Hermann Texas
> Medical Center. Houston Methodist. MD Anderson. Each one has the bed count,
> the staffed ICU count, the GPS coordinates and the week-by-week occupancy it
> reported to the US federal government. We did not invent a network and check
> it against statistics — we built the network *out of* the data.

Source: *COVID-19 Reported Patient Impact and Hospital Capacity by Facility*
(US HHS / CDC NHSN). Public domain, facility-level, **no patient data**.
8,176 real inpatient beds, 1,029 real staffed adult ICU beds, 216 weeks of
history per hospital.

### The validation, which could have failed

We gave the simulator each hospital's real beds and real load, ran it, and
compared the occupancy it produced against the occupancy that hospital actually
reported — facility by facility.

| | Error vs reported occupancy | R² |
|---|---|---|
| First attempt | 11.5 points | 0.33 |
| After fixing the admission mix | 7.4 points | 0.82 |
| After correcting for blocking | **4.2 points** | **0.94** |

**Say it like this:** "The first version failed. The error per hospital lined up
almost perfectly with how ICU-heavy that hospital was — we were sending 90% of
admissions to ward beds everywhere, which floods the ward and starves the ICU at
a hospital where half the beds are ICU. We fixed the model, and the fit went
from 0.33 to 0.94. A synthetic network could never have shown us that, because
every synthetic hospital had the same bed mix."

That is the strongest thing you can say about this work: **the real data caught
a modelling error.** That only happens when the data is genuinely load-bearing.

### Scenarios are real weeks, not dials

We do not turn a "surge multiplier" up. We pick a week these hospitals lived
through: the calmest fully-reported week (0.75 occupancy), the Delta peak (0.95),
the post-pandemic crisis (0.96).

Two things this corrected:

- **The busiest weeks were not COVID.** Houston ran fuller in December 2023 than
  in January 2021. We had assumed the opposite.
- **The network never sat at 70%.** We had tuned to 0.70. Reality was 0.75–0.96
  — a considerably harder regime than we had been simulating.

### What is *not* real — say this before you are asked

This is the half that matters most for credibility. Do not let a reviewer find
it first.

> The hospitals, their beds, their geography and their load are real. **Nothing
> about how they negotiate is.** No dataset in the world records whether a
> hospital refused a transfer, let alone whether it refused honestly — so the
> dishonest-refusal behaviour is our assumption, and we sweep its parameters
> rather than picking flattering ones.

Also assumed, and named as such: operating rooms, ventilators, step-down beds
and cath labs are not in the dataset, and neither is any specialty except
paediatric intensive care. So **which hospital can treat what is assumed, not
observed** — and since capability is what makes a transfer necessary, that is
our biggest remaining data gap. The fix is a CMS join on the CCN, which every
hospital record already carries.

Every parameter is classified observed / derived / assumed in
`docs/DATA_PROVENANCE.md` — **and that file is generated from the code**, so it
cannot drift away from what the system actually does. For this network: 5
observed, 5 derived, 7 assumed.

### If a reviewer says "your data isn't real"

Open `docs/DATA_PROVENANCE.md` and show them the facility table with the CCNs.
Then run:

```bash
python experiments/fetch_network.py --list      # the 27 hospitals, by name
python experiments/validate_occupancy.py        # simulated vs reported
```

Then tell them what is assumed, before they ask. The separate national check
against 3,186 facilities (`python experiments/calibrate.py`) still exists as a
*supporting* check on structural ratios — but it is not the validation, and do
not present it as one.

## 6b. The two newest pieces — and how to talk about them

### Attested refusal: we removed an assumption instead of leaning on it

This is the strongest thing to say about the project's honesty, so say it
plainly and early:

> "Our earlier results assumed a lying hospital simply cannot defend itself when
> challenged. That's convenient and not obviously true — a real bed manager
> would just repeat the lie. So we removed the assumption. In the attested
> version, to discharge a challenge you must *sign* the claim, co-signed by a
> key you don't control. The question stops being 'can a liar defend?' and
> becomes 'will a liar sign?' — and we swept that from 0 to 1."

| If a liar declines to sign... | Transfer success |
|---|---|
| never (signatures deter nobody) | 66.6% |
| a quarter of the time | 81.3% |
| half the time | 82.3% |
| always | 83.9% |

**The two endpoints are the proof the model is right.** At full deterrence the
attested protocol reproduces the old assumption *exactly* (p = 1.0000). At zero
deterrence it falls back to plain Contract Net (p = 0.31). Our old numbers were
the optimistic end of this sweep all along, and now we can say so with a number.

**What it signs, and why that matters.** Not a bed count — the *predicate*:
"no unit available", or "the only free unit is inside the reserve I declare".
Nothing about occupancy is disclosed. There are two predicates because a
cautious hospital holding a reserve genuinely *has* a bed, and forcing it to
certify "nothing free" would make an honest hospital sign something false. We
found that by testing it: the single-predicate version produced two false
accusations, and the test caught it.

**If asked "so you detect perjury?" — say no.** That claim died under
measurement:

> "We built automatic perjury detection and then tested it in a run where every
> signed statement was true by construction. It still produced 155 accusations,
> thirteen against hospitals that never lie. A hospital that was full at 2pm and
> admits someone at 2:30 after a discharge looks identical to one that lied.
> No time window fixes it. So we stopped claiming detection. What an attestation
> actually gives a regulator is one specific signed, timestamped proposition to
> check against records it can already inspect — instead of standing access to
> everyone's live bed state. We report the 0.61% false-positive rate on every
> referral."

### Real time: it is 9 milliseconds

If anyone doubts this could run live, you have the number:

> "Worst-case 99th-percentile decision latency is **12.3 milliseconds**, on 27
> real hospitals, with half of them lying. That's 81 times inside a
> one-second budget. Signature verification is 18 microseconds and needs no
> round trip to any authority, which is what keeps it inside the clinical
> window. Today's alternative spends 68 minutes of coordinator time."

Volunteer the caveat before they ask: single process, in memory, no network, no
database. It shows the *protocol* isn't the bottleneck, not that a deployment
would be easy.

Worth mentioning because it is a genuine engineering finding: getting there
meant fixing a quadratic in the audit ledger. Every challenge rebuilt the whole
chain to search it. On a four-day run, deliberation took 69.5 seconds; indexed,
it takes 9.0. Twelve tests pin that the fast path returns byte-identical results.

## 7. Questions the panel will ask

**"Isn't this just an auction?"**
An auction is step one and two. The contribution is steps three and four: a
refusal can be contradicted by evidence and has to be defended. No auction does
that.

**"Why not just use a central booking system?"**
Three reasons, in order of strength. (1) Hospitals will not hand a competitor or
a regulator their live bed state — this is the reason such systems fail to get
adopted, not a technical one. (2) We measured what centralisation would buy you,
and within a 2-point margin it is nothing, because requests rarely collide.
(3) One node holding everything is one node to attack or subpoena.

**"What stops a hospital lying anyway?"**
Nothing stops it — the mechanism does not prevent lying, it makes lying
*answerable*. A refusal goes on the permanent record. If you contradict it later,
anyone can point at both halves. We measure exactly how much that helps: about
40% of the damage, not 100%.

**"Where does the AI come in?"**
Careful here — be precise. The decision is made by a deterministic rule, not by
a language model. That is on purpose: if a language model picked the winner,
none of our numbers would be reproducible. The multi-agent part is the AI part:
autonomous agents, one per hospital, negotiating and arguing under a formal
argumentation semantics. The optional language model writes the plain-English
explanation that goes on the record; it never decides.

**"Is the argumentation part your own invention?"**
No, and that is a strength. It is Dung's abstract argumentation from 1995, the
standard formalism for deciding which arguments survive a dispute. We apply it
to a new problem. Our implementation is validated against the textbook cases in
`tests/test_argumentation.py`.

**"How many hospitals does this scale to?"**
Tested at 12 and 30. Cost is one message round per hospital asked, capped at 8
per round. Roughly 41 messages per transfer versus 5 for a phone tree — we
spend bytes to save human minutes, and we say so.

**"What would you need to deploy it?"**
A real pilot with two or three willing hospitals, ethics approval, and the
capability directory, which already exists. We would not deploy on these results
— this is a proof of concept, TRL 3–4.

**"What is the weakest part?"**
Say it straight: *the fairness layer does not show a statistically significant
effect, and every number comes from a simulation with no clinical validation.*
Volunteering your weakest point is what a panel is actually testing for.

## 8. The three-minute live demo

1. Open `python -m mahros.server` → http://127.0.0.1:8000
2. Pick a hospital, choose **cath lab / cardiac / acuity 5**, press
   **Find this patient a bed**. Let them watch every hospital get asked at once
   and answer in its own words. Point out that most refuse for lack of
   *capability*, not beds — that is why transfers exist.
3. Now the turn. In **Are they telling the truth?**, click a tertiary hospital to
   **protective**. Run the same case again. It refuses.
4. Point at the challenge appearing, with the evidence card from the shared
   record, and the refusal being struck out.
5. Turn **off** "Check refusals against the shared record" and run it once more.
   The same lie now goes unchallenged. That is the ablation, live.
6. Finish on **Fake a record** — the ledger check fails instantly.

Keep an eye on the **"0 wrongly accused"** counter throughout. If a panellist is
sharp they will ask about false positives, and you can point at it.

## 9. If you get stuck

Say: *"I don't know — that isn't something we measured."* Then say what you would
measure to find out. Panels reward that far more than a confident guess, and this
project has enough honest, measured results that you never need to bluff.
