"""The application's connection to PostgreSQL.

One connection, used one transaction at a time. Every transaction that
changes something names who is acting (`mahros.actor`), and the database's own
triggers turn that into audit rows -- the application never writes the audit
log directly, so it cannot forget to, and it cannot write it wrongly.

The application connects as `mahros_app`, a role that may only change the
editable columns (occupancy, hospital behaviour, discharge) and append records.
Real reference data is read-only to it, and locked by trigger for everyone.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..ledger.interface import Block

ENV_URL = "MAHROS_DATABASE_URL"

#: Kinds the engine knows, in display order.
KINDS = ("icu_bed", "hdu_bed", "ward_bed", "or_slot", "ventilator", "cath_lab")


class DataError(ValueError):
    """A change the data does not allow. The message is shown to the user."""


def plain_url(url: str) -> str:
    """Accept SQLAlchemy-style URLs too (postgresql+psycopg://...)."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def actor_label(name: str | None) -> str:
    """'Rohith V S' -> 'user:Rohith V S'. Agents pass 'agent:...' through."""
    name = (name or "").strip()
    if name.startswith(("agent:", "system:")):
        return name[:80]
    clean = re.sub(r"[^\w .@-]", "", name)[:40].strip() or "reviewer"
    return f"user:{clean}"


def _friendly(exc: Exception) -> str:
    """Turn a database refusal into the sentence the trigger or check wrote."""
    diag = getattr(exc, "diag", None)
    msg = (getattr(diag, "message_primary", None) or str(exc)).strip()
    if "occupied_within_capacity" in msg:
        return ("Occupancy must be between 0 and the pool's real capacity "
                "(the database refused it).")
    if msg.startswith("permission denied"):
        return ("The database refused this change: real data (capacity, "
                "specialties, HHS reports) is locked.")
    return msg


class Store:
    def __init__(self, url: str) -> None:
        self.url = plain_url(url)
        self._conn: psycopg.Connection | None = None
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls) -> "Store | None":
        url = os.environ.get(ENV_URL, "").strip()
        return cls(url) if url else None

    # -- plumbing ---------------------------------------------------------- #
    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = psycopg.connect(self.url, row_factory=dict_row,
                                         connect_timeout=10)
        return self._conn

    @contextmanager
    def tx(self, actor: str | None = None) -> Iterator[psycopg.Cursor]:
        """One transaction. Everything inside commits together or not at all."""
        with self._lock:
            conn = self._connection()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        if actor:
                            cur.execute("SELECT set_config('mahros.actor', %s, true)",
                                        (actor,))
                        yield cur
            except psycopg.errors.CheckViolation as exc:
                raise DataError(_friendly(exc)) from exc
            except psycopg.errors.InsufficientPrivilege as exc:
                raise DataError(_friendly(exc)) from exc
            except psycopg.errors.RaiseException as exc:
                raise DataError(_friendly(exc)) from exc
            except psycopg.OperationalError:
                self.close()
                raise

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def ping(self) -> bool:
        try:
            with self.tx() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    # -- events ------------------------------------------------------------ #
    def event(self, event_type: str, details: dict | None = None,
              level: str = "info", source: str = "app") -> None:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO system_events (level, source, event_type, details) "
                "VALUES (%s, %s, %s, %s)",
                (level, source, event_type, Jsonb(details or {})))

    def audit_action(self, actor: str, action: str, details: dict) -> int:
        """Record a user action that is not itself a row change (a toggle)."""
        with self.tx(actor) as cur:
            cur.execute("SELECT audit_append(%s, NULL, NULL, NULL, %s) AS id",
                        (action, Jsonb(details)))
            return cur.fetchone()["id"]

    # -- epoch ------------------------------------------------------------- #
    def current_epoch(self) -> dict | None:
        with self.tx() as cur:
            cur.execute("SELECT * FROM live_epochs ORDER BY epoch DESC LIMIT 1")
            return cur.fetchone()

    # -- live state -------------------------------------------------------- #
    def live_state(self) -> dict[str, Any]:
        """Occupancy per pool and behaviour per hospital: what the engine runs on."""
        with self.tx() as cur:
            cur.execute("SELECT hospital_id, kind, capacity, occupied FROM resources")
            pools = cur.fetchall()
            cur.execute("SELECT hospital_id, policy FROM hospital_settings")
            policies = {r["hospital_id"]: r["policy"] for r in cur.fetchall()}
        return {"pools": pools, "policies": policies}

    def hospitals_view(self) -> list[dict[str, Any]]:
        """Everything the Hospital data tab shows, one entry per hospital."""
        with self.tx() as cur:
            cur.execute("""
                SELECT h.hospital_id, h.name, h.city, h.tier, h.lat, h.lon,
                       s.policy
                FROM hospitals h JOIN hospital_settings s USING (hospital_id)
                ORDER BY h.tier DESC, h.name""")
            hospitals = cur.fetchall()
            cur.execute("""
                SELECT r.*, coalesce(a.active, 0) AS transferred_in
                FROM resources r
                LEFT JOIN (SELECT resource_id, count(*) AS active FROM admissions
                           WHERE discharged_at IS NULL GROUP BY resource_id) a
                  USING (resource_id)""")
            pools = cur.fetchall()
            cur.execute("SELECT hospital_id, specialty, source FROM hospital_capabilities "
                        "ORDER BY specialty")
            caps = cur.fetchall()
        by_h: dict[str, dict] = {h["hospital_id"]: {**h, "pools": [], "specialties": []}
                                 for h in hospitals}
        order = {k: i for i, k in enumerate(KINDS)}
        for p in sorted(pools, key=lambda p: order.get(p["kind"], 99)):
            if p["hospital_id"] in by_h:
                by_h[p["hospital_id"]]["pools"].append({
                    **p, "edited": p["occupied"] != p["default_occupied"]})
        for c in caps:
            if c["hospital_id"] in by_h:
                by_h[c["hospital_id"]]["specialties"].append(c["specialty"])
        out = []
        for h in by_h.values():
            h["edited"] = h["policy"] != "honest" or any(p["edited"] for p in h["pools"])
            out.append(h)
        return out

    def update_hospital(self, actor: str, hospital_id: str,
                        occupied: dict[str, int] | None = None,
                        policy: str | None = None) -> None:
        """Change what a person may change. Real data is refused by the database."""
        with self.tx(actor) as cur:
            cur.execute("SELECT 1 FROM hospitals WHERE hospital_id = %s", (hospital_id,))
            if cur.fetchone() is None:
                raise DataError(f"No hospital with id {hospital_id}.")
            for kind, value in (occupied or {}).items():
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise DataError(f"{kind}: occupancy must be a whole number.") from None
                cur.execute("""
                    SELECT r.resource_id, r.capacity,
                           (SELECT count(*) FROM admissions a
                            WHERE a.resource_id = r.resource_id
                              AND a.discharged_at IS NULL) AS active
                    FROM resources r WHERE r.hospital_id = %s AND r.kind = %s""",
                            (hospital_id, kind))
                row = cur.fetchone()
                if row is None:
                    raise DataError(f"This hospital has no {kind.replace('_', ' ')} pool.")
                if not 0 <= value <= row["capacity"]:
                    raise DataError(
                        f"{kind.replace('_', ' ')}: {value} is outside 0 to "
                        f"{row['capacity']} (the real capacity).")
                if value < row["active"]:
                    raise DataError(
                        f"{kind.replace('_', ' ')}: {row['active']} patient(s) transferred in "
                        f"this session are still in these beds, so occupancy cannot go "
                        f"below {row['active']}.")
                cur.execute("UPDATE resources SET occupied = %s WHERE resource_id = %s",
                            (value, row["resource_id"]))
            if policy is not None:
                cur.execute("UPDATE hospital_settings SET policy = %s WHERE hospital_id = %s",
                            (policy, hospital_id))

    def set_network_load(self, actor: str, level: float) -> None:
        """Set every pool to `level` of its capacity (never below patients in it)."""
        level = max(0.0, min(1.0, float(level)))
        with self.tx(actor) as cur:
            cur.execute("SELECT audit_append('SET_NETWORK_LOAD', NULL, NULL, NULL, %s)",
                        (Jsonb({"level": level}),))
            cur.execute("""
                UPDATE resources r SET occupied = GREATEST(
                    round(r.capacity * %s)::int,
                    (SELECT count(*) FROM admissions a
                     WHERE a.resource_id = r.resource_id AND a.discharged_at IS NULL))
                WHERE r.occupied IS DISTINCT FROM GREATEST(
                    round(r.capacity * %s)::int,
                    (SELECT count(*) FROM admissions a
                     WHERE a.resource_id = r.resource_id AND a.discharged_at IS NULL))""",
                        (level, level))

    def restore(self, actor: str, hospital_id: str | None = None) -> dict:
        """Put real data back. Never deletes history; a full restore starts a new epoch.

        Patients transferred in during the session are discharged (recorded),
        occupancy returns to what each hospital reported in the reference week,
        and every hospital is honest again.
        """
        with self.tx(actor) as cur:
            where, args = ("", ()) if hospital_id is None else (
                " AND hospital_id = %s", (hospital_id,))
            cur.execute("SELECT audit_append('RESTORE_DEFAULTS', NULL, %s, NULL, %s)",
                        (hospital_id, Jsonb({"scope": hospital_id or "all hospitals"})))
            cur.execute("UPDATE admissions SET discharged_at = now(), "
                        "discharge_reason = 'restore defaults' "
                        "WHERE discharged_at IS NULL" + where, args)
            discharged = cur.rowcount
            cur.execute("UPDATE resources SET occupied = default_occupied "
                        "WHERE occupied <> default_occupied" + where, args)
            pools = cur.rowcount
            cur.execute("UPDATE hospital_settings SET policy = 'honest' "
                        "WHERE policy <> 'honest'" + where, args)
            policies = cur.rowcount
            epoch = None
            if hospital_id is None:
                cur.execute("""
                    INSERT INTO live_epochs (started_by, reason, metro, reference_week)
                    SELECT %s, 'restore defaults', metro, reference_week
                    FROM live_epochs ORDER BY epoch DESC LIMIT 1
                    RETURNING epoch""", (actor,))
                epoch = cur.fetchone()["epoch"]
        return {"discharged": discharged, "pools_reset": pools,
                "policies_reset": policies, "new_epoch": epoch}

    # -- ledger ------------------------------------------------------------ #
    def ledger_blocks(self, epoch: int) -> list[Block]:
        with self.tx() as cur:
            cur.execute("SELECT * FROM ledger_blocks WHERE epoch = %s "
                        "ORDER BY block_index", (epoch,))
            heads = cur.fetchall()
            cur.execute("SELECT block_index, payload FROM ledger_entries "
                        "WHERE epoch = %s ORDER BY block_index, position", (epoch,))
            entries: dict[int, list] = {}
            for r in cur.fetchall():
                entries.setdefault(r["block_index"], []).append(r["payload"])
        return [Block(index=h["block_index"], timestamp=h["block_ts"],
                      prev_hash=h["prev_hash"], entries=entries.get(h["block_index"], []),
                      merkle_root=h["merkle_root"], hash=h["block_hash"])
                for h in heads]

    @staticmethod
    def _insert_blocks(cur, epoch: int, blocks: list[Block]) -> None:
        for b in blocks:
            cur.execute("""
                INSERT INTO ledger_blocks (epoch, block_index, block_ts, prev_hash,
                                           merkle_root, block_hash, n_entries)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (epoch, b.index, b.timestamp, b.prev_hash, b.merkle_root,
                         b.hash, len(b.entries)))
            for pos, e in enumerate(b.entries):
                cur.execute("INSERT INTO ledger_entries (epoch, block_index, position, "
                            "payload) VALUES (%s, %s, %s, %s)",
                            (epoch, b.index, pos, Jsonb(e)))

    def save_blocks(self, actor: str, epoch: int, blocks: list[Block]) -> None:
        with self.tx(actor) as cur:
            self._insert_blocks(cur, epoch, blocks)

    def verify_ledger(self, epoch: int) -> dict[str, Any]:
        """Recompute every block from what is stored. Any edit in pgAdmin shows."""
        blocks = self.ledger_blocks(epoch)
        errors = []
        for i, b in enumerate(blocks):
            if b.index != i:
                errors.append(f"block {i}: missing (found block {b.index} in its place)")
                break
            if b.compute_merkle_root() != b.merkle_root:
                errors.append(f"block {i}: an entry was changed (Merkle root mismatch)")
            if b.compute_hash() != b.hash:
                errors.append(f"block {i}: the block header was changed")
            if i > 0 and b.prev_hash != blocks[i - 1].hash:
                errors.append(f"block {i}: link to block {i - 1} is broken")
        return {"valid": not errors, "epoch": epoch, "blocks": len(blocks),
                "entries": sum(len(b.entries) for b in blocks), "errors": errors}

    # -- transfers --------------------------------------------------------- #
    def record_transfer(self, actor: str, epoch: int, rec: dict[str, Any],
                        new_blocks: list[Block]) -> dict[str, Any]:
        """Write one finished negotiation, all of it, in one transaction."""
        req = rec["request"]
        with self.tx(actor) as cur:
            patient_id = uuid.uuid4()
            cur.execute("INSERT INTO patients (patient_id, pseudonym, age_band) "
                        "VALUES (%s, %s, %s)",
                        (patient_id, req["patient_ref"], rec.get("age_band")))
            cur.execute("""
                INSERT INTO transfer_requests (request_id, epoch, patient_id, origin_id,
                    resource_kind, specialty, acuity, window_minutes, sim_minute,
                    outcome, failure_reason, rounds, peers_contacted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (req["request_id"], epoch, patient_id, req["origin"],
                         req["resource"], req["specialty"], req["acuity"],
                         req["window_min"], rec["sim_minute"],
                         "awarded" if rec["awarded"] else "failed",
                         rec.get("failure_reason"), rec.get("rounds"),
                         rec.get("peers_contacted")))

            refusal_ids: dict[str, int] = {}
            open_challenge: dict[str, int] = {}
            for seq, d in enumerate(rec["decisions"]):
                cur.execute("""
                    INSERT INTO agent_decisions (request_id, seq, agent_id, hospital_id,
                                                 action, reason, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING decision_id""",
                            (req["request_id"], seq, d["agent_id"], d.get("hospital_id"),
                             d["action"], d.get("reason"), Jsonb(d["payload"])))
                did = cur.fetchone()["decision_id"]
                if d["action"] == "refuse":
                    refusal_ids[d["hospital_id"]] = did
                elif d["action"] == "challenge":
                    c = d["payload"]
                    cur.execute("""
                        INSERT INTO challenges (request_id, refusal_decision_id, target_id,
                            refusal_reason, claim, evidence, outcome)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING challenge_id""",
                                (req["request_id"], refusal_ids.get(d["hospital_id"]),
                                 d["hospital_id"], c.get("refusal_reason", ""),
                                 c.get("claim", ""), Jsonb(c.get("evidence", [])),
                                 d.get("challenge_outcome")))
                    open_challenge[d["hospital_id"]] = cur.fetchone()["challenge_id"]

            self._insert_blocks(cur, epoch, new_blocks)

            out: dict[str, Any] = {"patient_id": str(patient_id)}
            if rec["awarded"]:
                a = rec["agreement"]
                cur.execute("SELECT resource_id FROM resources "
                            "WHERE hospital_id = %s AND kind = %s",
                            (a["receiver"], req["resource"]))
                resource_id = cur.fetchone()["resource_id"]
                # The bed is taken. The CHECK constraint refuses an overfull pool.
                cur.execute("UPDATE resources SET occupied = occupied + 1 "
                            "WHERE resource_id = %s", (resource_id,))
                admission_id = uuid.uuid4()
                cur.execute("""
                    INSERT INTO admissions (admission_id, patient_id, hospital_id,
                        resource_id, request_id, epoch, admitted_sim_minute,
                        expected_los_minutes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            (admission_id, patient_id, a["receiver"], resource_id,
                             req["request_id"], epoch, rec["sim_minute"],
                             rec.get("expected_los_minutes")))
                cur.execute("""
                    INSERT INTO allocations (allocation_id, request_id, receiver_id,
                        admission_id, decided_by, time_to_care_minutes, slack_minutes,
                        contested, rationale, terms_hash, ledger_epoch, ledger_block)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (a["agreement_id"], req["request_id"], a["receiver"],
                             admission_id, a["decided_by"], a.get("time_to_care"),
                             a.get("slack_minutes"), bool(a.get("contested")),
                             a.get("rationale"), a["terms_hash"], epoch,
                             rec.get("ledger_block")))
                out["admission_id"] = str(admission_id)
        return out

    def transfers(self, limit: int = 30) -> list[dict]:
        with self.tx() as cur:
            cur.execute("""
                SELECT t.request_id, t.epoch, t.origin_id, o.name AS origin_name,
                       t.resource_kind, t.specialty, t.acuity, t.outcome,
                       t.failure_reason, t.created_at,
                       a.receiver_id, r.name AS receiver_name,
                       (SELECT count(*) FROM challenges c
                        WHERE c.request_id = t.request_id) AS challenges
                FROM transfer_requests t
                JOIN hospitals o ON o.hospital_id = t.origin_id
                LEFT JOIN allocations a ON a.request_id = t.request_id
                LEFT JOIN hospitals r ON r.hospital_id = a.receiver_id
                ORDER BY t.created_at DESC LIMIT %s""", (limit,))
            return cur.fetchall()

    def trace(self, request_id: str) -> dict | None:
        """One transfer from start to finish, every table that recorded it."""
        with self.tx() as cur:
            cur.execute("SELECT * FROM transfer_requests WHERE request_id = %s",
                        (request_id,))
            req = cur.fetchone()
            if req is None:
                return None
            cur.execute("SELECT * FROM agent_decisions WHERE request_id = %s "
                        "ORDER BY seq", (request_id,))
            decisions = cur.fetchall()
            cur.execute("SELECT * FROM challenges WHERE request_id = %s "
                        "ORDER BY challenge_id", (request_id,))
            challenges = cur.fetchall()
            cur.execute("SELECT * FROM allocations WHERE request_id = %s", (request_id,))
            allocation = cur.fetchone()
            cur.execute("SELECT * FROM admissions WHERE request_id = %s", (request_id,))
            admission = cur.fetchone()
            block = None
            if allocation and allocation["ledger_block"] is not None:
                cur.execute("SELECT * FROM ledger_blocks WHERE epoch = %s "
                            "AND block_index = %s",
                            (allocation["ledger_epoch"], allocation["ledger_block"]))
                block = cur.fetchone()
            pks = [("transfer_requests", request_id)]
            pks += [("agent_decisions", str(d["decision_id"])) for d in decisions]
            pks += [("challenges", str(c["challenge_id"])) for c in challenges]
            if allocation:
                pks.append(("allocations", allocation["allocation_id"]))
            if admission:
                pks.append(("admissions", str(admission["admission_id"])))
            cur.execute("""
                SELECT audit_id, ts, actor, action, table_name, row_pk, row_hash
                FROM audit_log
                WHERE (table_name, row_pk) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
                ORDER BY audit_id""", ([t for t, _ in pks], [p for _, p in pks]))
            audit = cur.fetchall()
        return {"request": req, "decisions": decisions, "challenges": challenges,
                "allocation": allocation, "admission": admission,
                "ledger_block": block, "audit": audit}

    def epoch_history(self, epoch: int) -> dict[str, Any]:
        """What the engine needs to resume a session after a restart."""
        with self.tx() as cur:
            cur.execute("""
                SELECT a.receiver_id, t.origin_id, ad.expected_los_minutes
                FROM allocations a
                JOIN transfer_requests t USING (request_id)
                LEFT JOIN admissions ad ON ad.admission_id = a.admission_id
                WHERE t.epoch = %s ORDER BY t.created_at""", (epoch,))
            allocations = cur.fetchall()
            cur.execute("""
                SELECT d.action, d.hospital_id, count(*) AS n
                FROM agent_decisions d JOIN transfer_requests t USING (request_id)
                WHERE t.epoch = %s
                  AND d.action IN ('challenge', 'overrule', 'burden_objection')
                GROUP BY d.action, d.hospital_id""", (epoch,))
            counts = cur.fetchall()
            cur.execute("SELECT count(*) AS n, max(sim_minute) AS last "
                        "FROM transfer_requests WHERE epoch = %s", (epoch,))
            totals = cur.fetchone()
        return {"allocations": allocations, "counts": counts,
                "transfers": totals["n"], "last_sim_minute": totals["last"]}

    # -- Regional Monitor: where the network is running out ---------------- #
    def bottlenecks(self) -> dict[str, Any]:
        """What this session's transfers and the live occupancy say is scarce.

        Plain counting, no model: which needs went unmet and why, which pools
        are fullest network-wide, and which hospitals refused most. A
        coordinator reads this to decide where to add capacity first.
        """
        with self.tx() as cur:
            cur.execute("SELECT epoch FROM live_epochs ORDER BY epoch DESC LIMIT 1")
            epoch = cur.fetchone()["epoch"]
            cur.execute("""
                SELECT resource_kind, specialty,
                       count(*) AS requests,
                       count(*) FILTER (WHERE outcome = 'failed') AS failed
                FROM transfer_requests WHERE epoch = %s
                GROUP BY resource_kind, specialty
                ORDER BY failed DESC, requests DESC""", (epoch,))
            needs = cur.fetchall()
            cur.execute("""
                SELECT failure_reason, count(*) AS n FROM transfer_requests
                WHERE epoch = %s AND outcome = 'failed'
                GROUP BY failure_reason ORDER BY n DESC""", (epoch,))
            reasons = cur.fetchall()
            cur.execute("""
                SELECT kind, sum(capacity) AS capacity, sum(occupied) AS occupied,
                       count(*) FILTER (WHERE capacity > 0 AND occupied >= capacity) AS full_hospitals,
                       count(*) FILTER (WHERE capacity > 0) AS hospitals
                FROM resources GROUP BY kind""")
            pools = cur.fetchall()
            cur.execute("""
                SELECT d.hospital_id, h.name, count(*) AS refusals,
                       count(*) FILTER (WHERE d.reason = 'at_self_protection_reserve') AS said_full
                FROM agent_decisions d
                JOIN transfer_requests t USING (request_id)
                JOIN hospitals h ON h.hospital_id = d.hospital_id
                WHERE t.epoch = %s AND d.action = 'refuse'
                GROUP BY d.hospital_id, h.name ORDER BY refusals DESC LIMIT 5""", (epoch,))
            refusers = cur.fetchall()
        for p in pools:
            p["occupancy"] = (p["occupied"] / p["capacity"]) if p["capacity"] else None
        pools.sort(key=lambda p: -(p["occupancy"] or 0))
        total = sum(n["requests"] for n in needs)
        failed = sum(n["failed"] for n in needs)
        headline = None
        worst = next((n for n in needs if n["failed"]), None)
        if worst:
            headline = (f"{worst['failed']} of {failed} unplaced patients needed "
                        f"{worst['resource_kind'].replace('_', ' ')} ({worst['specialty']}).")
        return {"epoch": epoch, "requests": total, "failed": failed, "headline": headline,
                "needs": needs, "reasons": reasons, "pools": pools, "refusers": refusers}

    # -- Data Integrity Agent findings on the real reports ----------------- #
    def flags_summary(self) -> dict[str, Any]:
        with self.tx() as cur:
            cur.execute("SELECT count(*) AS n FROM hhs_weekly_reports "
                        "WHERE inpatient_beds IS NOT NULL")
            reports = cur.fetchone()["n"]
            cur.execute("""
                SELECT h.hospital_id, h.name,
                       count(*) FILTER (WHERE f.severity = 'red')   AS red,
                       count(*) FILTER (WHERE f.severity = 'amber') AS amber
                FROM hospitals h LEFT JOIN report_flags f USING (hospital_id)
                GROUP BY h.hospital_id, h.name ORDER BY red DESC, amber DESC, h.name""")
            per_hospital = cur.fetchall()
            cur.execute("SELECT rule, severity, count(*) AS n FROM report_flags "
                        "GROUP BY rule, severity ORDER BY n DESC")
            by_rule = cur.fetchall()
        red = sum(h["red"] for h in per_hospital)
        amber = sum(h["amber"] for h in per_hospital)
        return {"reports_checked": reports, "red": red, "amber": amber,
                "hospitals_with_red": sum(1 for h in per_hospital if h["red"]),
                "hospitals": len(per_hospital), "by_rule": by_rule,
                "per_hospital": per_hospital}

    def hospital_flags(self, hospital_id: str) -> list[dict]:
        with self.tx() as cur:
            cur.execute("""
                SELECT f.week_start, f.rule, f.severity, f.detail,
                       r.inpatient_beds, r.inpatient_used, r.icu_beds, r.icu_used
                FROM report_flags f
                JOIN hhs_weekly_reports r USING (hospital_id, week_start)
                WHERE f.hospital_id = %s ORDER BY f.week_start""", (hospital_id,))
            return cur.fetchall()

    # -- audit ------------------------------------------------------------- #
    def audit_rows(self, limit: int = 100, table: str | None = None,
                   actor: str | None = None, row_pk: str | None = None) -> list[dict]:
        clauses, args = [], []
        for col, val in (("table_name", table), ("actor", actor), ("row_pk", row_pk)):
            if val:
                clauses.append(f"{col} = %s")
                args.append(val)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.tx() as cur:
            cur.execute(f"SELECT * FROM audit_log{where} ORDER BY audit_id DESC LIMIT %s",
                        (*args, max(1, min(int(limit), 1000))))
            return cur.fetchall()

    def hospital_history(self, hospital_id: str, limit: int = 100) -> list[dict]:
        """Every change to one hospital's live data: its pools and its behaviour."""
        with self.tx() as cur:
            cur.execute("SELECT resource_id::text AS pk FROM resources "
                        "WHERE hospital_id = %s", (hospital_id,))
            pools = [r["pk"] for r in cur.fetchall()]
            cur.execute("""
                SELECT audit_id, ts, actor, action, table_name, row_pk, before, after
                FROM audit_log
                WHERE (table_name = 'resources' AND row_pk = ANY(%s))
                   OR (table_name = 'hospital_settings' AND row_pk = %s)
                   OR (action = 'RESTORE_DEFAULTS' AND (row_pk IS NULL
                                                        OR row_pk = %s))
                ORDER BY audit_id DESC LIMIT %s""",
                        (pools, hospital_id, hospital_id, limit))
            return cur.fetchall()

    def audit_verify(self) -> dict[str, Any]:
        with self.tx() as cur:
            cur.execute("SELECT * FROM audit_verify()")
            r = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM audit_checkpoints")
            n_cp = cur.fetchone()["n"]
        return {"valid": r["ok"], "rows_checked": r["rows_checked"],
                "first_bad_id": r["first_bad_id"], "problem": r["problem"],
                "checkpoints": n_cp}

    def audit_checkpoint(self, actor: str) -> dict[str, Any]:
        with self.tx(actor) as cur:
            cur.execute("SELECT * FROM audit_checkpoint()")
            return cur.fetchone()

    def status(self) -> dict[str, Any]:
        with self.tx() as cur:
            counts = {}
            for t in ("hospitals", "hhs_weekly_reports", "resources", "transfer_requests",
                      "agent_decisions", "allocations", "admissions", "ledger_blocks",
                      "audit_log", "report_flags"):
                cur.execute(f"SELECT count(*) AS n FROM {t}")
                counts[t] = cur.fetchone()["n"]
            cur.execute("SELECT * FROM live_epochs ORDER BY epoch DESC LIMIT 1")
            epoch = cur.fetchone()
        return {"connected": True, "counts": counts, "epoch": epoch}


def to_jsonable(obj: Any) -> Any:
    """Rows contain UUIDs, datetimes and Decimals; make them JSON-safe."""
    return json.loads(json.dumps(obj, default=str))
