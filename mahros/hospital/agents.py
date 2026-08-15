"""Layer 1: intra-hospital resource agents.

Each agent owns exactly one resource type inside one hospital and answers two
questions:

  1. `assess(request)` -- can I physically take this patient?
  2. `price(request)`  -- what is it worth to me to give this unit up?

The pricing function is the agent's *private* utility. Nothing outside the
hospital sees it; peers only ever see the derived Bid. That separation is what
makes the negotiation decentralised rather than a disguised central optimiser.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Acuity, ResourceType, Specialty, TransferRequest
from .resources import ResourceLedger


@dataclass
class AgentAssessment:
    feasible: bool
    reason: str = ""
    prep_minutes: float = 0.0
    capability_match: float = 0.0
    post_accept_strain: float = 0.0
    opportunity_cost: float = 0.0


class ResourceAgent:
    """Base class for Bed / OR / Staff / ER agents."""

    resource: ResourceType = ResourceType.WARD_BED
    base_prep_minutes: float = 15.0
    # A hospital refuses to drop below this reserve for its own incoming patients.
    self_protection_reserve: int = 1

    def __init__(self, hospital, ledger: ResourceLedger) -> None:
        self.hospital = hospital
        self.ledger = ledger

    # -- feasibility ------------------------------------------------------- #
    def assess(self, req: TransferRequest, now: float) -> AgentAssessment:
        pool = self.ledger.pool(self.resource)
        if pool is None or pool.capacity == 0:
            return AgentAssessment(False, "resource_not_offered")

        capability = self.capability_match(req)
        if capability <= 0.0:
            return AgentAssessment(False, "no_specialty_capability")

        reserve = self.effective_reserve(req)
        if pool.available <= reserve:
            return AgentAssessment(False, "at_self_protection_reserve")

        post_strain = (pool.occupied + pool.reserved + 1) / pool.capacity
        return AgentAssessment(
            feasible=True,
            prep_minutes=self.prep_minutes(req),
            capability_match=capability,
            post_accept_strain=post_strain,
            opportunity_cost=self.price(req, post_strain),
        )

    def effective_reserve(self, req: TransferRequest) -> int:
        """Hospitals relax their own safety reserve for the sickest patients.

        This is the modelled expression of duty-of-care: a life-threatening
        case can consume the last unit, a routine one cannot.
        """
        if req.acuity >= Acuity.CRITICAL:
            return 0
        if req.acuity == Acuity.EMERGENT:
            return max(0, self.self_protection_reserve - 1)
        return self.self_protection_reserve

    # -- pricing ----------------------------------------------------------- #
    def price(self, req: TransferRequest, post_strain: float) -> float:
        """Private opportunity cost of surrendering one unit, in [0, ~3].

        Convex in strain: the last free ICU bed is worth far more than the
        fifth. Discounted by how long the unit is tied up.
        """
        los_days = req.expected_los_minutes / 1440.0
        return (post_strain ** 3) * (1.0 + 0.25 * los_days)

    def prep_minutes(self, req: TransferRequest) -> float:
        extra = 10.0 if req.acuity >= Acuity.CRITICAL else 0.0
        return self.base_prep_minutes + extra

    def capability_match(self, req: TransferRequest) -> float:
        """0 = cannot treat, 1 = perfect fit."""
        caps = self.hospital.specialties
        if req.specialty in caps:
            return 1.0
        if req.specialty == Specialty.GENERAL:
            return 0.9
        # A tertiary centre can usually stabilise outside its named specialties.
        if self.hospital.tier >= 3:
            return 0.6
        return 0.0

    # -- commitment -------------------------------------------------------- #
    def reserve(self, req: TransferRequest, hold_until: float) -> bool:
        pool = self.ledger.pool(self.resource)
        return bool(pool and pool.reserve(req.request_id, hold_until))

    def release(self, req_id: str) -> None:
        pool = self.ledger.pool(self.resource)
        if pool:
            pool.release_reservation(req_id)


class BedAgent(ResourceAgent):
    resource = ResourceType.ICU_BED
    base_prep_minutes = 25.0
    self_protection_reserve = 1


class HDUAgent(ResourceAgent):
    resource = ResourceType.HDU_BED
    base_prep_minutes = 20.0


class WardAgent(ResourceAgent):
    resource = ResourceType.WARD_BED
    base_prep_minutes = 10.0
    self_protection_reserve = 2


class ORAgent(ResourceAgent):
    """Operating room: prep dominated by scheduling a surgical team."""

    resource = ResourceType.OR_SLOT
    base_prep_minutes = 45.0
    self_protection_reserve = 0

    def prep_minutes(self, req: TransferRequest) -> float:
        # Out-of-hours theatre call-in penalty (23:00-07:00), a documented
        # driver of worse outcomes for night-time transfers.
        base = super().prep_minutes(req)
        hour = (self.hospital.now() / 60.0) % 24
        if hour >= 23 or hour < 7:
            base += 35.0
        return base


class VentilatorAgent(ResourceAgent):
    resource = ResourceType.VENTILATOR
    base_prep_minutes = 15.0


class CathLabAgent(ResourceAgent):
    resource = ResourceType.CATH_LAB
    base_prep_minutes = 30.0
    self_protection_reserve = 0

    def capability_match(self, req: TransferRequest) -> float:
        if Specialty.CARDIAC not in self.hospital.specialties:
            return 0.0
        return 1.0


AGENT_CLASSES: dict[ResourceType, type[ResourceAgent]] = {
    ResourceType.ICU_BED: BedAgent,
    ResourceType.HDU_BED: HDUAgent,
    ResourceType.WARD_BED: WardAgent,
    ResourceType.OR_SLOT: ORAgent,
    ResourceType.VENTILATOR: VentilatorAgent,
    ResourceType.CATH_LAB: CathLabAgent,
}
