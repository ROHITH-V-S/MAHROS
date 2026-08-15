"""Transfer-allocation strategies: MAHROS and the baselines it is measured against.

Every strategy implements the same interface, so the runner is identical across
arms and any difference in results comes from the allocation logic alone.

    resolve(request, now) -> StrategyResult

The baselines are chosen to answer the four objections directly:

  * `PhoneTreeStrategy`   -- "isn't it faster to just call the hospitals?"
      Models what actually happens today: a human coordinator ringing hospitals
      one at a time, ~9 min per call, limited coordinators per hospital, and
      information that is already stale by the time they reach hospital number
      six. This is the honest comparator, and under surge it is where MAHROS
      should win decisively. If MAHROS does not beat it, you have learned
      something real.

  * `CentralizedStrategy` -- the omniscient central authority. It sees every
      hospital's private state and solves a global assignment. This is an
      *upper bound*, not a straw man: MAHROS should approach it on efficiency
      while never requiring anyone to surrender their data. Closing most of the
      gap with zero data centralisation is the actual contribution.

  * `NearestAvailableStrategy` -- greedy first-fit. Fast, myopic, and it starves
      the closest tertiary centre. Demonstrates why fairness needs to be explicit.

  * `NoTransferStrategy`  -- the patient waits for a local bed. Floor condition.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.types import Agreement, RequestStatus, TransferRequest
from ..fairness.metrics import FairnessLedger
from ..negotiation.cnp import CNPConfig, ContractNetNegotiator
from ..negotiation.messages import Message, MessageBus, Perf
from ..negotiation.scoring import ScoringWeights, score_bids
from ..privacy.anonymizer import PrivacyAudit


@dataclass
class StrategyResult:
    receiver: str | None = None
    agreement: Agreement | None = None
    elapsed_minutes: float = 0.0         # wall-clock consumed by the brokering itself
    coordinator_minutes: float = 0.0     # scarce human staff time consumed
    contacted: int = 0
    rationale: str = ""
    decided_by: str = ""
    failure_reason: str = ""


class Strategy:
    name = "abstract"
    #: does this strategy require every hospital to expose private state?
    requires_central_data: bool = False

    def __init__(self, ctx: "StrategyContext") -> None:
        self.ctx = ctx

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        return {}


@dataclass
class StrategyContext:
    hospitals: dict[str, Any]
    travel_time: Callable[[str, str], float]
    fairness: FairnessLedger
    privacy: PrivacyAudit
    bus: MessageBus
    ledger: Any
    weights: ScoringWeights
    cnp: CNPConfig
    coordinator: Any = None
    rng: random.Random = field(default_factory=lambda: random.Random(7))


# --------------------------------------------------------------------------- #
# MAHROS
# --------------------------------------------------------------------------- #

class MahrosStrategy(Strategy):
    name = "MAHROS"
    requires_central_data = False

    def __init__(self, ctx: StrategyContext) -> None:
        super().__init__(ctx)
        self.negotiator = ContractNetNegotiator(
            hospitals=ctx.hospitals,
            travel_time=ctx.travel_time,
            bus=ctx.bus,
            fairness=ctx.fairness,
            privacy=ctx.privacy,
            weights=ctx.weights,
            config=ctx.cnp,
            coordinator=ctx.coordinator,
            ledger=ctx.ledger,
        )
        self.contested = 0
        self.rounds_used = 0

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        outcome = self.negotiator.run(req, now)
        self.rounds_used += outcome.rounds
        if outcome.contested:
            self.contested += 1

        # Protocol latency: bid windows are *parallel*, so a round costs one
        # window regardless of how many peers were asked. That parallelism is
        # precisely the claim being tested against the phone tree.
        elapsed = self.ctx.cnp.bid_window_minutes * outcome.rounds

        if outcome.agreement is None:
            return StrategyResult(
                elapsed_minutes=elapsed,
                coordinator_minutes=req.coordination_minutes,
                contacted=req.n_peers_contacted,
                failure_reason=req.failure_reason or "no_feasible_bid",
            )
        return StrategyResult(
            receiver=outcome.agreement.receiver,
            agreement=outcome.agreement,
            elapsed_minutes=elapsed,
            coordinator_minutes=req.coordination_minutes,
            contacted=req.n_peers_contacted,
            rationale=outcome.rationale,
            decided_by=outcome.decided_by,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "contested_decisions": self.contested,
            "mean_rounds": round(self.rounds_used / max(1, self.contested + 1), 2),
        }


# --------------------------------------------------------------------------- #
# Baseline 1: today's practice
# --------------------------------------------------------------------------- #

class PhoneTreeStrategy(Strategy):
    """Serial human calling. The comparator that reviewers will care about most."""

    name = "PhoneTree"
    requires_central_data = False

    MEAN_CALL_MINUTES = 9.0
    CALL_SD = 3.0
    MAX_CALLS = 8
    #: probability the coordinator's mental model of who has space is wrong
    STALE_INFO_PROB = 0.25

    def __init__(self, ctx: StrategyContext) -> None:
        super().__init__(ctx)
        self.calls_made = 0
        self.blocked_no_coordinator = 0

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        origin = self.ctx.hospitals[req.origin]
        rng = self.ctx.rng

        # A transfer needs a free human. Under surge, there isn't one.
        if not origin.acquire_coordinator():
            self.blocked_no_coordinator += 1
            return StrategyResult(
                elapsed_minutes=0.0,
                failure_reason="no_coordinator_available",
            )

        peers = sorted(
            (h for h in self.ctx.hospitals if h != req.origin),
            key=lambda h: self.ctx.travel_time(req.origin, h),
        )[: self.MAX_CALLS]

        elapsed = 0.0
        contacted = 0
        public = self.ctx.privacy.outbound(req.public_view())

        for peer in peers:
            call_minutes = max(2.0, rng.gauss(self.MEAN_CALL_MINUTES, self.CALL_SD))
            elapsed += call_minutes
            contacted += 1
            self.calls_made += 1

            self.ctx.bus.send(
                Message(Perf.CFP, req.origin, peer, req.request_id, public, now + elapsed)
            )

            # Calls happen one at a time, so each is evaluated at a *later* clock
            # time -- by call six the situation has moved on. This is the crux.
            t = now + elapsed
            if t > req.expires_at:
                break

            hosp = self.ctx.hospitals[peer]
            bid = hosp.evaluate(public, self.ctx.travel_time(req.origin, peer), t)
            if not bid.feasible:
                continue

            # Humans work from stale whiteboards and phone hearsay.
            if rng.random() < self.STALE_INFO_PROB:
                continue

            if hosp.commit(req, t + self.ctx.cnp.reservation_hold_minutes):
                agreement = Agreement(
                    request_id=req.request_id,
                    origin=req.origin,
                    receiver=peer,
                    resource=req.resource,
                    patient_ref=req.patient_ref,
                    agreed_at=t,
                    promised_care_start=t + bid.time_to_care,
                    decided_by="phone_tree",
                    rationale=f"Accepted on call {contacted} to {peer}.",
                )
                self.ctx.fairness.record_accept(peer, req.expected_los_minutes)
                self.ctx.fairness.record_send(req.origin)
                origin.release_coordinator(elapsed)
                return StrategyResult(
                    receiver=peer,
                    agreement=agreement,
                    elapsed_minutes=elapsed,
                    coordinator_minutes=elapsed,
                    contacted=contacted,
                    rationale=agreement.rationale,
                    decided_by="phone_tree",
                )

        origin.release_coordinator(elapsed)
        return StrategyResult(
            elapsed_minutes=elapsed,
            coordinator_minutes=elapsed,
            contacted=contacted,
            failure_reason="phone_tree_exhausted",
        )

    def stats(self) -> dict[str, Any]:
        return {
            "calls_made": self.calls_made,
            "blocked_no_coordinator": self.blocked_no_coordinator,
        }


# --------------------------------------------------------------------------- #
# Baseline 2: omniscient central authority (upper bound)
# --------------------------------------------------------------------------- #

class CentralizedStrategy(Strategy):
    """Sees every hospital's private state. Efficiency ceiling, privacy floor."""

    name = "Centralized"
    requires_central_data = True

    #: the single node every hospital reports its private state to
    NODE = "CENTRAL"
    DISPATCH_LATENCY = 1.0               # a central system is fast, and knows everything

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        t = now + self.DISPATCH_LATENCY
        bids = []
        # The central authority broadcasts and every hospital reports back to it.
        # Logged to the same bus as MAHROS so the audit measures both on equal
        # terms -- otherwise this arm looks free, which is exactly the illusion
        # centralised designs trade on.
        self.ctx.bus.send(
            Message(Perf.CFP, self.NODE, self.NODE, req.request_id,
                    req.public_view(), t)
        )
        for hid, hosp in self.ctx.hospitals.items():
            if hid == req.origin:
                continue
            # NOTE: the central node reads private state directly. That is the
            # whole point of this arm -- it is the thing MAHROS refuses to do.
            bid = hosp.evaluate(req.public_view(), self.ctx.travel_time(req.origin, hid), t)
            bids.append(bid)
            self.ctx.bus.send(
                Message(
                    Perf.PROPOSE if bid.feasible else Perf.REFUSE,
                    hid, self.NODE, req.request_id,
                    {"feasible": bid.feasible, "eta": round(bid.time_to_care, 1),
                     "strain": round(bid.post_accept_strain, 2),
                     "reason": bid.refusal_reason},
                    t,
                )
            )

        # No fairness term: the classic centralised objective is pure efficiency.
        scored = score_bids(
            req, bids, t,
            ScoringWeights(fairness_enabled=False),
            None,
        )
        for cand in scored:
            hosp = self.ctx.hospitals[cand.bid.bidder]
            if hosp.commit(req, t + self.ctx.cnp.reservation_hold_minutes):
                agreement = Agreement(
                    request_id=req.request_id, origin=req.origin,
                    receiver=cand.bid.bidder, resource=req.resource,
                    patient_ref=req.patient_ref, agreed_at=t,
                    promised_care_start=t + cand.bid.time_to_care,
                    decided_by="central_optimiser",
                    rationale=f"Global optimum: {cand.bid.bidder}, ETA {cand.bid.time_to_care:.0f} min.",
                )
                self.ctx.fairness.record_accept(cand.bid.bidder, req.expected_los_minutes)
                self.ctx.fairness.record_send(req.origin)
                return StrategyResult(
                    receiver=cand.bid.bidder, agreement=agreement,
                    elapsed_minutes=self.DISPATCH_LATENCY,
                    coordinator_minutes=0.2,
                    contacted=len(bids), rationale=agreement.rationale,
                    decided_by="central_optimiser",
                )
        return StrategyResult(
            elapsed_minutes=self.DISPATCH_LATENCY,
            contacted=len(bids),
            failure_reason="no_capacity_network_wide",
        )


# --------------------------------------------------------------------------- #
# Baseline 3: greedy nearest-available
# --------------------------------------------------------------------------- #

class NearestAvailableStrategy(Strategy):
    name = "NearestAvailable"
    requires_central_data = True

    NODE = "REGISTRY"                    # a shared bed-availability registry
    DISPATCH_LATENCY = 3.0

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        t = now + self.DISPATCH_LATENCY
        peers = sorted(
            (h for h in self.ctx.hospitals if h != req.origin),
            key=lambda h: self.ctx.travel_time(req.origin, h),
        )
        self.ctx.bus.send(
            Message(Perf.CFP, self.NODE, self.NODE, req.request_id, req.public_view(), t)
        )
        for peer in peers:
            hosp = self.ctx.hospitals[peer]
            bid = hosp.evaluate(req.public_view(), self.ctx.travel_time(req.origin, peer), t)
            # A shared registry means every hospital's live state is readable by
            # the querying node, whether or not it ends up being chosen.
            self.ctx.bus.send(
                Message(
                    Perf.PROPOSE if bid.feasible else Perf.REFUSE,
                    peer, self.NODE, req.request_id,
                    {"feasible": bid.feasible, "eta": round(bid.time_to_care, 1)},
                    t,
                )
            )
            if not bid.feasible:
                continue
            if hosp.commit(req, t + self.ctx.cnp.reservation_hold_minutes):
                agreement = Agreement(
                    request_id=req.request_id, origin=req.origin, receiver=peer,
                    resource=req.resource, patient_ref=req.patient_ref, agreed_at=t,
                    promised_care_start=t + bid.time_to_care,
                    decided_by="greedy_nearest",
                    rationale=f"Nearest hospital with declared capacity: {peer}.",
                )
                self.ctx.fairness.record_accept(peer, req.expected_los_minutes)
                self.ctx.fairness.record_send(req.origin)
                return StrategyResult(
                    receiver=peer, agreement=agreement,
                    elapsed_minutes=self.DISPATCH_LATENCY,
                    coordinator_minutes=1.0,
                    contacted=peers.index(peer) + 1,
                    rationale=agreement.rationale, decided_by="greedy_nearest",
                )
        return StrategyResult(
            elapsed_minutes=self.DISPATCH_LATENCY,
            failure_reason="no_nearby_capacity",
        )


# --------------------------------------------------------------------------- #
# Baseline 4: floor condition
# --------------------------------------------------------------------------- #

class NoTransferStrategy(Strategy):
    """No inter-hospital coordination at all: the patient waits where they are."""

    name = "NoTransfer"

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        origin = self.ctx.hospitals[req.origin]
        pool = origin.resources.pool(req.resource)
        if pool and pool.reserve(req.request_id, now + self.ctx.cnp.reservation_hold_minutes):
            return StrategyResult(
                receiver=req.origin, elapsed_minutes=0.0, contacted=0,
                rationale="Held locally; a unit became free in-house.",
                decided_by="local_only",
            )
        return StrategyResult(failure_reason="no_local_capacity")


STRATEGIES: dict[str, type[Strategy]] = {
    "mahros": MahrosStrategy,
    "phone": PhoneTreeStrategy,
    "central": CentralizedStrategy,
    "nearest": NearestAvailableStrategy,
    "none": NoTransferStrategy,
}
