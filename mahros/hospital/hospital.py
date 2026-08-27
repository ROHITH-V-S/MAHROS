"""A hospital: an autonomous, self-interested participant in the network.

Each hospital holds its own resource ledger, its own agents, and its own
transfer coordinators (a scarce human resource -- this is what makes the
phone-tree baseline realistic and what MAHROS actually relieves).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..core.types import (
    Acuity,
    Bid,
    ResourceType,
    Specialty,
    TransferRequest,
)
from .agents import AGENT_CLASSES, ResourceAgent
from .behaviours import BiddingPolicy, HonestPolicy, ReportedAssessment
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
    def __init__(
        self,
        cfg: HospitalConfig,
        sim=None,
        policy: BiddingPolicy | None = None,
        seed: int = 0,
    ) -> None:
        self.cfg = cfg
        self.id = cfg.hospital_id
        self.name = cfg.name
        self.tier = cfg.tier
        self.specialties = cfg.specialties
        self.sim = sim

        # How this hospital decides what to *say*. Honest by default, so every
        # pre-existing result is unchanged unless an experiment opts in.
        self.policy: BiddingPolicy = policy or HonestPolicy()
        self._rng = random.Random(hash((cfg.hospital_id, seed)) & 0xFFFFFFFF)
        #: request_id -> what we reported and what was actually true. Private;
        #: the only thing that ever reads it is this hospital's own defence.
        self._statements: dict[str, ReportedAssessment] = {}
        self.misreports: int = 0
        self.refusals_overruled: int = 0

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

        # What is actually true, computed before anyone decides what to say.
        truth = agent.assess(shadow, now)

        # What this hospital chooses to report. For an honest hospital these
        # are the same object; for a strategic one they are not, and the
        # difference is what the challenge mechanism exists to surface.
        statement = self.policy.report(truth, shadow, self, now, self._rng)
        self._statements[shadow.request_id] = statement
        if statement.misreported:
            self.misreports += 1

        assessment = statement.reported
        if not assessment.feasible:
            bid.refusal_reason = assessment.reason
            self.refused_count += 1
            return bid

        # Would the patient even arrive inside the clinical safe window? This
        # is public geometry -- travel time and prep are computable by both
        # sides -- so it is never something a hospital can be challenged on.
        if now + travel_minutes + assessment.prep_minutes > shadow.expires_at:
            bid.refusal_reason = "cannot_meet_clinical_deadline"
            self.refused_count += 1
            return bid

        return self._fill_bid(bid, assessment)

    @staticmethod
    def _fill_bid(bid: Bid, assessment) -> Bid:
        bid.feasible = True
        bid.prep_minutes = assessment.prep_minutes
        bid.capability_match = assessment.capability_match
        bid.post_accept_strain = assessment.post_accept_strain
        bid.opportunity_cost = assessment.opportunity_cost
        return bid

    # -- answering a challenge --------------------------------------------- #
    def defend(self, request_id: str, refusal_reason: str) -> bool:
        """Can this hospital justify a refusal it is being challenged on?

        Delegates to the behaviour policy, which answers from this hospital's
        own assessment. An honest refusal is supported by that assessment and
        the defence succeeds; a fabricated one is not, and it fails.

        A hospital with no record of the statement cannot defend it. That is
        the right default: if you cannot say why you refused, you have not
        justified the refusal.
        """
        statement = self._statements.get(request_id)
        if statement is None:
            return False
        return self.policy.defend(statement, refusal_reason)

    def truthful_bid(self, public_req: dict, travel_minutes: float, now: float) -> Bid:
        """The bid this hospital *would* have made had it stated the truth.

        Used only when a refusal has been struck out by the argumentation
        layer. The hospital is held to its own assessment -- it is never
        compelled to accept a patient it genuinely cannot take, because in that
        case the truthful assessment is a refusal too and this returns one.
        """
        bid = Bid(
            request_id=public_req["request_id"],
            bidder=self.id,
            travel_minutes=travel_minutes,
            submitted_at=now,
        )
        statement = self._statements.get(public_req["request_id"])
        if statement is None:
            bid.refusal_reason = "no_statement_on_record"
            return bid

        truth = statement.truthful
        if not truth.feasible:
            bid.refusal_reason = truth.reason
            return bid
        if now + travel_minutes + truth.prep_minutes > public_req["expires_at"]:
            bid.refusal_reason = "cannot_meet_clinical_deadline"
            return bid

        self.refusals_overruled += 1
        return self._fill_bid(bid, truth)

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
