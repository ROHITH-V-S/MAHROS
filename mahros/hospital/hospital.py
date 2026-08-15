"""A hospital: an autonomous, self-interested participant in the network.

Each hospital holds its own resource ledger, its own agents, and its own
transfer coordinators (a scarce human resource -- this is what makes the
phone-tree baseline realistic and what MAHROS actually relieves).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.types import (
    Acuity,
    Bid,
    ResourceType,
    Specialty,
    TransferRequest,
)
from .agents import AGENT_CLASSES, ResourceAgent
from .resources import ResourceLedger


@dataclass
class HospitalConfig:
    hospital_id: str
    name: str
    tier: int                            # 1 = primary, 2 = district, 3 = tertiary
    x: float                             # km on a plane; travel time derived from this
    y: float
    capacities: dict[ResourceType, int]
    specialties: set[Specialty]
    coordinators: int = 1                # staff who can broker a transfer at a time
    base_arrival_rate: float = 0.9       # admissions per hour
    escalation_prob: float = 0.14        # P(admitted patient needs escalation)


class Hospital:
    def __init__(self, cfg: HospitalConfig, sim=None) -> None:
        self.cfg = cfg
        self.id = cfg.hospital_id
        self.name = cfg.name
        self.tier = cfg.tier
        self.specialties = cfg.specialties
        self.sim = sim

        self.resources = ResourceLedger(cfg.capacities)
        self.agents: dict[ResourceType, ResourceAgent] = {
            r: AGENT_CLASSES[r](self, self.resources)
            for r in cfg.capacities
            if r in AGENT_CLASSES
        }

        # coordinator pool -- consumed by brokering work
        self.coordinators_free: int = cfg.coordinators
        self.coordinator_busy_minutes: float = 0.0

        # local outcome bookkeeping
        self.sent_requests: list[TransferRequest] = []
        self.accepted_count: int = 0
        self.sent_count: int = 0
        self.refused_count: int = 0
        self.local_admissions: int = 0

    # -- glue -------------------------------------------------------------- #
    def now(self) -> float:
        return self.sim.now if self.sim is not None else 0.0

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Hospital {self.id} tier={self.tier} strain={self.resources.overall_strain():.2f}>"

    # -- capacity ---------------------------------------------------------- #
    def can_admit_locally(self, resource: ResourceType, specialty: Specialty) -> bool:
        agent = self.agents.get(resource)
        if agent is None:
            return False
        probe = TransferRequest(resource=resource, specialty=specialty)
        if agent.capability_match(probe) <= 0.0:
            return False
        return self.resources.available(resource) > 0

    def admit_local(self, resource: ResourceType) -> bool:
        pool = self.resources.pool(resource)
        if pool is None or pool.available <= 0:
            return False
        pool.occupy()
        self.local_admissions += 1
        return True

    def discharge(self, resource: ResourceType) -> None:
        pool = self.resources.pool(resource)
        if pool:
            pool.free()

    # -- Contract Net: responding to a peer's call-for-proposals ----------- #
    def evaluate(self, public_req: dict, travel_minutes: float, now: float) -> Bid:
        """Produce a bid from the *public* view of a request only.

        `public_req` is the anonymised dict that crossed the boundary. We
        reconstruct just enough to price it. Nothing patient-identifying is
        touched here -- that is the point.
        """
        resource = ResourceType(public_req["resource"])
        bid = Bid(
            request_id=public_req["request_id"],
            bidder=self.id,
            travel_minutes=travel_minutes,
            submitted_at=now,
        )

        agent = self.agents.get(resource)
        if agent is None:
            bid.refusal_reason = "resource_not_offered"
            self.refused_count += 1
            return bid

        shadow = TransferRequest(
            request_id=public_req["request_id"],
            origin=public_req["origin"],
            patient_ref=public_req["patient_ref"],
            resource=resource,
            specialty=Specialty(public_req["specialty"]),
            acuity=Acuity(public_req["acuity"]),
            created_at=public_req["created_at"],
            expires_at=public_req["expires_at"],
            expected_los_minutes=public_req["expected_los_minutes"],
        )

        assessment = agent.assess(shadow, now)
        if not assessment.feasible:
            bid.refusal_reason = assessment.reason
            self.refused_count += 1
            return bid

        # Would the patient even arrive inside the clinical safe window?
        if now + travel_minutes + assessment.prep_minutes > shadow.expires_at:
            bid.refusal_reason = "cannot_meet_clinical_deadline"
            self.refused_count += 1
            return bid

        bid.feasible = True
        bid.prep_minutes = assessment.prep_minutes
        bid.capability_match = assessment.capability_match
        bid.post_accept_strain = assessment.post_accept_strain
        bid.opportunity_cost = assessment.opportunity_cost
        return bid

    def commit(self, req: TransferRequest, hold_until: float) -> bool:
        agent = self.agents.get(req.resource)
        if agent is None:
            return False
        ok = agent.reserve(req, hold_until)
        if ok:
            self.accepted_count += 1
        return ok

    def release(self, req: TransferRequest) -> None:
        agent = self.agents.get(req.resource)
        if agent:
            agent.release(req.request_id)
            self.accepted_count = max(0, self.accepted_count - 1)

    def receive_patient(self, req: TransferRequest) -> bool:
        pool = self.resources.pool(req.resource)
        return bool(pool and pool.occupy(req.request_id))

    # -- coordinator pool -------------------------------------------------- #
    def acquire_coordinator(self) -> bool:
        if self.coordinators_free > 0:
            self.coordinators_free -= 1
            return True
        return False

    def release_coordinator(self, minutes_used: float) -> None:
        self.coordinators_free = min(self.cfg.coordinators, self.coordinators_free + 1)
        self.coordinator_busy_minutes += minutes_used
