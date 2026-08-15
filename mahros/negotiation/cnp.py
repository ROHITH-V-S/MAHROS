"""Layer 2: the decentralised negotiation protocol (Contract Net).

Four phases, each with real elapsed time so the protocol's latency shows up
honestly in the results:

    ANNOUNCE  origin broadcasts an anonymised CFP to eligible peers
    BID       peers evaluate privately and PROPOSE or REFUSE, in parallel
    AWARD     origin scores bids, reserves at the winner, ACCEPT/REJECT
    CONFIRM   patient travels; on arrival the reservation becomes occupancy

The property that makes this decentralised: the origin sees only the bids
returned to *it*, peers see only the anonymised CFP, and no node holds the
network's joint state. `MessageBus.audit_no_global_view` tests this.

Key implementation details that make results trustworthy:
  * bids are collected in parallel (one bid window), not serially
  * the winner's resource is *reserved* at award time, then released if the
    patient never arrives -- no double allocation
  * on award failure (race lost), the protocol re-awards to the runner-up
    rather than silently failing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.types import (
    Agreement,
    Bid,
    RequestStatus,
    TransferRequest,
)
from ..fairness.metrics import FairnessLedger
from ..privacy.anonymizer import PrivacyAudit
from .messages import Message, MessageBus, Perf
from .scoring import ScoredBid, ScoringWeights, is_contested, score_bids


@dataclass
class CNPConfig:
    bid_window_minutes: float = 2.0      # how long the origin waits for proposals
    max_peers: int = 8                   # cap the broadcast (bandwidth + realism)
    reservation_hold_minutes: float = 45.0
    contested_band: float = 0.05         # score gap below which the LLM is consulted
    agent_coordination_minutes: float = 0.5   # staff time MAHROS consumes per request
    enable_llm_arbitration: bool = False      # ablation switch; off = fully deterministic
    max_rounds: int = 2                  # retry with a widened peer set on failure


@dataclass
class NegotiationOutcome:
    request: TransferRequest
    agreement: Agreement | None = None
    scored: list[ScoredBid] = field(default_factory=list)
    all_bids: list[Bid] = field(default_factory=list)
    contested: bool = False
    decided_by: str = "cnp"
    rationale: str = ""
    rounds: int = 1


class ContractNetNegotiator:
    """Runs one CFP to completion. Instantiated per request by the runner."""

    def __init__(
        self,
        hospitals: dict[str, Any],
        travel_time: Callable[[str, str], float],
        bus: MessageBus,
        fairness: FairnessLedger,
        privacy: PrivacyAudit,
        weights: ScoringWeights,
        config: CNPConfig,
        coordinator=None,                # llm.coordinator.LLMCoordinator | None
        ledger=None,                     # ledger.interface.LedgerBackend | None
        observer: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.hospitals = hospitals
        self.travel_time = travel_time
        self.bus = bus
        self.fairness = fairness
        self.privacy = privacy
        self.weights = weights
        self.config = config
        self.coordinator = coordinator
        self.ledger = ledger
        # Optional trace hook. The batch simulation leaves this None (an
        # observer that did work would distort the timings we are measuring);
        # the live demo server uses it to stream each protocol step to a UI.
        self.observer = observer

    def _emit(self, kind: str, payload: dict) -> None:
        if self.observer is not None:
            self.observer(kind, payload)

    # -- phase 1: eligibility + announce ----------------------------------- #
    def select_peers(self, req: TransferRequest, round_no: int) -> list[str]:
        """Rank peers by travel time and take the closest N.

        Widen the net on a retry round. Note this uses only *public* topology
        (who is where), never peer capacity -- the origin genuinely does not
        know who has a free bed until it asks.
        """
        cap = self.config.max_peers * round_no
        peers = [h for h in self.hospitals if h != req.origin]
        peers.sort(key=lambda h: self.travel_time(req.origin, h))
        return peers[:cap]

    def announce(self, req: TransferRequest, peers: list[str], now: float) -> None:
        payload = self.privacy.outbound(req.public_view())
        self._emit("cfp", {
            "request_id": req.request_id,
            "origin": req.origin,
            "peers": list(peers),
            "public_view": payload,
            "resource": req.resource.value,
            "specialty": req.specialty.value,
            "acuity": int(req.acuity),
            "window_min": req.acuity.safe_window_minutes,
            "deadline": req.expires_at,
            "now": now,
        })
        for p in peers:
            self.bus.send(
                Message(
                    performative=Perf.CFP,
                    sender=req.origin,
                    receiver=p,
                    conversation_id=req.request_id,
                    content=payload,
                    sent_at=now,
                )
            )
        req.status = RequestStatus.ANNOUNCED
        req.n_peers_contacted += len(peers)

    # -- phase 2: collect bids (parallel) ---------------------------------- #
    def collect_bids(self, req: TransferRequest, peers: list[str], now: float) -> list[Bid]:
        public = self.privacy.outbound(req.public_view())
        bids: list[Bid] = []
        for p in peers:
            hosp = self.hospitals[p]
            tt = self.travel_time(req.origin, p)
            bid = hosp.evaluate(public, tt, now)
            bids.append(bid)
            self._emit("bid", {
                "request_id": req.request_id,
                "bidder": p,
                "feasible": bid.feasible,
                "reason": bid.refusal_reason,
                "travel_minutes": round(bid.travel_minutes, 1),
                "prep_minutes": round(bid.prep_minutes, 1),
                "time_to_care": round(bid.time_to_care, 1),
                "capability_match": round(bid.capability_match, 2),
                "post_accept_strain": round(bid.post_accept_strain, 3),
                # The bidder's private valuation. Shown in the demo purely to
                # make the point that it exists and never leaves the bidder.
                "opportunity_cost": round(bid.opportunity_cost, 3),
            })
            self.bus.send(
                Message(
                    performative=Perf.PROPOSE if bid.feasible else Perf.REFUSE,
                    sender=p,
                    receiver=req.origin,
                    conversation_id=req.request_id,
                    content={
                        "feasible": bid.feasible,
                        "eta": round(bid.time_to_care, 1),
                        "capability": round(bid.capability_match, 2),
                        "reason": bid.refusal_reason,
                    },
                    sent_at=now,
                )
            )
        req.n_bids_received += sum(1 for b in bids if b.feasible)
        return bids

    # -- phase 3: award ---------------------------------------------------- #
    def award(
        self, req: TransferRequest, bids: list[Bid], now: float
    ) -> NegotiationOutcome:
        scored = score_bids(req, bids, now, self.weights, self.fairness)
        outcome = NegotiationOutcome(request=req, scored=scored, all_bids=bids)

        self._emit("scored", {
            "request_id": req.request_id,
            "eliminated": [
                {"bidder": b.bidder, "reason": b.refusal_reason}
                for b in bids if not b.feasible
            ],
            # A feasible bid can still be dropped by score_bids if it cannot
            # beat the clinical deadline -- surface that rather than hide it.
            "missed_deadline": [
                b.bidder for b in bids
                if b.feasible and b.bidder not in {s.bid.bidder for s in scored}
            ],
            "ranked": [{
                "bidder": s.bid.bidder,
                "score": round(s.score, 4),
                "time_term": round(s.time_term, 3),
                "capability_term": round(s.capability_term, 3),
                "strain_term": round(s.strain_term, 3),
                "fairness_term": round(s.fairness_term, 3),
                "time_to_care": round(s.bid.time_to_care, 1),
                "slack_minutes": round(s.slack_minutes, 1),
                "fairness_credit": round(self.fairness.credit(s.bid.bidder), 3),
            } for s in scored],
        })

        if not scored:
            return outcome

        outcome.contested = is_contested(scored, self.config.contested_band)
        chosen = scored[0]
        decided_by = "cnp"
        rationale = ""

        # Escalate genuinely ambiguous choices to the LLM coordinator.
        if outcome.contested and self.coordinator is not None:
            decision = self.coordinator.arbitrate(req, scored, self.fairness, now)
            rationale = decision.rationale
            if self.config.enable_llm_arbitration and decision.winner:
                match = next((s for s in scored if s.bid.bidder == decision.winner), None)
                if match is not None:
                    chosen = match
                    decided_by = "llm_coordinator"
            else:
                decided_by = "cnp"  # LLM explained; deterministic policy still decided

        # Try to actually reserve. Losing a race to a concurrent award is normal
        # in a decentralised system -- fall through to the next-best bid.
        for candidate in [chosen] + [s for s in scored if s is not chosen]:
            receiver = self.hospitals[candidate.bid.bidder]
            hold_until = now + self.config.reservation_hold_minutes
            if receiver.commit(req, hold_until):
                agreement = self._finalise(req, candidate, now, decided_by, rationale)
                outcome.agreement = agreement
                outcome.decided_by = decided_by
                outcome.rationale = rationale or self._default_rationale(req, candidate, scored)
                # The explanation is part of the audited record, not just the
                # return value -- an agreement on the ledger must carry the
                # reason it was made, or the audit trail explains nothing.
                agreement.rationale = outcome.rationale
                self._notify(req, candidate, scored, now)
                self._emit("awarded", {
                    "request_id": req.request_id,
                    "origin": req.origin,
                    "winner": candidate.bid.bidder,
                    "runner_up": scored[1].bid.bidder if len(scored) > 1 else None,
                    "score": round(candidate.score, 4),
                    "time_to_care": round(candidate.bid.time_to_care, 1),
                    "slack_minutes": round(candidate.slack_minutes, 1),
                    "contested": outcome.contested,
                    "decided_by": decided_by,
                    "rationale": outcome.rationale,
                    "terms_hash": agreement.terms_hash,
                    "agreement_id": agreement.agreement_id,
                    "lost_race": candidate is not chosen,
                })
                return outcome

        return outcome

    def _finalise(
        self,
        req: TransferRequest,
        chosen: ScoredBid,
        now: float,
        decided_by: str,
        rationale: str,
    ) -> Agreement:
        req.status = RequestStatus.AWARDED
        req.awarded_to = chosen.bid.bidder
        req.awarded_at = now

        agreement = Agreement(
            request_id=req.request_id,
            origin=req.origin,
            receiver=chosen.bid.bidder,
            resource=req.resource,
            patient_ref=req.patient_ref,
            agreed_at=now,
            promised_care_start=now + chosen.bid.time_to_care,
            decided_by=decided_by,
            rationale=rationale,
        )

        self.fairness.record_accept(chosen.bid.bidder, req.expected_los_minutes)
        self.fairness.record_send(req.origin)

        if self.ledger is not None:
            receipt = self.ledger.record_agreement(agreement)
            agreement.terms_hash = receipt.terms_hash
        return agreement

    def _notify(
        self, req: TransferRequest, chosen: ScoredBid, scored: list[ScoredBid], now: float
    ) -> None:
        self.bus.send(
            Message(
                performative=Perf.ACCEPT_PROPOSAL,
                sender=req.origin,
                receiver=chosen.bid.bidder,
                conversation_id=req.request_id,
                content={"eta": round(chosen.bid.time_to_care, 1)},
                sent_at=now,
            )
        )
        for s in scored:
            if s is chosen:
                continue
            self.bus.send(
                Message(
                    performative=Perf.REJECT_PROPOSAL,
                    sender=req.origin,
                    receiver=s.bid.bidder,
                    conversation_id=req.request_id,
                    content={},
                    sent_at=now,
                )
            )

    @staticmethod
    def _default_rationale(
        req: TransferRequest, chosen: ScoredBid, scored: list[ScoredBid]
    ) -> str:
        runner = scored[1] if len(scored) > 1 else None
        base = (
            f"Selected {chosen.bid.bidder} for a {req.acuity.name.lower()} "
            f"{req.resource.value.replace('_', ' ')} transfer: reachable in "
            f"{chosen.bid.time_to_care:.0f} min, leaving {chosen.slack_minutes:.0f} min "
            f"of the {req.acuity.safe_window_minutes} min clinical window, with a "
            f"{chosen.capability_term:.0%} specialty match."
        )
        if runner is not None:
            base += (
                f" Next-best was {runner.bid.bidder} "
                f"({runner.bid.time_to_care:.0f} min, score {runner.score:.3f} "
                f"vs {chosen.score:.3f})."
            )
        if chosen.fairness_term < -0.01:
            base += " Accepted despite a recent above-share transfer burden, as no comparable alternative met the clinical window."
        elif chosen.fairness_term > 0.01:
            base += " This choice also rebalances load toward a hospital that has recently taken below its capacity share."
        return base

    # -- orchestration ----------------------------------------------------- #
    def run(self, req: TransferRequest, now: float) -> NegotiationOutcome:
        """Execute the full protocol. Returns the outcome; the caller advances time."""
        outcome = NegotiationOutcome(request=req)
        for round_no in range(1, self.config.max_rounds + 1):
            peers = self.select_peers(req, round_no)
            if not peers:
                break
            self.announce(req, peers, now)
            bids = self.collect_bids(req, peers, now)
            outcome = self.award(req, bids, now)
            outcome.rounds = round_no
            req.coordination_minutes += self.config.agent_coordination_minutes
            if outcome.agreement is not None:
                return outcome
            # Retry only if there is still clinical time to spare.
            if now + self.config.bid_window_minutes >= req.expires_at:
                break
        if outcome.agreement is None:
            req.status = RequestStatus.FAILED
            req.failure_reason = self._diagnose(outcome.all_bids)
            self._emit("failed", {
                "request_id": req.request_id,
                "origin": req.origin,
                "reason": req.failure_reason,
                "peers_contacted": req.n_peers_contacted,
            })
        return outcome

    @staticmethod
    def _diagnose(bids: list[Bid]) -> str:
        if not bids:
            return "no_peers_available"
        reasons: dict[str, int] = {}
        for b in bids:
            if not b.feasible:
                reasons[b.refusal_reason] = reasons.get(b.refusal_reason, 0) + 1
        if not reasons:
            return "all_awards_lost_race"
        return max(reasons.items(), key=lambda kv: kv[1])[0]
