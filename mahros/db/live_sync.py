"""Keeps the live negotiation engine and PostgreSQL in step.

The database is the source of truth. The engine is the brain that runs the
Contract Net; before each transfer it reads the current occupancy and hospital
behaviour from the database (so an edit in the Hospital data tab, or by hand in
pgAdmin, takes effect on the very next patient), and after each transfer every
step it took is written back in one transaction.

If that write fails, the engine is reloaded from the database, so the screen
can never show a transfer the database does not have.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.types import ResourceType
from ..hospital.behaviours import POLICIES
from .store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ..server.live import LiveNetwork

#: The engine's own decisions are recorded under this name.
ENGINE_ACTOR = "agent:transfer-engine"

#: Trace step -> (action stored, which payload key names the hospital, agent role)
_STEP_ACTIONS: dict[str, tuple[str, str | None, str]] = {
    "cfp": ("cfp", "origin", "transfer-manager"),
    "challenge": ("challenge", "target", "verifier"),
    "defence": ("defend", "defender", "hospital"),
    "attestation": ("attest", "hospital", "hospital"),
    "burden_objection": ("burden_objection", "objector", "hospital"),
    "overruled": ("overrule", "bidder", "verifier"),
    "deliberation": ("deliberation", None, "verifier"),
    "scored": ("scored", None, "transfer-manager"),
    "awarded": ("award", "winner", "transfer-manager"),
    "failed": ("failed", "origin", "transfer-manager"),
}

#: Fields that are a bidder's private valuation. They exist inside the bidding
#: hospital and are shown on screen to make that point, but they never enter
#: the shared record -- storing them would be the data monopoly the design
#: avoids.
PRIVATE_FIELDS = {"opportunity_cost"}


def age_band(age: int | None) -> str | None:
    if age is None:
        return None
    lo = (int(age) // 10) * 10
    return f"{lo}-{lo + 9}"


def decisions_from_steps(steps: list[dict]) -> list[dict[str, Any]]:
    """Turn the negotiation trace into one row per agent decision."""
    rows: list[dict[str, Any]] = []
    # A challenge's outcome is known only at the defence that answers it.
    outcome: dict[str, str] = {}
    for s in steps:
        if s["kind"] == "defence":
            outcome[s["defender"]] = "upheld" if s.get("upheld") else "struck_out"
    for s in steps:
        kind = s["kind"]
        payload = {k: v for k, v in s.items()
                   if k not in ("kind", "seq") and k not in PRIVATE_FIELDS}
        if kind == "bid":
            hid = s["bidder"]
            rows.append({"agent_id": f"hospital:{hid}", "hospital_id": hid,
                         "action": "propose" if s["feasible"] else "refuse",
                         "reason": s.get("reason") or None, "payload": payload})
            continue
        if kind not in _STEP_ACTIONS:
            continue
        action, key, role = _STEP_ACTIONS[kind]
        hid = s.get(key) if key else None
        agent = f"{role}:{hid}" if role == "hospital" and hid else role
        row = {"agent_id": agent, "hospital_id": hid, "action": action,
               "reason": s.get("reason") or s.get("refusal_reason") or s.get("was"),
               "payload": payload}
        if kind == "challenge":
            row["challenge_outcome"] = outcome.get(hid)
        rows.append(row)
    return rows


class DatabaseSync:
    def __init__(self, store: Store) -> None:
        self.store = store
        epoch = store.current_epoch()
        if epoch is None:
            raise RuntimeError("The database has no live epoch. Run: python -m mahros.db init")
        self.epoch: int = epoch["epoch"]
        self.reference_week: str = str(epoch["reference_week"])
        self.persisted_blocks = 0

    # -- database -> engine ----------------------------------------------- #
    def pull(self, net: "LiveNetwork") -> None:
        """Occupancy and behaviour, as the database has them now."""
        state = self.store.live_state()
        for row in state["pools"]:
            h = net.hospitals.get(row["hospital_id"])
            if h is None:
                continue
            pool = h.resources.pool(ResourceType(row["kind"]))
            if pool is None:
                continue
            if pool.capacity != row["capacity"]:
                raise RuntimeError(
                    f"{row['hospital_id']} {row['kind']}: the engine has capacity "
                    f"{pool.capacity} but the database has {row['capacity']}. The "
                    f"engine and database were built from different weeks.")
            pool.reserved = 0
            pool._reservations.clear()
            pool.occupied = row["occupied"]
        for hid, policy in state["policies"].items():
            h = net.hospitals.get(hid)
            if h is not None and h.policy.name != policy:
                h.policy = POLICIES[policy]()

    def resume(self, net: "LiveNetwork") -> None:
        """Pick up the current session after a restart: ledger, clock, counters."""
        epoch = self.store.current_epoch()
        self.epoch = epoch["epoch"]
        blocks = self.store.ledger_blocks(self.epoch)
        if blocks:
            net.ledger.load_blocks(blocks)
        else:
            self.store.save_blocks(ENGINE_ACTOR, self.epoch, net.ledger.chain[:1])
            blocks = net.ledger.chain[:1]
        self.persisted_blocks = len(blocks)

        hist = self.store.epoch_history(self.epoch)
        if hist["last_sim_minute"] is not None:
            net.now = float(hist["last_sim_minute"]) + net.negotiator.config.bid_window_minutes
        for a in hist["allocations"]:
            net.fairness.record_accept(a["receiver_id"], a["expected_los_minutes"] or 0.0)
            net.fairness.record_send(a["origin_id"])
            if a["receiver_id"] in net.hospitals:
                net.hospitals[a["receiver_id"]].accepted_count += 1
            if a["origin_id"] in net.hospitals:
                net.hospitals[a["origin_id"]].sent_count += 1
            net.reliability.record_award(a["receiver_id"])
        for hid in hist.get("given_back", []):
            # Awarded, then given back: both count towards its give-back rate.
            net.reliability.record_award(hid)
            net.reliability.record_give_back(hid)
        neg = net.negotiator
        for c in hist["counts"]:
            if c["action"] == "challenge":
                neg.total_challenges += c["n"]
            elif c["action"] == "overrule":
                neg.total_overruled += c["n"]
                if c["hospital_id"] in net.hospitals:
                    net.hospitals[c["hospital_id"]].refusals_overruled += c["n"]
            elif c["action"] == "burden_objection":
                neg.total_burden_objections += c["n"]
        net.resumed_transfers = hist["transfers"]
        self.pull(net)

    # -- engine -> database ------------------------------------------------ #
    def record(self, net: "LiveNetwork", actor: str, result: dict, req, patient,
               steps: list[dict]) -> dict[str, Any]:
        net.ledger.flush()
        new_blocks = net.ledger.chain[self.persisted_blocks:]
        block_of = None
        if result["awarded"]:
            agreement_id = result["agreement"]["agreement_id"]
            for b in new_blocks:
                if any(e.get("agreement_id") == agreement_id for e in b.entries):
                    block_of = b.index
        rec = {
            "request": result["request"],
            "awarded": result["awarded"],
            "agreement": result.get("agreement"),
            "failure_reason": result.get("failure_reason"),
            "rounds": result.get("rounds"),
            "peers_contacted": result.get("peers_contacted"),
            "sim_minute": req.created_at,
            "expected_los_minutes": req.expected_los_minutes,
            "age_band": age_band(getattr(patient, "age", None)),
            "decisions": decisions_from_steps(steps),
            "ledger_block": block_of,
        }
        written = self.store.record_transfer(actor, self.epoch, rec, list(new_blocks))
        self.persisted_blocks = len(net.ledger.chain)
        return written
