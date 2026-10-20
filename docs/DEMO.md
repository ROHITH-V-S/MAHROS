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

### 4. Catch a false refusal (2.5 min)

Live tab. Origin **450076 (MD Anderson)**, **ICU bed / general / acuity 5**.
Run it. Houston Methodist takes the patient (it now has free ICU beds).

In **Are they telling the truth?**, switch **450358** to **protective**.

> "Accepting a transfer costs a bed. The cheapest way to avoid one is to say
> you are full. In an ordinary auction nobody can check."

Same origin, **ICU bed / general / acuity 4**. Run it. Point at:
- Methodist: *"we're down to our last one"*
- the **challenge**: *"it accepted a different ICU transfer 1 minute ago. It
  had one to give."*
- the defence failing, **refusal struck out**, the argument map

> "Nobody revealed a bed count. The claim was checked against what Methodist
> itself agreed to a minute ago, on a record it cannot edit."

Untick **Check refusals against the shared record** and run the acuity-4 case
again: the same refusal is taken at face value. Tick it again.

(Methodist never lies about an acuity-5 patient. That is a rule in the model,
not luck.)

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
| No challenge appears in step 4 | Methodist did not take the acuity-5 patient. Check its ICU is at 100 in Hospital data, run step 4 again from origin 450076 |
| pgAdmin asks for a password | `mahros-admin-local` |
| Results tab empty | `python experiments/run_all.py 5` then `python experiments/build_dashboard.py` |
| Anything else | `docker compose restart app`. The data is in the database, so nothing is lost |
