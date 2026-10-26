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

### 2. Ask every hospital at once (1.5 min)

Live tab. Set **cath lab / cardiac / acuity 5**, any origin. Press
**Find this patient a bed**.

> "Every capable hospital in reach is asked at the same moment, and each
> answers for itself from its own beds. Most refuse because they don't have a
> cath lab: transfers exist because capability is scarce."

### 3. Edit a hospital, see the system use it (2 min)

**Hospital data** tab → Houston Methodist Hospital (450358). Its ICU shows
about **155 / 158**: that is what it reported in January 2021. Capacity has a
lock; occupancy does not.

Change ICU occupancy to **100** → **Save**. Point at:
- the yellow **edited** badge, with **real: 155** underneath
- **History**: *"user:your-name · ICU: occupied 155 → 100"*

pgAdmin → `audit_log` → *View/Edit Data → Last 100 Rows*: the same change,
with the before and after values, who, when, and a fingerprint (`row_hash`).

### 4. Catch a false refusal (3 min)

Two rules make this work on the first run, every time. If asked, say them:
a protective hospital lies only when it **really has a bed** and the patient
is **below acuity 5**; "we're full" is challenged only if that hospital
**accepted the same kind of patient in the last hour**. So: give Methodist
room (step 3 did: ICU 100), let it accept one, then ask again at acuity 4.

1. Live tab. Origin **450076 (MD Anderson)**, **ICU bed / general / acuity 5**.
   Run it. Houston Methodist takes the patient. (Leave that bed alone in the
   Receiving hospital tab for now. If it is given back, the challenge in step 3
   still happens, but is worded "took one and gave it back", not "had one".)
2. In **Are they telling the truth?**, switch **450358** to **protective**.

   > "Accepting a transfer costs a bed. The cheapest way to avoid one is to say
   > you are full. In an ordinary auction nobody can check."

3. Same origin, **acuity 4**. Run it. Point at:
   - Methodist: *"we're down to our last one"*
   - the **challenge**: *"it accepted a different ICU transfer 1 minute ago. It
     had one to give."*
   - the defence failing, **refusal struck out**, the argument map

   > "Nobody revealed a bed count. The claim was checked against what Methodist
   > itself agreed to a minute ago, on a record it cannot edit."

4. Untick **Check refusals against the shared record**, run acuity 4 again: the
   same refusal is taken at face value. Tick it again.
5. Optional, the honest case: switch Methodist back to **honest**, and in
   Hospital data set its ICU to **158** (full). Run acuity 4: same refusal,
   same challenge, and this time **the defence holds**.

   > "Same words, same challenge. The liar loses, the honest hospital wins,
   > because only the honest one's own beds back it up."

### 4b. The receiving hospital confirms the bed (2 min)

**Receiving hospital** tab. The red dot is the hospital holding a bed for the
last patient; the tab shows how many are waiting.

- **✓ Bed ready — confirm**: recorded with your name.
- **✗ Can't take this patient** → pick a reason (equipment fault, not cleaned,
  no staff) → **Release the bed and offer the patient to other hospitals**.
  The bed is freed, the other hospitals are asked again with every hospital
  that declined left out, and the patient loses time: **5 minutes** for a
  decline, **15** for no answer (declared assumptions). Switch to **Live
  negotiation**: it opens with *"DECLINED THE BED"*, states the time lost, and
  says whether the second choice took the patient or had filled up.
- The hospital's **record** shows under the list: beds awarded and given back
  this session. Each give-back lowers its score a little next time (the
  **Reliability** column in the ranking): enough to lose a close call, never
  enough to beat a much faster hospital.
- Tick **Pass on automatically if nobody answers in 30 seconds**, run a
  transfer, and wait: the patient moves on by itself, recorded as *no answer*.
  It stops after 3 passes in a row (a coordinator has to phone). Untick it
  before anything else: it applies to every bed held while it is on.

> "An award is a reservation, not a promise. The receiving hospital confirms
> the bed is really ready; if it can't, or doesn't answer, the patient goes
> straight back out. A hospital that keeps giving beds back ranks lower, and
> taking a patient then giving it back doesn't wipe the record: a later
> 'we're full' still has to be justified."

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
| No challenge appears in step 4 | Methodist did not take the acuity-5 patient, or its bed was declined since. Check its ICU is at 100 in Hospital data and it is protective, then run acuity 5 and acuity 4 again from 450076 |
| pgAdmin asks for a password | `mahros-admin-local` |
| Results tab empty | `python experiments/run_all.py 5` then `python experiments/build_dashboard.py` |
| Anything else | `docker compose restart app`. The data is in the database, so nothing is lost |
