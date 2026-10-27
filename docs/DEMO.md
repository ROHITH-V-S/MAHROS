# Demo runbook

One page. Read it the night before; glance at it on the day.
For *what to say* and the questions you will be asked, see
[PANEL_GUIDE.md](PANEL_GUIDE.md).

---

## Before the review (10 minutes)

1. Start **Docker Desktop** and wait until it says it is running.
2. In a terminal:

   ```
   cd C:\Users\Rohith\Desktop\mahros
   docker compose up -d --build
   ```

3. Open two browser tabs:
   - **http://localhost:8090**: MAHROS
   - **http://localhost:5051**: pgAdmin. In the left panel, open
     *Servers → MAHROS database → Databases → mahros → Schemas → public → Tables*.
     It should open without asking for a password. If it asks, the password
     is `mahros-admin-local`.
4. In MAHROS, type your name in **Your name** (top right). Every change you
   make is recorded under it.
5. In the **Hospital data** tab, click **Restore all to real data** (twice, to
   confirm). You start clean, from what the hospitals really reported.
6. Check the top bar shows **saved to PostgreSQL** (green) and **records intact**.

If **saved to PostgreSQL** is red, the database is not running: re-run step 2,
or `docker compose logs app`.

Fallback if Docker will not start: `python -m mahros.server` runs the same
console in memory (no database tab, nothing saved). Say so if you use it.

---

## The 10-minute demo

### 1. The data is real, and you can query it (1 min)

pgAdmin → `hhs_weekly_reports` → right-click → *View/Edit Data → First 100 Rows*.

> "These are the weekly reports 27 named Houston hospitals sent to the US
> government, 216 weeks each. The simulation runs on these hospitals, their
> real beds, their real specialties from the CMS register, and real road times."

Then try to change one value in that grid and save. **The database refuses**:
*"holds real public data ... and is locked"*.

> "The app cannot write to the real data at all, and the database refuses
> ordinary edits even from the owner. Someone with full control of a database
> can always switch a lock off; that is why every change also goes on a
> hash-chained audit log."

**The order matters.** Tested twice from a fresh restore, same result both
times. Start from **Restore all to real data** with no transfers yet, and run
nothing extra in between. (If the cath lab case runs first, Methodist takes
that patient and the liar case stops working.) If something looks off:
restore, start again at step 2. To show an earlier case again, don't re-run
it: **Live tab → Saved transfers → Show** plays it back from the database.

### 2. Edit a hospital, see the system use it (1 min)

**Hospital data** tab → Houston Methodist Hospital (450358). Its ICU shows
about **155 / 158**: what it reported in January 2021. Capacity is locked;
occupancy is not. Change ICU occupancy to **100** → **Save**. Point at the
yellow **edited** badge (**real: 155**) and **History**. pgAdmin → `audit_log`
→ *Last 100 Rows*: the same change, who, when, before/after, fingerprint.

### 3. Ask every hospital at once (1 min)

Live tab. Origin **450076 (MD Anderson)**, **ICU bed / general / acuity 5**.
Run it. **Houston Methodist takes the patient.** Point at "What actually
crosses the boundary". Leave this bed alone in Receiving hospital until 4d.

### 4. Catch a false refusal (2 min)

Why it is fixed, if asked: a protective hospital lies only when it really has
a bed and the patient is below acuity 5; "we're full" is challenged only if
that hospital accepted the same kind of patient in the last hour.

Switch **450358** to **protective**. Same origin, **acuity 4**. Run it.
Methodist: *"we're down to our last one"* → **challenge**: *"it accepted a
different ICU transfer ... ago. It had one to give."* → defence fails →
**refusal struck out**.

> "Nobody revealed a bed count. The claim was checked against what Methodist
> itself agreed to, on a record it cannot edit."

### 4a. Checking off (30 s)

Untick **Check refusals against the shared record**, acuity 4 again: the
same refusal is believed. Tick it again.

### 4b. Honest and full (1 min)

Methodist → **honest**; Hospital data → ICU **158** → Save. Acuity 4 again:
same refusal, same challenge, **defence holds**. (450193, which took the 4
patient, is challenged too and also defends.)

### 4c. The receiving hospital gives the bed back (1.5 min)

**Receiving hospital** tab: Methodist has a red dot (the bed from step 3).
**Can't take this patient** → **Equipment fault** → **Release the bed and
offer the patient to other hospitals**. It costs the patient 5 minutes;
Methodist is left out. **Live negotiation** opens with *"DECLINED THE BED"*
and says the second choice (450193) no longer had a bed, so 450068 was
chosen.

### 4d. Giving a bed back doesn't wipe the evidence (1 min)

Methodist → **protective**, ICU **100**. Acuity 4 again: challenged *"It took
another ICU patient ... and then gave it back (equipment fault). It is asked
to justify this refusal."* → struck out. Receiving hospital → 450358: record
*"2 awarded, 1 given back ... −0.037 on its score"*.

### 4e. "We don't have that" (30 s)

**Cath lab / cardiac / acuity 5** from 450076: "no cath lab" refusals,
checked against the public CMS register, none challenged.

### 4f. No evidence, no accusation (30 s, optional)

Origin **670008**, **ICU / general / acuity 5**: four hospitals honestly full
(real January 2021 data), nobody challenged.

### 4g. Nothing is lost on reload (30 s)

Reload the page. **Saved transfers** lists everything; **Show** on the
step-4 transfer plays it back from the database, labelled PLAYBACK.

### 5. Show the transfer in the database (1 min)

**Hospital data** tab → **Transfers saved in the database** → **trace** on the
last one: every agent's decision, the challenge, the allocation, the ledger
block, the audit entries. In pgAdmin, `agent_decisions` and `challenges` show
the same rows.

### 6. Tamper, and get caught (1.5 min)

**Let the reviewer do this.** In pgAdmin open `audit_log`, change any value in
the `actor` column of an old row (e.g. to `nobody`), and save (F6).

MAHROS → **Hospital data** → **Verify now**. The panel turns red and names
the row: *"entry #N: contents changed: this row no longer matches its own hash"*.

Put the value back and verify again: green.

> "The app cannot edit these tables at all; it can only add rows. The owner can
> edit them, as you just did, and the next check names the row."

### 7. Reset (30 s)

**Restore all to real data** (twice). Every hospital is back to what it
reported. Nothing was deleted: the history of everything you just did is still
in `audit_log` and `transfer_requests`.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| **saved to PostgreSQL** is red | `docker compose up -d`, then refresh |
| No challenge appears in step 4 | Methodist did not take the acuity-5 patient in step 3, usually because a transfer ran before it. Restore all to real data and start again at step 2, in order |
| Panel wants an earlier case again | Live tab → Saved transfers → **Show** (plays it back; nothing re-runs) |
| pgAdmin asks for a password | `mahros-admin-local` |
| Results tab empty | `python experiments/run_all.py 5` then `python experiments/build_dashboard.py` |
| Anything else | `docker compose restart app`. The data is in the database, so nothing is lost |
