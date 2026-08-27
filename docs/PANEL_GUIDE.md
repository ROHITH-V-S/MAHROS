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
| 0% (everyone honest) | 86.9% | 86.0% | — (costs ~1 pt) |
| 25% | 71.7% | 76.5% | 32% of the loss |
| 50% | 62.4% | 71.2% | 36% of the loss |
| 75% | 57.0% | 69.1% | 40% of the loss |
| 100% | 47.0% | 65.0% | 45% of the loss |

*(transfer success rate, surge + scarcity scenario, 6 seeds)*

**Say it like this:** "If a quarter of hospitals start protecting their beds by
claiming they have none, the network's success rate falls from 87% to 72%.
Turning on the challenge mechanism recovers about a third of that. It does not
fix the problem completely, and we say so."

### The safety result — arguably the most important

**Zero false accusations, in every condition tested.**

We deliberately included a third kind of hospital: honest but cautious, keeping
a bigger reserve than the norm. It refuses more often than average and every
refusal is genuine. It was **never once overruled**. That matters because a
mechanism that punished caution instead of deceit would be worse than useless.

### Against today's practice

MAHROS beats the phone tree by **18.2 percentage points** (p < 0.0001, and the
only comparison that survives correction for multiple testing), using **1.1
minutes of coordinator time per transfer instead of 47**. We stress-tested this: even giving the phone tree its
best possible case (perfect information, 5-minute calls), it still only reaches
79.5%. The advantage comes from asking everyone at once, not from any assumption
we chose.

### Why decentralised barely loses anything — the explanation

This one earns respect, because it explains a null result instead of hiding it.

A centralised optimiser that sees everything and solves several transfers
together should beat us. It does not: across **30 paired seeds** the difference is
+0.31 points (p = 0.67), and against a fully joint (Hungarian) optimiser it is
+1.05 points (p = 0.16). Neither is detectable.

And we found out **why**: transfer requests almost never collide. Even batching
for a full hour, the average batch holds 1.2 patients. There is almost nothing
for joint optimisation to optimise, while its costs — pooling everyone's data,
and delaying every patient by the batching window — are paid every single time.

**And we can now say the strong version.** "We found no difference" is not the
same as "we proved they are the same" — the proper test for the latter is TOST,
and at 30 seeds it **passes**:

> "We ran a formal equivalence test — two one-sided tests, against a two
> percentage point margin we declared before looking at the data. MAHROS is
> statistically equivalent to the centralised optimiser, p = 0.013. That's a
> positive claim, not just a failure to find a difference."

**One honest qualification, and volunteer it.** Against the *fully joint*
optimiser — the one that batches transfers and solves them together — equivalence
is not established (p = 0.10). No difference is detectable there either, and the
point estimate actually favours MAHROS, but the interval is a fraction too wide
to close the claim. Say that before you're asked; it costs you nothing and it
shows you know the difference between the two statements.

### The honest negatives — say these before you are asked

- **The fairness layer works, but it took 30 seeds to show it.** It improves
  load balance by 0.073 Gini (p < 0.0001) at **no measurable cost** in success
  rate or waiting time. Worth being straight about the history: at 12 seeds this
  was p = 0.13 and we reported it as a null result. It was under-powered, not
  absent. If asked why it works only in the deliberation phase: three quarters of
  transfers are critical patients, and for those we deliberately switch fairness
  almost off in the scoring, because equity must not cost a critically ill
  patient minutes. So a hospital over its share instead *asks not to be picked* —
  and only for a non-critical patient, and only when someone equally close can
  take them.
- **The deliberation phase costs about 0.9 points of success on a fully honest
  network**, because it adds a round trip. It is insurance, and insurance has a
  premium.
- **Everything is simulated.** No real patients, no clinical validation.
- **We only detect a minority of individual lies.** The success-rate recovery is
  much larger than the per-lie detection rate, because catching a hospital once
  puts it back in the running for many later patients.

### Two more results worth having ready

**The audit ledger is doing real work.** Under attack, removing it drops the
network from 69.6% to 61.0% — because with no shared record there is no evidence,
so every challenge fails. In the previous version of this project that ablation
did literally nothing, and we say so.

**Privacy is measured in bits, not adjectives.** Each MAHROS message narrows an
observer's belief about the sender's free beds by 2.04 bits — 57.8% of what a
fully transparent system would give away. A centralised optimiser gives 2.83 bits
per message, 87.3%. The honest other half: MAHROS asks more hospitals, so *in
total per transfer* it discloses more than a phone tree does (15.8 bits vs 1.9).
We report both halves.

## 6. Why anyone should believe the simulation

We checked the model against **3,186 real hospitals** — the US federal
facility-level capacity dataset (HHS/CDC, public domain).

| What we assumed | What real hospitals show | |
|---|---|---|
| Network runs near 70% occupancy | **70.0%** mean | matches |
| ICU runs hotter than the ward | ICU 80.3% vs ward 70.0% | matches |
| ICU beds per inpatient bed rises with hospital size | 0.108 → 0.118 → 0.127 | matches |
| Small hospitals often cannot escalate at all | 27.6% have **no** ICU beds | confirms the premise |

Run `python experiments/calibrate.py` and it prints this table.

**Say it like this:** "We tuned the simulation to run at about 70% occupancy
before we had any real data. When we checked against three thousand real
hospitals, the actual figure was 70.0%. That was not fitted — it was a lucky
confirmation that we were in the right regime."

Where the model deliberately differs — our network has about half the ICU depth
of a US hospital — the calibration report says so explicitly and quantifies it,
because we are modelling a critical-care-scarce system on purpose.

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
