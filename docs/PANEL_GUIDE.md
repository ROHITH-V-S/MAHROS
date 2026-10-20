# Explaining MAHROS

For the team, before a review. Plain English first, then the numbers, then
the questions you will be asked and how to answer them.

The numbers here are generated from the result files (see the block below);
everything else is hand-written. For the live demo itself, see
[DEMO.md](DEMO.md).

---

## 1. The one-paragraph version

A patient is already in hospital and gets worse. They now need something this
hospital does not have right now: an ICU bed, a cath lab, a neurosurgeon.
Today, a person phones other hospitals one at a time until someone says yes.
MAHROS asks every capable hospital nearby **at once**; each hospital's agent
answers for itself from its own beds; the best answer wins. And because
saying "sorry, we're full" is the cheapest way to avoid a patient, MAHROS can
**check** a refusal: against the public register of what each hospital
treats, and against what that hospital itself agreed to take in the last
hour, without anyone revealing how many beds they have.

> 🧒 Like asking the whole class at once who can lend you a pencil, instead of
> asking one friend at a time. And if a friend says "I don't have one" but you
> just saw them lend one to someone else, you can say so.

## 2. What is real and what is not

**Real:** the 27 Houston hospitals and 30 Phoenix hospitals, their beds, ICU
beds and weekly occupancy (US government reports, 2020–2024); what each one
can treat (US government register); the roads between them; which kinds of
patients really get transferred (37,439 California transfers, 2021).

**Simulated:** the patients' arrivals and deterioration (fitted so the
hospitals end up as full as they really were), and **how hospitals answer a
transfer request, including whether they tell the truth.** No public data
records that anywhere.

Say this sentence early: *"The hospitals are real. Their behaviour is
simulated, because nobody publishes transfer refusals, and every behavioural
setting is swept."*

## 3. What is new

1. **A refusal becomes a claim you can check**, without anyone giving away
   their bed count. "We don't treat that" is checked against the public
   register; "we're full" against the hospital's own recent acceptances on a
   record it cannot edit.
2. **Signed refusals**: the hospital must sign its claim. Whether a lying
   hospital would sign is a setting we sweep from 0 to 1, not an assumption.
3. **A defence against collusion**: hospitals that coordinate their refusals
   leave no contradiction to catch. Random spot checks catch them. (We also
   tried checking the hospitals already caught more often; at the same
   checking effort it did **not** do better, and we report that.)
4. **Real data underneath**: two cities, real capability, real roads, real
   case-mix, and a backend a reviewer can inspect and try to tamper with.

What is **not** new, and we say so: asking hospitals for bids (Contract
Net, 1980), sharing load between hospitals (several published optimisers),
hash-chained audit logs.

## 4. How a transfer works (six steps on screen)

1. **Ask everyone at once.** The hospital with the patient sends the request
   to the 8 nearest hospitals that list the right specialty.
2. **They each answer.** Each hospital's agent checks its own beds and says
   yes (with how soon) or no (with a reason).
3. **Question the doubtful ones.** The Verifier checks every "no" against the
   register and the ledger. A contradicted "no" is challenged.
4. **Settle the argument.** The hospital defends (or signs) its refusal, or
   the refusal is struck out. The rule for who wins is a standard one (Dung's
   grounded semantics), not something we invented.
5. **Pick the best hospital.** By time to care first, then specialty fit,
   then how strained it would become, then fairness.
6. **Record it permanently.** The agreement and every capacity refusal go on
   the ledger; every row goes in the database and the audit log.

## 5. The numbers to remember

<!-- results:panel:start -->
- MAHROS places 13.0 percentage points more patients in time than a phone tree given perfect information (p < 0.0001).
- Against optimisers that see every hospital's true occupancy: -0.3 pts vs central greedy, +0.4 pts vs batched optimal; equivalent within ±2 points: yes / yes.
- On an honest network a shared registry ties MAHROS (-0.0 pts, Holm p = 0.8980).
- With half the hospitals refusing falsely, a shared registry places 68.6% and MAHROS 86.5% (+17.9 pts).
- With half the hospitals refusing falsely, success falls to 63.7% without checking and holds at 86.5% with it.
- With half the hospitals protective, checking refusals keeps success at 86.2%; without checking it falls to 64.5%, and without the ledger to 62.4%.
- Requiring hospitals to sign their refusals gives 62.3% to 86.5% success depending on how often a liar refuses to sign (no checking: 63.7%).
- A cartel of 6 cuts success to 79.2%; 15% random spot checks bring it back to 90.8%.
- Checking hospitals already caught more often is no better than uniform 15% spot checks at similar effort (+0.5 pts, p = 0.6581, 111% of the checks).
- Phoenix (30 hospitals, not re-tuned): MAHROS beats the phone tree by +9.9 to +14.3 points across four real weeks.
- Simulated occupancy is within 3.8 points of what Houston's hospitals reported (R² 0.95), and 3.0 points for Phoenix (R² 0.97), which was not tuned.
- 5.4% of Houston's weekly capacity reports and 0.2% of Phoenix's are impossible or look copied (20 of 27 and 3 of 30 hospitals have at least one report that cannot be right).
- Coordinator time per transfer: 1.1 min with MAHROS, 50.3 min by phone.
- MAHROS beat the phone tree under every value of every unsourced parameter. Checking refusals adds 21.8 points when protective hospitals lie consistently; when they lie only half the time a second round of asking already recovers almost everything, and checking costs up to 0.4 points.
<!-- results:end -->

Full tables: [RESULTS.md](RESULTS.md).

## 6. Questions you will be asked

**"Your data is simulated."**
The hospitals, beds, specialties, roads, weekly occupancy and case-mix are
real, and you can query them in pgAdmin. The model reproduces the occupancy
the hospitals reported, from an empty start, in both cities, and Phoenix was
never tuned. What is simulated is behaviour, because no transfer-refusal data
is public. That is labelled everywhere.

**"You caught liars you invented."**
Yes, so we don't lean on it. The signed version asks whether a liar will
sign, and we report the whole range from "always" to "never". Separately, the
real HHS reports from these same hospitals are often impossible or copied
(the Data Integrity Agent's numbers), so unreliable self-reports are an
observed problem, not an invented one.

**"Why is zero false accusations impressive?"**
Don't call it impressive. It is a safety property we test: an honest refusal
is backed by the hospital's real state, so it can always be defended. It
failed once, when spot checks were applied to "we don't treat that" refusals,
and the fix is in the code and the tests.

**"The phone tree is a strawman."**
The headline gives the phone tree perfect information: no stale whiteboards,
no lost "yes" answers. MAHROS still wins, because asking eight hospitals at
once beats asking them one after another. That is queueing, not a parameter
we chose. Call length and staleness are swept.

**"Why not just a shared bed registry?"**
When everyone is honest, a registry does as well as MAHROS; we say so. A
registry believes what it is told. Under the same protective hospitals it
falls further than MAHROS (registry_attack in RESULTS.md).

**"Why not a central optimiser?"**
It needs every hospital's live bed count. With that it does slightly better;
RESULTS.md gives the size of the gap and the equivalence test. MAHROS gets
close without anyone handing over private state.

**"Where is the optimisation?"**
The problem is stated formally in the README: maximise patients reaching care
in time, then minimise time to care, then balance load, under capacity,
capability and privacy constraints. Each award is a scored choice, and we
compare against the minimum-cost assignment (Hungarian) as the ceiling.

**"Show me it from the backend."**
Open pgAdmin. Run a transfer; open `agent_decisions`, `challenges`,
`allocations`, `audit_log`. Then let them edit any audit row and press
**Verify now**.

**"Is this a blockchain?"**
No. It is a hash-chained ledger and a hash-chained audit log in PostgreSQL,
with checkpoints. That gives tamper *evidence*. A superuser could rewrite the
whole chain consistently; a checkpoint kept outside the database catches that.

**"Does it work outside Houston?"**
We replicated it on 30 Phoenix hospitals with no changes (replication in
RESULTS.md). Phoenix ran cooler, so it is a different regime, not a copy.

**"Why an LLM?"**
It only writes explanations, it is off by default, and it is never involved
in a decision. Every number is reproducible without it.

**"Is the case-mix right for Texas?"**
It is Californian, because no Texas source exists. Our transfer rate (4.5% of
admissions) is above California's 1.6% of discharges, so transfer volume is
swept and the ranking holds.

## 7. If you get stuck

Say what is real, what is simulated, and point at RESULTS.md. Never say
"real-time" (the data is historical), "blockchain", "lie detector", or "saves
lives". Say "historical public data", "hash-chained audit log",
"capacity-claim verification", and "reduces time to care".
