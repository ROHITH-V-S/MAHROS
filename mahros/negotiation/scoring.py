"""Award scoring: how the requesting hospital picks a winner among bids.

Deliberately transparent and deterministic. The LLM does **not** choose the
winner in the default configuration -- if it did, your results would not be
reproducible and a reviewer would (rightly) reject the evaluation. The LLM's
role is explanation, and, under an explicit ablation flag, tie-breaking within
a near-equal band. See llm/coordinator.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Acuity, Bid, TransferRequest
from ..fairness.metrics import FairnessLedger


@dataclass
class ScoringWeights:
    """Higher score = better. All terms normalised to roughly [0, 1]."""

    w_time: float = 0.45          # speed to definitive care -- the clinical driver
    w_capability: float = 0.25    # specialty fit
    w_strain: float = 0.15        # prefer the peer who stays comfortable after accepting
    w_fairness: float = 0.15      # burden balancing
    fairness_enabled: bool = True

    def normalised(self) -> "ScoringWeights":
        if not self.fairness_enabled:
            total = self.w_time + self.w_capability + self.w_strain
            return ScoringWeights(
                w_time=self.w_time / total,
                w_capability=self.w_capability / total,
                w_strain=self.w_strain / total,
                w_fairness=0.0,
                fairness_enabled=False,
            )
        return self


@dataclass
class ScoredBid:
    bid: Bid
    score: float
    time_term: float
    capability_term: float
    strain_term: float
    fairness_term: float
    slack_minutes: float          # how much of the safe window is left on arrival

    def explain(self) -> str:
        return (
            f"{self.bid.bidder}: score={self.score:.3f} "
            f"(time={self.time_term:.2f} cap={self.capability_term:.2f} "
            f"strain={self.strain_term:.2f} fair={self.fairness_term:+.2f}) "
            f"ETA={self.bid.time_to_care:.0f}min slack={self.slack_minutes:.0f}min"
        )


def score_bids(
    req: TransferRequest,
    bids: list[Bid],
    now: float,
    weights: ScoringWeights,
    fairness: FairnessLedger | None = None,
) -> list[ScoredBid]:
    """Score all feasible bids, best first.

    Hard constraints are applied before scoring: an infeasible bid or one that
    cannot beat the clinical deadline is discarded, never traded off. Fairness
    can reorder acceptable options; it can never make an unsafe one win.
    """
    w = weights.normalised()
    feasible = [b for b in bids if b.feasible]
    if not feasible:
        return []

    window = max(1.0, req.expires_at - req.created_at)
    times = [b.time_to_care for b in feasible]
    t_min, t_max = min(times), max(times)
    t_span = max(t_max - t_min, 1e-6)

    scored: list[ScoredBid] = []
    for b in feasible:
        arrival = now + b.time_to_care
        slack = req.expires_at - arrival
        if slack < 0:
            continue  # hard clinical constraint, not a soft penalty

        # Relative speed among the actual candidates, blended with absolute
        # safety margin so a field of uniformly slow bids is not flattered.
        rel = 1.0 - (b.time_to_care - t_min) / t_span
        absolute = max(0.0, min(1.0, slack / window))
        time_term = 0.6 * rel + 0.4 * absolute

        capability_term = b.capability_match
        strain_term = max(0.0, 1.0 - b.post_accept_strain)

        fairness_term = 0.0
        if w.fairness_enabled and fairness is not None:
            # credit > 0 => already over-burdened => penalise.
            fairness_term = -fairness.credit(b.bidder)

        score = (
            w.w_time * time_term
            + w.w_capability * capability_term
            + w.w_strain * strain_term
            + w.w_fairness * fairness_term
        )

        # For the sickest patients, collapse toward pure speed: equity must not
        # cost a life-threatening patient minutes. This is an ethical guardrail
        # and should be stated explicitly in the paper.
        if req.acuity >= Acuity.CRITICAL:
            score = 0.75 * (w.w_time * time_term + w.w_capability * capability_term) \
                    / max(w.w_time + w.w_capability, 1e-6) + 0.25 * score

        scored.append(
            ScoredBid(
                bid=b,
                score=score,
                time_term=time_term,
                capability_term=capability_term,
                strain_term=strain_term,
                fairness_term=fairness_term,
                slack_minutes=slack,
            )
        )

    scored.sort(key=lambda s: (-s.score, s.bid.time_to_care, s.bid.bidder))
    return scored


def is_contested(scored: list[ScoredBid], band: float = 0.05) -> bool:
    """True when the top bids are close enough that the choice is a judgement call.

    This is the trigger for escalating to the LLM coordinator.
    """
    if len(scored) < 2:
        return False
    return (scored[0].score - scored[1].score) < band
