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
from ..eval.assignment import INF, linear_sum_assignment
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
    #: The event loop. Only the batching optimiser reads it, and only to see
    #: which requests fall inside its own collection window.
    sim: Any = None


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
        neg = self.negotiator
        # How many refusals were fabricated, and how many the mechanism caught.
        # `misreports` is read from the hospitals' private records: it is ground
        # truth available to the *experiment*, never to the protocol.
        misreports = sum(getattr(h, "misreports", 0) for h in self.ctx.hospitals.values())
        return {
            "contested_decisions": self.contested,
            "mean_rounds": round(self.rounds_used / max(1, self.contested + 1), 2),
            "deliberations": neg.deliberations,
            "challenges_raised": neg.total_challenges,
            "refusals_overruled": neg.total_overruled,
            "burden_objections": neg.total_burden_objections,
            "misreports": misreports,
            "detection_rate": round(neg.total_overruled / misreports, 4) if misreports else 0.0,
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
            # Log the answer, not just the question. A phone call discloses just
            # as much as a protocol message -- the coordinator on the other end
            # hears "we're down to our last bed" exactly the same way. Omitting
            # these made the phone tree look like it leaked nothing at all,
            # which flattered the wrong arm. See mahros/privacy/leakage.py.
            self.ctx.bus.send(
                Message(
                    Perf.PROPOSE if bid.feasible else Perf.REFUSE,
                    peer, req.origin, req.request_id,
                    {"feasible": bid.feasible, "eta": round(bid.time_to_care, 1),
                     "capability": round(bid.capability_match, 2),
                     "reason": bid.refusal_reason},
                    t,
                )
            )
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
                    {"feasible": bid.feasible, "eta": round(bid.time_to_care, 1),
                     "strain": round(bid.post_accept_strain, 2),
                     "reason": bid.refusal_reason},
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

class BatchedOptimalStrategy(Strategy):
    """The real ceiling: full information *and* a joint solve.

    This is what `central` should always have been. It differs from the greedy
    centralised arm in the one way that matters: it collects transfer requests
    for `BATCH_WINDOW` minutes and then assigns the whole batch at once by
    minimum-cost matching, instead of serving them first-come-first-served.

    Why that is strictly stronger. Two patients need the last ICU bed at the
    nearest tertiary centre. Greedy gives it to whoever escalated first and
    sends the second one across the region. The joint solver notices that the
    second patient is 40 minutes from anywhere else while the first is 12
    minutes from a second option, and swaps them. No decentralised protocol can
    do this, because no participant can see both requests.

    It pays for that power twice over, and both costs are modelled:
      * every hospital surrenders its live private state to one node
      * every patient waits out the batching window before anything happens

    If MAHROS lands within a declared equivalence margin of *this*, the claim
    "decentralised negotiation gives up almost nothing" finally means something.
    """

    name = "BatchedOptimal"
    requires_central_data = True

    NODE = "ORACLE"
    #: Collection window. Longer batches assign better and start later; five
    #: minutes is about the largest delay a transfer service would tolerate.
    BATCH_WINDOW = 5.0

    def __init__(self, ctx: StrategyContext) -> None:
        super().__init__(ctx)
        self.batches = 0
        self.batch_sizes: list[int] = []
        #: request_id -> receiver, decided when the batch was solved
        self._decided: dict[str, str | None] = {}

    def resolve(self, req: TransferRequest, now: float) -> StrategyResult:
        if req.request_id not in self._decided:
            self._solve_batch(req, now)

        receiver = self._decided.pop(req.request_id, None)
        t = now + self.BATCH_WINDOW
        if receiver is None:
            return StrategyResult(
                elapsed_minutes=self.BATCH_WINDOW,
                coordinator_minutes=0.2,
                failure_reason="no_capacity_network_wide",
            )

        hosp = self.ctx.hospitals[receiver]
        bid = hosp.evaluate(req.public_view(), self.ctx.travel_time(req.origin, receiver), t)
        if not bid.feasible or not hosp.commit(
                req, t + self.ctx.cnp.reservation_hold_minutes):
            # The bed went between planning and committing. Even an oracle races.
            return StrategyResult(
                elapsed_minutes=self.BATCH_WINDOW,
                coordinator_minutes=0.2,
                failure_reason="no_capacity_network_wide",
            )

        agreement = Agreement(
            request_id=req.request_id, origin=req.origin, receiver=receiver,
            resource=req.resource, specialty=req.specialty,
            patient_ref=req.patient_ref, agreed_at=t,
            promised_care_start=t + bid.time_to_care,
            decided_by="batched_optimal",
            rationale=(f"Joint optimum over a batch of "
                       f"{self.batch_sizes[-1] if self.batch_sizes else 1}: {receiver}."),
        )
        self.ctx.fairness.record_accept(receiver, req.expected_los_minutes)
        self.ctx.fairness.record_send(req.origin)
        return StrategyResult(
            receiver=receiver, agreement=agreement,
            elapsed_minutes=self.BATCH_WINDOW, coordinator_minutes=0.2,
            contacted=len(self.ctx.hospitals) - 1,
            rationale=agreement.rationale, decided_by="batched_optimal",
        )

    def _solve_batch(self, trigger: TransferRequest, now: float) -> None:
        """Collect everything escalating inside the window and assign jointly."""
        batch = [trigger]
        sim = getattr(self.ctx, "sim", None)
        if sim is not None:
            for payload in sim.queue.peek_until(now + self.BATCH_WINDOW, "negotiate"):
                other = payload.get("request") if isinstance(payload, dict) else None
                if (other is not None and other is not trigger
                        and other.request_id not in self._decided
                        and other.status not in (RequestStatus.COMPLETED,
                                                 RequestStatus.IN_TRANSIT,
                                                 RequestStatus.EXPIRED)):
                    batch.append(other)

        t = now + self.BATCH_WINDOW
        peers = [h for h in self.ctx.hospitals]

        # Cost = minutes to definitive care. Infeasible pairings cost infinity
        # and are never chosen. This is the objective a transfer service would
        # actually state, and it deliberately contains no fairness term -- the
        # classical centralised objective is pure efficiency.
        cost: list[list[float]] = []
        for req in batch:
            row = []
            for hid in peers:
                if hid == req.origin:
                    row.append(INF)
                    continue
                hosp = self.ctx.hospitals[hid]
                bid = hosp.evaluate(
                    req.public_view(), self.ctx.travel_time(req.origin, hid), t)
                # Same content shape as every other arm, so the leakage
                # measurement compares like with like.
                self.ctx.bus.send(Message(
                    Perf.PROPOSE if bid.feasible else Perf.REFUSE, hid, self.NODE,
                    req.request_id,
                    {"feasible": bid.feasible, "eta": round(bid.time_to_care, 1),
                     "strain": round(bid.post_accept_strain, 2),
                     "reason": bid.refusal_reason}, t))
                if not bid.feasible or t + bid.time_to_care > req.expires_at:
                    row.append(INF)
                else:
                    row.append(bid.time_to_care)
            cost.append(row)

        if len(peers) < len(batch):
            # More simultaneous requests than hospitals: pad with dummy columns
            # so the solver stays well-formed. Padded picks mean "unplaced".
            pad = len(batch) - len(peers)
            for row in cost:
                row.extend([INF] * pad)
            peers = peers + [None] * pad       # type: ignore[list-item]

        assignment = linear_sum_assignment(cost)
        for i, req in enumerate(batch):
            col = assignment[i]
            chosen = None
            if col >= 0 and col < len(peers) and peers[col] is not None \
                    and cost[i][col] != INF:
                chosen = peers[col]
            self._decided[req.request_id] = chosen

        self.batches += 1
        self.batch_sizes.append(len(batch))
        self.ctx.bus.send(Message(
            Perf.CFP, self.NODE, self.NODE, trigger.request_id,
            {"batch": len(batch)}, t))

    def stats(self) -> dict[str, Any]:
        return {
            "batches_solved": self.batches,
            "mean_batch_size": round(
                sum(self.batch_sizes) / len(self.batch_sizes), 2) if self.batch_sizes else 0.0,
            "max_batch_size": max(self.batch_sizes) if self.batch_sizes else 0,
        }


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
    "optimal": BatchedOptimalStrategy,
    "nearest": NearestAvailableStrategy,
    "none": NoTransferStrategy,
}
