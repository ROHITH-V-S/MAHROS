"""Layer 2: the decentralised negotiation protocol (Contract Net + argument).

Five phases, each with real elapsed time so the protocol's latency shows up
honestly in the results:

    ANNOUNCE    origin broadcasts an anonymised CFP to eligible peers
    BID         peers evaluate privately and PROPOSE or REFUSE, in parallel
    DELIBERATE  refusals are checked against the shared ledger; contradicted
                ones are challenged, defended, and resolved by grounded
                argumentation semantics
    AWARD       origin scores the surviving bids, reserves at the winner
    CONFIRM     patient travels; on arrival the reservation becomes occupancy

DELIBERATE is the phase that distinguishes this from a textbook Contract Net,
and it is there for one reason: in a plain sealed-bid auction a hospital can
decline any patient it does not want, give any excuse it likes, and never be
contradicted. The deliberation phase makes a refusal something you have to be
able to *justify*. See `argumentation.py` and `challenge.py`.

For a fully honest network the phase changes no outcome -- every honest refusal
is defensible, so every challenge is defeated and the same hospital wins. That
is asserted in the tests. Its value appears exactly when someone is not being
straight, which is the case the original evaluation could not model at all.

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
from ..core.types import Acuity
from ..fairness.metrics import FairnessLedger
from ..privacy.anonymizer import PrivacyAudit
from .argumentation import ArgKind, Argument, ArgumentationFramework, Label, build_attacks
from .challenge import ChallengeEngine
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

    # -- deliberation ------------------------------------------------------ #
    #: Challenge refusals the ledger contradicts. Off = plain Contract Net,
    #: which is the ablation arm the paper reports against.
    enable_argumentation: bool = True
    #: Deliberation is one extra exchange, in parallel like the bid round.
    deliberation_minutes: float = 0.75
    #: A hospital may object to its own bid on burden grounds only when it is
    #: this far over its capacity-weighted fair share.
    burden_objection_threshold: float = 0.35
    #: ...and never for a patient this sick, and never when it is the only
    #: hospital that can help. Fairness must not cost a critical patient a bed.
    burden_objection_max_acuity: int = int(Acuity.EMERGENT)
    #: Two options count as clinically comparable within this many minutes of
    #: each other. A burden objection is only allowed when a comparable
    #: alternative exists, so load balancing can never add real delay.
    comparable_minutes: float = 15.0


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
    #: The dispute, if there was one. Kept on the outcome so the ledger and the
    #: UI can both show not just who won but what was argued.
    framework: ArgumentationFramework | None = None
    challenges_raised: int = 0
    refusals_overruled: int = 0


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
        # Reads the public directory and the ledger to find refusals the record
        # contradicts. The directory is genuinely public information -- which
        # hospital runs which service is published, not confidential -- so
        # building it here discloses nothing that was private.
        self.challenges = ChallengeEngine(
            ledger,
            registry={
                hid: {s.value for s in getattr(h, "specialties", set())}
                for hid, h in hospitals.items()
            },
            resource_registry={
                hid: {r.value for r in getattr(h, "resources", None).pools}
                for hid, h in hospitals.items()
                if getattr(h, "resources", None) is not None
            },
        )
        self.deliberations = 0
        self.total_challenges = 0
        self.total_overruled = 0
        self.total_burden_objections = 0

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

    # -- phase 2b: deliberate ---------------------------------------------- #
    def deliberate(
        self, req: TransferRequest, bids: list[Bid], now: float
    ) -> tuple[list[Bid], ArgumentationFramework]:
        """Check refusals against the record; resolve the resulting dispute.

        Returns the revised bid list and the framework that produced it. The
        revision is monotone in one direction only: a refusal that cannot be
        defended is replaced by the bid that hospital would have made had it
        told the truth. A hospital is never compelled to accept a patient it
        genuinely cannot take -- if the truthful assessment is also a refusal,
        the refusal stands.
        """
        af = ArgumentationFramework()
        # Sanitise without counting a boundary crossing: this is the *same*
        # payload already published in the CFP, reused to recompute a truthful
        # bid if a refusal is struck out. Counting it again would inflate the
        # privacy metric with a disclosure that never happened.
        public = self.privacy.anonymizer.sanitize_outbound(req.public_view())
        n_feasible = sum(1 for b in bids if b.feasible)

        # 1. Everyone's opening statement.
        for bid in bids:
            if bid.feasible:
                af.add(Argument(
                    arg_id=f"bid:{bid.bidder}",
                    kind=ArgKind.BID, speaker=bid.bidder, subject=bid.bidder,
                    claim=(f"{bid.bidder} can take this patient; care would start in "
                           f"{bid.time_to_care:.0f} minutes."),
                ))
            else:
                af.add(Argument(
                    arg_id=f"refusal:{bid.bidder}",
                    kind=ArgKind.REFUSAL, speaker=bid.bidder, subject=bid.bidder,
                    claim=_refusal_sentence(bid.bidder, bid.refusal_reason),
                    machine_reason=bid.refusal_reason,
                ))

        # 2. The origin checks each refusal against the shared ledger, and
        #    challenges the ones it contradicts.
        # Every capacity refusal goes on the record before it is examined.
        # A refusal you cannot be held to is not worth making.
        if self.ledger is not None and hasattr(self.ledger, "record_refusal"):
            for bid in bids:
                if not bid.feasible and bid.refusal_reason == "at_self_protection_reserve":
                    self.ledger.record_refusal(
                        hospital=bid.bidder,
                        resource=req.resource.value,
                        request_id=req.request_id,
                        reason=bid.refusal_reason,
                        at=now,
                    )

        for bid in bids:
            if bid.feasible:
                continue
            for challenge in self.challenges.challenges_for(bid, req, now):
                af.add(Argument(
                    arg_id=f"challenge:{bid.bidder}",
                    kind=ArgKind.CHALLENGE, speaker=req.origin, subject=bid.bidder,
                    claim=challenge.claim,
                    machine_reason=challenge.refusal_reason,
                    evidence=challenge.evidence_ids,
                ))
                self.total_challenges += 1
                self._emit("challenge", {
                    "request_id": req.request_id,
                    "challenger": req.origin,
                    "target": bid.bidder,
                    **challenge.as_dict(),
                })

                # 3. ...and the challenged hospital answers for itself.
                if self.hospitals[bid.bidder].defend(req.request_id, bid.refusal_reason):
                    af.add(Argument(
                        arg_id=f"defence:{bid.bidder}",
                        kind=ArgKind.DEFENCE, speaker=bid.bidder, subject=bid.bidder,
                        claim=(f"{bid.bidder} stands by its refusal: its own capacity "
                               f"record supports it. Circumstances changed since that "
                               f"earlier admission."),
                        machine_reason=bid.refusal_reason,
                    ))
                    self._emit("defence", {
                        "request_id": req.request_id,
                        "defender": bid.bidder,
                        "upheld": True,
                        "reason": bid.refusal_reason,
                    })
                else:
                    self._emit("defence", {
                        "request_id": req.request_id,
                        "defender": bid.bidder,
                        "upheld": False,
                        "reason": bid.refusal_reason,
                    })

        # 4. A hospital carrying well over its share may object to its own bid
        #    -- but only for a patient who is not critical, and only when
        #    somebody else can take them. Fairness never costs a bed.
        if self.weights.fairness_enabled and n_feasible > 1 \
                and int(req.acuity) <= self.config.burden_objection_max_acuity:
            feasible = [b for b in bids if b.feasible]
            for bid in bids:
                if not bid.feasible:
                    continue
                credit = self.fairness.credit(bid.bidder)
                if credit <= self.config.burden_objection_threshold:
                    continue
                # The objection only stands if somebody else can take this
                # patient at *comparable* speed. A fairness argument that sends
                # a patient materially further is not a fairness argument, it
                # is a delay, and the clinical constraint outranks it.
                alternatives = [
                    b for b in feasible
                    if b.bidder != bid.bidder
                    and b.time_to_care <= bid.time_to_care + self.config.comparable_minutes
                ]
                if not alternatives:
                    continue
                af.add(Argument(
                    arg_id=f"burden:{bid.bidder}",
                    kind=ArgKind.BURDEN_OBJECTION,
                    speaker=bid.bidder, subject=bid.bidder,
                    claim=(f"{bid.bidder} can take this patient but asks not to: it is "
                           f"{credit:.0%} over its fair share of recent transfers, and "
                           f"this patient is not critical."),
                    machine_reason="over_fair_share",
                ))
                self.total_burden_objections += 1
                self._emit("burden_objection", {
                    "request_id": req.request_id,
                    "objector": bid.bidder,
                    "credit": round(credit, 3),
                })

        build_attacks(af)
        labels = af.grounded_labelling()

        # 5. Apply the verdict.
        revised: list[Bid] = []
        overruled = 0
        for bid in bids:
            refusal_id = f"refusal:{bid.bidder}"
            bid_id = f"bid:{bid.bidder}"
            if refusal_id in labels and labels[refusal_id] is Label.OUT:
                # The refusal was struck out. Hold this hospital to the bid it
                # would have made had it been straight with us.
                truthful = self.hospitals[bid.bidder].truthful_bid(
                    public, self.travel_time(req.origin, bid.bidder), now)
                if truthful.feasible:
                    overruled += 1
                    self.total_overruled += 1
                    self._emit("overruled", {
                        "request_id": req.request_id,
                        "bidder": bid.bidder,
                        "was": bid.refusal_reason,
                        "time_to_care": round(truthful.time_to_care, 1),
                    })
                revised.append(truthful)
            elif bid_id in labels and labels[bid_id] is Label.OUT:
                # The hospital's own burden objection stood: it drops out. Note
                # this is a *withdrawal*, not an inability -- the record should
                # show it could have taken the patient and asked not to.
                revised.append(_withdraw(bid, "withdrew_over_fair_share"))
            else:
                revised.append(bid)

        self.deliberations += 1
        self._emit("deliberation", {
            "request_id": req.request_id,
            "arguments": af.transcript(),
            "summary": af.summary(),
            "overruled": overruled,
        })
        return revised, af

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
            specialty=req.specialty,
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

            # Deliberation: check the refusals against the shared record before
            # accepting them. Costs one extra parallel exchange.
            framework = None
            if self.config.enable_argumentation:
                before = self.total_overruled
                bids, framework = self.deliberate(req, bids, now)
                now += self.config.deliberation_minutes
                overruled_here = self.total_overruled - before
            else:
                overruled_here = 0

            outcome = self.award(req, bids, now)
            outcome.rounds = round_no
            outcome.framework = framework
            outcome.refusals_overruled = overruled_here
            if framework is not None:
                outcome.challenges_raised = len(
                    [a for a in framework.arguments.values()
                     if a.kind is ArgKind.CHALLENGE])
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


# --------------------------------------------------------------------------- #
# Plain-English phrasing for the arguments. The UI shows these verbatim, and so
# does the ledger rationale, so they are part of the system rather than
# decoration -- an audit trail nobody can read explains nothing.
# --------------------------------------------------------------------------- #

_REFUSAL_SENTENCES = {
    "no_specialty_capability": "{h} says it cannot treat this specialty.",
    "resource_not_offered": "{h} says it does not have this facility at all.",
    "at_self_protection_reserve": "{h} says it is down to its last unit and is "
                                  "holding it for its own patients.",
    "cannot_meet_clinical_deadline": "{h} says the patient could not reach it in time.",
    "no_statement_on_record": "{h} gave no reason.",
}


def _refusal_sentence(hospital: str, reason: str) -> str:
    template = _REFUSAL_SENTENCES.get(reason, "{h} declined ({r}).")
    return template.format(h=hospital, r=reason.replace("_", " "))


def _withdraw(bid: Bid, reason: str) -> Bid:
    """Turn a feasible bid into a withdrawal, preserving the audit fields.

    Used when a hospital's own burden objection survives the dispute: it could
    have taken the patient, and said so, and then asked not to be chosen. That
    is a different fact from "could not", and the record should show it.
    """
    bid.feasible = False
    bid.refusal_reason = reason
    return bid
