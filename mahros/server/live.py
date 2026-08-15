"""A live, interactive MAHROS network for the demo server.

This is the *same engine* the batch experiments use -- the same Hospital,
ResourceAgent, ContractNetNegotiator, FairnessLedger and HashChainLedger
objects. Nothing here reimplements the negotiation; it wraps it so a human can
drive it one request at a time and watch each protocol step.

Differences from the batch runner, all deliberate:

  * time advances on demand rather than through an event queue, so a person can
    pause between steps and read what happened
  * the negotiator carries an observer that records every phase
  * load can be injected directly, so you can push a hospital to the brink and
    watch the network's answer change
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.types import (
    Acuity,
    DemographicGroup,
    Patient,
    ResourceType,
    Specialty,
    TransferRequest,
)
from ..fairness.metrics import FairnessLedger, equity_report
from ..hospital.hospital import Hospital
from ..ledger.interface import HashChainLedger
from ..llm.coordinator import LLMCoordinator
from ..negotiation.cnp import CNPConfig, ContractNetNegotiator
from ..negotiation.messages import MessageBus
from ..negotiation.scoring import ScoringWeights
from ..privacy.anonymizer import PrivacyAudit, build_anonymizer
from ..sim.scenario import (
    SCENARIOS,
    PatientGenerator,
    ScenarioConfig,
    build_network,
    travel_time_matrix,
)


@dataclass
class TraceEvent:
    seq: int
    kind: str
    payload: dict[str, Any]
    at: float = field(default_factory=time.time)


class LiveNetwork:
    """A running hospital network you can poke at."""

    def __init__(
        self,
        scenario: str = "baseline",
        seed: int = 42,
        fairness_enabled: bool = True,
        llm_enabled: bool = False,
    ) -> None:
        self.scenario_name = scenario
        self.seed = seed
        self.rng = random.Random(seed)

        cfg: ScenarioConfig = SCENARIOS[scenario]
        self.scenario = cfg
        hospital_cfgs = build_network(cfg)

        self.now: float = 8 * 60.0          # start the clock at 08:00
        self.hospitals: dict[str, Hospital] = {
            hc.hospital_id: Hospital(hc, self) for hc in hospital_cfgs
        }
        self._tt = travel_time_matrix(hospital_cfgs, cfg.ambulance_speed_kmh)

        weights = {
            h.hospital_id: float(sum(n for r, n in h.capacities.items() if r.is_critical))
            for h in hospital_cfgs
        }
        self.fairness = FairnessLedger(list(self.hospitals), window=100,
                                       capacity_weights=weights)
        self.privacy = PrivacyAudit(build_anonymizer(prefer_presidio=False))
        self.bus = MessageBus()
        self.ledger = HashChainLedger(block_size=8)
        self.coordinator = LLMCoordinator() if llm_enabled else None
        self.fairness_enabled = fairness_enabled

        self.gen = PatientGenerator(cfg)
        self.history: list[dict] = []        # completed negotiations
        self._trace: list[TraceEvent] = []
        self._seq = 0

        self.negotiator = ContractNetNegotiator(
            hospitals=self.hospitals,
            travel_time=self.travel_time,
            bus=self.bus,
            fairness=self.fairness,
            privacy=self.privacy,
            weights=ScoringWeights(fairness_enabled=fairness_enabled),
            config=CNPConfig(),
            coordinator=self.coordinator,
            ledger=self.ledger,
            observer=self._observe,
        )

    # -- glue expected by Hospital ----------------------------------------- #
    @property
    def sim(self):
        return self

    def travel_time(self, a: str, b: str) -> float:
        return self._tt.get((a, b), 999.0)

    # -- trace -------------------------------------------------------------- #
    def _observe(self, kind: str, payload: dict) -> None:
        self._seq += 1
        self._trace.append(TraceEvent(self._seq, kind, payload))

    def _drain_trace(self) -> list[dict]:
        out = [{"seq": e.seq, "kind": e.kind, **e.payload} for e in self._trace]
        self._trace = []
        return out

    # -- load ---------------------------------------------------------------- #
    def seed_load(self, level: float = 0.6) -> dict:
        """Fill the network to roughly `level` occupancy so bids are meaningful.

        An empty network makes every negotiation trivial -- everyone bids, the
        nearest wins, and the demo shows nothing. Pressure is what makes the
        protocol interesting.
        """
        level = max(0.0, min(0.98, level))
        filled = 0
        for h in self.hospitals.values():
            for res, pool in h.resources.pools.items():
                pool.occupied = 0
                pool.reserved = 0
                pool._reservations.clear()
                target = int(round(pool.capacity * level * self.rng.uniform(0.75, 1.2)))
                target = min(target, pool.capacity)
                pool.occupied = target
                filled += target
        return {"occupied_units": filled, "level": level}

    def set_occupancy(self, hospital_id: str, resource: str, occupied: int) -> dict:
        """Directly set one pool's occupancy -- the 'what if' control."""
        h = self.hospitals[hospital_id]
        pool = h.resources.pool(ResourceType(resource))
        if pool is None:
            raise KeyError(f"{hospital_id} does not offer {resource}")
        pool.occupied = max(0, min(int(occupied), pool.capacity))
        return self.hospital_state(hospital_id)

    def advance(self, minutes: float) -> None:
        self.now += float(minutes)

    # -- the main action ----------------------------------------------------- #
    def request_transfer(
        self,
        origin: str,
        resource: str,
        specialty: str,
        acuity: int,
        patient_name: str = "",
        patient_note: str = "",
    ) -> dict:
        """Run one full Contract Net negotiation and return every step of it."""
        if origin not in self.hospitals:
            raise KeyError(f"unknown hospital {origin}")

        res = ResourceType(resource)
        spec = Specialty(specialty)
        ac = Acuity(int(acuity))

        # Build a real patient so the privacy layer has something to do. The
        # note is free text and is scrubbed before anything crosses a boundary.
        patient = Patient(
            name=patient_name or "Unnamed Patient",
            mrn=f"{origin}-{self.rng.randint(100000, 999999)}",
            phone=f"+91 98{self.rng.randint(10**7, 10**8 - 1)}",
            age=self.rng.randint(1, 92),
            group=self.gen.demographic(),
            home_hospital=origin,
            notes=patient_note or "",
        )
        token = self.privacy.anonymizer.pseudonymize(patient, origin)

        los = self._plausible_los(res, spec)
        req = TransferRequest(
            origin=origin,
            patient_ref=token,
            resource=res,
            specialty=spec,
            acuity=ac,
            created_at=self.now,
            expires_at=self.now + ac.safe_window_minutes,
            expected_los_minutes=los,
            group=patient.group,
        )

        self._trace = []
        before_blocks = len(self.ledger.chain)
        outcome = self.negotiator.run(req, self.now)
        steps = self._drain_trace()

        # Realistic clock cost of running the protocol.
        self.advance(self.negotiator.config.bid_window_minutes * outcome.rounds)

        result = {
            "request": {
                "request_id": req.request_id,
                "origin": origin,
                "origin_name": self.hospitals[origin].name,
                "resource": res.value,
                "specialty": spec.value,
                "acuity": int(ac),
                "acuity_name": ac.name,
                "window_min": ac.safe_window_minutes,
                "expected_los_hours": round(los / 60.0, 1),
                "patient_ref": token,
            },
            "privacy": {
                "internal_note": patient.notes,
                "scrubbed_note": self.privacy.anonymizer.scrub(patient.notes),
                "crosses_boundary": req.public_view(),
                "withheld": sorted(
                    ["name", "mrn", "phone", "age", "group"]
                ),
            },
            "steps": steps,
            "awarded": outcome.agreement is not None,
            "rounds": outcome.rounds,
            "peers_contacted": req.n_peers_contacted,
            "feasible_bids": req.n_bids_received,
        }

        if outcome.agreement is not None:
            a = outcome.agreement
            result["agreement"] = {
                "agreement_id": a.agreement_id,
                "receiver": a.receiver,
                "receiver_name": self.hospitals[a.receiver].name,
                "terms_hash": a.terms_hash,
                "rationale": a.rationale,
                "decided_by": a.decided_by,
                "care_starts_in_min": round(a.promised_care_start - req.created_at, 1),
                "sealed_block": len(self.ledger.chain) > before_blocks,
            }
            # The patient actually arrives and occupies the bed.
            self.hospitals[a.receiver].receive_patient(req)
        else:
            result["failure_reason"] = req.failure_reason

        self.history.append({
            "request_id": req.request_id,
            "origin": origin,
            "receiver": outcome.agreement.receiver if outcome.agreement else None,
            "resource": res.value,
            "acuity": int(ac),
            "awarded": outcome.agreement is not None,
            "at": self.now,
        })
        return result

    def _plausible_los(self, resource: ResourceType, specialty: Specialty) -> float:
        """Length of stay matching what the user actually asked for.

        Drawing a random escalation profile here would hand an ICU request a
        three-hour cath-lab stay, which is the sort of detail an audience
        notices and rightly stops trusting.
        """
        from ..sim.scenario import _ESCALATION_PROFILES

        exact = [p for p in _ESCALATION_PROFILES
                 if p[0] is resource and p[1] is specialty]
        same_resource = [p for p in _ESCALATION_PROFILES if p[0] is resource]
        pool = exact or same_resource
        if not pool:
            return 1440.0
        mean_los = self.rng.choice(pool)[3]
        import math
        return max(60.0, self.rng.lognormvariate(math.log(mean_los), 0.45))

    def auto_request(self) -> dict:
        """Generate a clinically plausible escalation at a random hospital."""
        origin = self.rng.choice(list(self.hospitals))
        resource, specialty, acuity, _ = self.gen.escalation_profile()
        patient = self.gen.make_patient(origin)
        return self.request_transfer(
            origin=origin,
            resource=resource.value,
            specialty=specialty.value,
            acuity=int(acuity),
            patient_name=patient.name,
            patient_note=patient.notes,
        )

    # -- state views --------------------------------------------------------- #
    def hospital_state(self, hid: str) -> dict:
        h = self.hospitals[hid]
        return {
            "id": hid,
            "name": h.name,
            "tier": h.tier,
            "x": round(h.cfg.x, 2),
            "y": round(h.cfg.y, 2),
            "specialties": sorted(s.value for s in h.specialties),
            "strain": round(h.resources.overall_strain(), 3),
            "accepted": h.accepted_count,
            "sent": h.sent_count,
            "fairness_credit": round(self.fairness.credit(hid), 3),
            "pools": [
                {
                    "resource": r.value,
                    "capacity": p.capacity,
                    "occupied": p.occupied,
                    "reserved": p.reserved,
                    "available": p.available,
                    "strain": round(p.strain, 3),
                }
                for r, p in h.resources.pools.items()
            ],
        }

    def state(self) -> dict:
        hosps = [self.hospital_state(h) for h in self.hospitals]
        awarded = sum(1 for x in self.history if x["awarded"])
        return {
            "scenario": self.scenario_name,
            "seed": self.seed,
            "now_minutes": round(self.now, 1),
            "clock": self._clock_label(),
            "fairness_enabled": self.fairness_enabled,
            "llm_enabled": self.coordinator is not None,
            "hospitals": hosps,
            "travel_minutes": {
                f"{a}|{b}": round(v, 1) for (a, b), v in self._tt.items() if a != b
            },
            "fairness": self.fairness.summary(),
            "totals": {
                "negotiations": len(self.history),
                "awarded": awarded,
                "failed": len(self.history) - awarded,
                "messages": self.bus.message_count,
                "boundary_crossings": self.privacy.crossings,
                "privacy_violations": len(self.privacy.violations),
            },
            "ledger": self.ledger_state(),
            "decentralisation": self.bus.audit_no_global_view(set(self.hospitals)),
            "resources": [r.value for r in ResourceType],
            "specialties": [s.value for s in Specialty],
        }

    def _clock_label(self) -> str:
        total = int(self.now)
        day = total // 1440
        hh = (total % 1440) // 60
        mm = total % 60
        return f"day {day + 1}  {hh:02d}:{mm:02d}"

    def ledger_state(self) -> dict:
        v = self.ledger.verify()
        blocks = [
            {
                "index": b.index,
                "hash": b.hash[:16],
                "prev_hash": b.prev_hash[:16],
                "merkle_root": b.merkle_root[:16],
                "entries": len(b.entries),
            }
            for b in self.ledger.chain
        ]
        return {
            "valid": v["valid"],
            "errors": v["errors"][:3],
            "records": v["records"],
            "blocks": blocks[-6:],
            "total_blocks": len(self.ledger.chain),
            "pending": len(self.ledger._pending),
        }

    # -- demo controls -------------------------------------------------------- #
    def tamper(self) -> dict:
        """Forge a ledger entry so the audience can watch verification fail."""
        self.ledger.flush()
        for block in self.ledger.chain[1:]:
            if block.entries:
                was = block.entries[0].get("receiver")
                block.entries[0]["receiver"] = "H99_FORGED"
                return {
                    "tampered": True,
                    "block": block.index,
                    "was": was,
                    "now": "H99_FORGED",
                    "ledger": self.ledger_state(),
                }
        return {"tampered": False,
                "message": "No sealed agreements yet — run a few transfers first.",
                "ledger": self.ledger_state()}

    def set_fairness(self, enabled: bool) -> dict:
        self.fairness_enabled = enabled
        self.negotiator.weights = ScoringWeights(fairness_enabled=enabled)
        return {"fairness_enabled": enabled}

    def equity(self) -> dict:
        return equity_report([]).as_dict()
