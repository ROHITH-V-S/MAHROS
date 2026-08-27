# Demo runbook

One page. Read it the night before, glance at it on the day.
For *what to say* and the questions you will be asked, see
[PANEL_GUIDE.md](PANEL_GUIDE.md).

---

## Before you leave the house

```
cd C:\Users\Rohith\Desktop\mahros
python -m mahros.server
```

Leave that terminal open. Open **http://127.0.0.1:8000**.

Two tabs in the top bar: **Live negotiation** and **Results**.

Sanity check, 60 seconds:

1. Click **▶ Run continuously** — cases start negotiating on their own. Stop it.
2. Click **Fake a record** — the *records intact* pill turns red.
3. Click **Results** — the charts load.
4. Click **Start over** so you begin clean.

If the Results tab is empty, you have not built it yet:

```
python experiments/run_all.py 5
python experiments/build_dashboard.py
```

---

## The 3-minute version

### 1. Frame the problem (30 s)

> "A patient is already admitted and stabilised, and then deteriorates. They now
> need an ICU bed, or a cath lab, that this hospital hasn't got. Today, a human
> being picks up a phone and calls hospitals one at a time. That takes about
> forty-seven minutes of staff time per transfer, and by the sixth call the
> information from the first is out of date."

### 2. Show the parallel ask (45 s)

Set up **cath lab / cardiac / acuity 5**. Press **Find this patient a bed**.

Point at the transcript as it fills:

> "Every hospital is asked at the same moment, and each one answers for itself —
> in its own words. Notice most of them are refusing because they don't *have* a
> cath lab, not because they're full. That's the whole reason transfers exist:
> it's scarce capability, not bed count."

Point at the countdown bar at the top of the panel — the clinical window burning
down.

### 3. The turn — make a hospital dishonest (60 s)

In **Are they telling the truth?**, find a tier-3 hospital and click its button
so it reads **protective**.

> "Accepting a transfer costs a hospital a bed it may need tonight. The cheapest
> way to avoid one is to say you haven't got one. In an ordinary auction nobody
> can check that."

Run the same case again. It refuses.

Now point at what happens next:

- the **challenge** bubble, with the evidence card underneath it
- the hospital failing to justify itself
- **REFUSAL STRUCK OUT**
- the **argument map** — green boxes survived, dashed red ones were defeated

> "The refusal was checked against the shared record. Nobody revealed a bed count
> — it was checked against what that hospital has already agreed to take. If you
> declined an ICU patient and accepted a different one twenty minutes later, you
> had a bed."

### 4. The ablation, live (30 s)

Turn **off** "Check refusals against the shared record". Run it again.

> "Same hospital, same lie, and now it simply works. That's the comparison in the
> paper — except there we run it across five thousand transfers instead of one."

Turn it back on.

### 5. Land it (15 s)

Point at the masthead: **"0 wrongly accused"**.

> "The mechanism has never once overruled a hospital that was telling the truth.
> That's the number that decides whether you could deploy this — a system that
> punished caution instead of dishonesty would be worse than useless."

Finish on **Fake a record** if you have time: the chain check fails instantly.

---

## Things that will go wrong, and what to do

**"I made it protective but nothing gets caught."**
The network is too full — a protective hospital only lies when it genuinely has
a bed, so when the network is full its refusals are simply *true*. Drag **How
busy is the network?** down to ~50% and press **Start over**.

Say it out loud if it happens: *"it's not lying here because it really is full —
that's the mechanism behaving correctly."* It sounds much better than silence.

**"No challenge appears at all."**
Challenges need history. Click **Run 10 fast** first to build up a record, then
run your case.

**"The page is blank / the dot is red."**
The server died. Restart it in the terminal; the page reconnects on its own.

**"Results tab shows an error page."**
You haven't built the dashboard. Use the two commands above, or just skip the
tab — the live demo is the interesting half.

**Someone asks for the code mid-demo.**
`mahros/negotiation/argumentation.py` is the contribution and is about 200 lines
of well-commented Python. `mahros/negotiation/challenge.py` is what makes the
ledger evidence.

---

## If you only get 60 seconds

Skip steps 1 and 2. Make a hospital protective, run one case, point at the
challenge and the struck-out refusal, and say:

> "Hospitals have a reason to refuse transfers they don't want. Our protocol lets
> a refusal be challenged against a shared record, and the hospital has to
> justify it. An honest refusal always can. A fabricated one can't. It recovers
> about 40% of the damage that dishonest refusals do to the network, and it has
> never wrongly accused a hospital that was telling the truth."

That is the whole project in four sentences.
