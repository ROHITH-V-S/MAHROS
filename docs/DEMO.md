# Demo runbook

One page. Read it once the night before, glance at it on the day.

---

## Before you leave the house

```
cd C:\Users\Rohith\Desktop\mahros
python -m mahros.server
```

Leave that terminal open. Open **http://127.0.0.1:8000**.

Two tabs in the top bar: **Live sim** and **Results**.

Sanity check, 30 seconds:
1. Click **▶ Run continuously** — cases start negotiating on their own.
2. Let it run to ~15 transfers, then click **Forge ledger** — the ledger pill turns red.
3. Click **Results** — charts load.

If all three work, you are ready. Stop continuous mode and click **Reset network**
so you start clean.

---

## The 3-minute version

**1. Frame the problem (30s).** "A patient is already admitted and stabilised.
They deteriorate and now need an ICU bed or a surgeon their hospital doesn't have
free. Someone has to find a bed elsewhere. Today that's a person making phone
calls, one at a time. About 3.5% of US admissions are transfers like this —
roughly 1.5 million a year."

**2. Run one case (60s).** Set origin to a **primary hospital (T1)**, resource
**cath lab**, specialty **cardiac**, acuity **5**. Hit **Request transfer**.

Talk over it as it runs:
- "It's asking 8 peers at once — that's the point, a phone call is serial."
- "Most refuse, and they say *why*: `no_specialty_capability`. Only two hospitals
  in this network have a cath lab. It's not a bed shortage, it's a capability
  shortage."
- Point at the scoring table: "Time, capability, strain, fairness — each broken
  out. Nothing is a black box."
- Point at the rationale: "Every agreement carries the reason it was made, in
  plain English, and it's on the ledger."

**3. Show the two properties nobody else combines (60s).**
- Uncheck **Fairness layer enabled**, run continuously ~20 cases, show the burden
  bars skewing. Re-enable it, show them evening out.
- Click **Forge ledger**: "A hospital tries to under-report what it accepted.
  The Merkle root no longer matches. Any member hospital can check this
  independently — which is why the fairness counts can't be gamed."

**4. Land the result (30s).** Switch to the **Results** tab.
"Against a phone tree: +31% relative success rate, 62% fewer safe-window
breaches, 98% less coordinator time. Against a *centralized* system that sees
every hospital's private data: statistically indistinguishable — 87.0% vs 86.3%
— while no node in my system brokers more than 15% of negotiations. That's the
contribution: central-authority performance without the central authority."

---

## Questions you will get

**"Isn't it faster to just phone around?"**
For one transfer, yes. This is about ten transfers at once during a surge, when
your coordinators are already saturated. The phone-tree baseline in my model
consumes 47 minutes of staff time per transfer; mine consumes 1.1. And a phone
call leaves no auditable record and no fairness tracking across months.

**"Why would a hospital give up its patients?"**
It isn't zero-sum — hospitals both send and receive, and the fairness layer is
what makes that balance real instead of assumed. It's also already standard
practice, driven by duty of care and liability. Framed as deployment *within* a
hospital network or trust, not between rivals.

**"Is the LLM making the decisions?"**
No, and deliberately. The deterministic scorer picks the winner; the LLM writes
the explanation. If the LLM chose, none of my numbers would be reproducible.
Letting it arbitrate is available as an ablation — and finding it *doesn't* beat
the rule is a legitimate result.

**"Is this a real blockchain?"**
It's a permissioned consortium ledger, and I'd defend that as the correct
choice — hospitals in one health system don't need permissionless consensus.
What they need is append-only, tamper-evident, independently verifiable. The
default is a Merkle hash chain; there's a Solidity contract for when you want
multi-party consensus and two-sided attestation.

**"How do you know the simulation is realistic?"**
I don't fully, and I'd say so. One external check: transfers come out at 3.7–4.3%
of admissions against the ~3.5% real-world figure — and I didn't tune for that,
it fell out of the escalation model. Everything else is a documented assumption
in `docs/MODEL_ASSUMPTIONS.md`.

**"What's the weakest part?"** *(Have this answer ready — it lands well.)*
Two things. The 25% stale-information rate in my phone-tree baseline is
plausible but unsourced, and it drives a chunk of my headline win — it needs
sensitivity analysis. And I model hospitals as honest bidders; a hospital that
strategically under-reports capacity to dodge transfers would defeat the current
design. The ledger deters denial after the fact, not misreporting before it.

---

## If something breaks

**`[Errno 10048]` / port in use** — a server is already running. Either just open
the page, or use `python -m mahros.server --port 8001`.

**Page loads but nothing happens on click** — the WebSocket dropped. The dot next
to "MAHROS" top-left is grey/red instead of green. Refresh the page.

**Results tab is blank** — the sweep hasn't been built:
```
python experiments/run_all.py 5
python experiments/build_dashboard.py
```
(~3 minutes.)

**Everything is on fire** — fall back to the terminal, it needs no server:
```
python -m mahros.cli compare --scenario surge_scarcity --seeds 3
python -m mahros.cli verify
python -m pytest tests -q
```

**Total fallback** — the Results dashboard is also published as a static page you
can open on any machine with a browser, no Python needed. Have the URL saved.

---

## Numbers worth memorising

| | MAHROS | Phone tree | Centralized |
|---|---|---|---|
| Success rate | 87.0% | 66.2% | 86.3% |
| Mean time to care | 66.5 min | 88.0 min | 68.5 min |
| Staff min/transfer | 1.1 | 47.3 | 0.0 |
| Burden Gini | 0.265 | 0.356 | 0.256 |
| Decisions brokered by one node | 14.7% | 13.6% | 100% |

5 seeds, surge + scarcity scenario, 95% CI. 50 tests passing. TRL 3–4.
