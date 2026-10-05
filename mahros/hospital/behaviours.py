"""How a hospital decides what to *say* -- which is not always what is true.

The gap this closes
-------------------
Every result in the original evaluation assumed hospitals bid honestly. That
assumption is the weakest thing in the design, and `docs/MODEL_ASSUMPTIONS.md`
already flagged it as the most important unmodelled behaviour. It matters
because the incentive runs the wrong way: accepting a transfer costs the
receiving hospital a scarce bed and gains it very little, so the cheapest
rational move is to claim you cannot help.

A protocol that only works when everyone is honest is not a protocol. It is an
agreement.

The separation that makes this tractable
----------------------------------------
Each hospital always computes a **truthful assessment** of what it could do.
A *bidding policy* then decides what to report. Nothing else in the system ever
sees the truthful assessment -- but the truth is retained, because it is what
the hospital must fall back on if a refusal is challenged and it has to defend
itself. That single design choice gives the whole mechanism its bite:

    An honest refusal can always be defended. A fabricated one cannot.

Three policies
--------------
``HonestPolicy``      reports exactly what it assessed. The original model.
``StrategicPolicy``   fabricates a refusal when a patient looks expensive and
                      it is already uncomfortable. This is the adversary.
``DefensivePolicy``   never lies, but holds a larger self-protection reserve
                      than the network norm. Honest-but-unhelpful, and its
                      refusals *are* defensible -- which is the point. Not every
                      "no" is a lie, and a mechanism that cannot tell the
                      difference would be worse than useless.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.types import Acuity, TransferRequest

if TYPE_CHECKING:  # pragma: no cover
    from .agents import AgentAssessment


@dataclass
class ReportedAssessment:
    """What the hospital says, plus a private record of whether it was true.

    `truthful` is never transmitted anywhere. It exists so the simulation can
    measure how often the mechanism catches a lie, which is the entire point of
    the experiment. Reading it from inside the negotiation would be cheating.
    """

    reported: "AgentAssessment"
    truthful: "AgentAssessment"
    misreported: bool = False
    fabricated_reason: str = ""

    @property
    def is_honest(self) -> bool:
        return not self.misreported


class BiddingPolicy:
    """Base policy: decides what a hospital reports, and whether it can defend it."""

    name = "honest"
    #: Does this policy ever state something its own assessment contradicts?
    can_lie = False

    def report(
        self,
        truth: "AgentAssessment",
        req: TransferRequest,
        hospital,
        now: float,
        rng: random.Random,
    ) -> ReportedAssessment:
        return ReportedAssessment(reported=truth, truthful=truth)

    def defend(self, reported: ReportedAssessment, machine_reason: str) -> bool:
        """Can this hospital defend a challenged refusal?

        A refusal is defensible exactly when the hospital's own truthful
        assessment agrees with the reason it gave. That is not a modelling
        convenience -- it is what "defensible" means. A hospital asked to
        justify a refusal must show its own state supports it, and a fabricated
        refusal has no such state to show.

        **This is an optimistic assumption and it should be named as one.** It
        says a lying hospital simply cannot mount a defence, when in reality it
        could just repeat the lie. The attested variant below removes the
        assumption rather than relying on it.
        """
        if reported.misreported:
            return False
        return not reported.truthful.feasible

    def will_attest(
        self,
        reported: ReportedAssessment,
        rng: random.Random,
        deterrence: float = 1.0,
    ) -> bool:
        """Will this hospital put a co-signed, non-repudiable signature on it?

        This replaces the free win `defend` grants the mechanism. Instead of
        assuming a liar cannot defend, the attested protocol asks whether a liar
        will *sign* -- an institutional question with an explicit, swept answer,
        rather than an assumption buried in the simulator.

        An honest hospital always signs: the claim is true, so signing costs it
        nothing.
        """
        return True


class HonestPolicy(BiddingPolicy):
    """Reports its assessment unchanged. The original, and the control arm."""

    name = "honest"


class StrategicPolicy(BiddingPolicy):
    """Refuses patients it does not want, and gives a reason it has not got.

    Strategic refusal is not modelled as random noise. It is modelled the way it
    would actually happen: a hospital that is already uncomfortable, looking at
    a patient who will occupy a scarce bed for a long time, decides it would
    rather someone else took this one.

    The fabricated reason is chosen to be *plausible* -- the reasons a real bed
    manager would give, and the ones hardest to disprove without a shared
    record. That is precisely why the ledger-backed challenge matters.
    """

    name = "strategic"
    can_lie = True

    #: Defaults. Both are declared modelling assumptions and both are swept in
    #: `experiments/adversarial.py` -- the strength of the adversary must never
    #: be a free parameter chosen to flatter the defence.
    DEFAULT_COMFORT_THRESHOLD = 0.55
    DEFAULT_EXPENSIVE_LOS = 1440.0        # 24 h
    DEFAULT_SHIRK_PROB = 1.0

    def __init__(
        self,
        shirk_prob: float = DEFAULT_SHIRK_PROB,
        comfort_threshold: float = DEFAULT_COMFORT_THRESHOLD,
        expensive_los: float = DEFAULT_EXPENSIVE_LOS,
    ) -> None:
        #: P(fabricate | the hospital would rather not take this patient).
        #: 1.0 is a hospital with a consistent protective policy, which is more
        #: realistic than a coin flip: bed managers are not random.
        self.shirk_prob = shirk_prob
        #: Occupancy above which it starts protecting capacity.
        self.comfort_threshold = comfort_threshold
        #: A stay longer than this makes a patient "expensive" to accept.
        self.expensive_los = expensive_los

    def will_attest(
        self,
        reported: ReportedAssessment,
        rng: random.Random,
        deterrence: float = 1.0,
    ) -> bool:
        """Will a hospital that just lied also *sign* the lie?

        This is the question the unattested protocol quietly assumed away. An
        honest refusal is signed without hesitation. A fabricated one is a
        different proposition: putting a co-signed, non-repudiable statement
        behind it converts a deniable phone-call excuse into durable,
        attributable evidence that the hospital's own later acceptances may
        falsify.

        `deterrence` is the probability it declines rather than signs falsely,
        and it is a **declared assumption about institutional behaviour** -- not
        something any dataset measures. It is swept end to end in
        `experiments/attestation.py`, including the pessimistic extreme where
        signing deters nobody.

        The two failure directions are both interesting and both reported:
        at high deterrence the mechanism prevents the refusal; at low deterrence
        it fails to prevent but manufactures the signed evidence that makes the
        refusal enforceable after the fact.
        """
        if not reported.misreported:
            return True                      # true claim, no reason not to sign
        return rng.random() >= deterrence

    def _wants_out(self, truth: "AgentAssessment", req: TransferRequest) -> bool:
        if not truth.feasible:
            return False                     # already refusing, honestly
        if req.acuity >= Acuity.LIFE_THREATENING:
            return False                     # nobody games a crash call
        expensive = req.expected_los_minutes >= self.expensive_los
        uncomfortable = truth.post_accept_strain >= self.comfort_threshold
        return expensive or uncomfortable

    def report(
        self,
        truth: "AgentAssessment",
        req: TransferRequest,
        hospital,
        now: float,
        rng: random.Random,
    ) -> ReportedAssessment:
        from .agents import AgentAssessment

        if not self._wants_out(truth, req) or rng.random() >= self.shirk_prob:
            return ReportedAssessment(reported=truth, truthful=truth)

        # Pick the excuse. A hospital that is *not* listed for this specialty
        # can plausibly disclaim capability -- it is only stabilising anyway.
        # A hospital that plainly is a centre for it would be caught instantly
        # by the public directory, so it reaches for the capacity excuse
        # instead: unverifiable from outside, and the one every bed manager
        # already uses. Modelling a competent liar rather than a careless one
        # is what makes the evaluation worth anything.
        if truth.capability_match < 1.0:
            reason = "no_specialty_capability"
        else:
            reason = "at_self_protection_reserve"

        return ReportedAssessment(
            reported=AgentAssessment(feasible=False, reason=reason),
            truthful=truth,
            misreported=True,
            fabricated_reason=reason,
        )


class DefensivePolicy(BiddingPolicy):
    """Honest, but keeps more in reserve than the network norm.

    This is the control that stops the adversarial experiment from being a
    strawman. A defensive hospital refuses more often than an honest one and
    *every one of its refusals is true and defensible*. If the challenge
    mechanism punished it, the mechanism would be broken -- it would be
    penalising caution rather than deceit. The experiment reports the false-
    accusation rate against this arm specifically.
    """

    name = "defensive"

    def __init__(self, extra_reserve: int = 2) -> None:
        self.extra_reserve = extra_reserve

    def report(
        self,
        truth: "AgentAssessment",
        req: TransferRequest,
        hospital,
        now: float,
        rng: random.Random,
    ) -> ReportedAssessment:
        from .agents import AgentAssessment

        if not truth.feasible or req.acuity >= Acuity.CRITICAL:
            return ReportedAssessment(reported=truth, truthful=truth)

        pool = hospital.resources.pool(req.resource)
        if pool is not None and pool.available <= self.extra_reserve:
            # A real, stated policy: this hospital holds more back. Its own
            # assessment agrees, so the refusal is defensible.
            honest_refusal = AgentAssessment(False, "at_self_protection_reserve")
            return ReportedAssessment(reported=honest_refusal, truthful=honest_refusal)
        return ReportedAssessment(reported=truth, truthful=truth)


POLICIES: dict[str, type[BiddingPolicy]] = {
    "honest": HonestPolicy,
    "strategic": StrategicPolicy,
    "defensive": DefensivePolicy,
}


class ColludingPolicy(StrategicPolicy):
    """A strategic hospital that coordinates its refusals with a cartel.

    It lies for the same reasons a `StrategicPolicy` does. The difference is
    what it does when it would rather *accept*: if taking this patient would
    contradict a refusal another cartel member made recently, it refuses
    instead, keeping the shared story clean.

    That is the whole attack. The ledger challenge needs one hospital to refuse
    a resource and then accept it; a cartel that abstains in concert never
    produces that pair, so its refusals are individually unimpeachable however
    dishonest they are.

    The abstention is a real cost, not a free win: the cartel gives up patients
    it wanted and had room for. `Cartel.abstentions` counts them.
    """

    name = "colluding"
    can_lie = True

    def __init__(self, cartel, **kw) -> None:
        super().__init__(**kw)
        self.cartel = cartel

    def report(self, truth, req, hospital, now, rng):
        resource = req.resource.value

        # Ordinary strategic behaviour first.
        out = super().report(truth, req, hospital, now, rng)
        if out.misreported or not out.reported.feasible:
            self.cartel.note_refusal(resource, now)
            return out

        # It would have accepted. Would accepting expose a partner?
        if self.cartel.must_abstain(resource, now):
            self.cartel.note_abstention()
            self.cartel.note_refusal(resource, now)
            refused = ReportedAssessment(
                reported=_refused_like(truth, "at_self_protection_reserve"),
                truthful=truth,
                misreported=True,
                fabricated_reason="at_self_protection_reserve",
            )
            return refused
        return out


def _refused_like(truth, reason: str):
    """A copy of an assessment turned into a refusal with `reason`."""
    import copy as _copy
    fake = _copy.copy(truth)
    fake.feasible = False
    fake.refusal_reason = reason
    return fake


def assign_policies(
    hospital_ids: list[str],
    strategic_fraction: float = 0.0,
    defensive_fraction: float = 0.0,
    shirk_prob: float = StrategicPolicy.DEFAULT_SHIRK_PROB,
    comfort_threshold: float = StrategicPolicy.DEFAULT_COMFORT_THRESHOLD,
    seed: int = 0,
    incentive_rank: dict[str, float] | None = None,
    collusion_size: int = 0,
    cartel=None,
) -> dict[str, BiddingPolicy]:
    """Deterministically assign behaviour policies across the network.

    Two properties this has to have, and both matter for the experiment:

    **Nested.** Raising the strategic fraction must *add* liars to the existing
    set, never reshuffle which hospitals are lying. Otherwise the adversarial
    sweep confounds dose with identity and the resulting curve means nothing.

    **Incentive-ordered.** Strategic behaviour is assigned to the hospitals
    with the most to gain from it first. That is not pessimism, it is the
    structure of the problem: the tertiary centre holding the region's only
    cath lab is asked for everything, is permanently near capacity, and is the
    one for whom "we're full" saves the most. Assigning shirking uniformly at
    random would understate the risk by putting it on small hospitals nobody
    asks. `incentive_rank` supplies that ordering (the runner passes tier);
    ties break on a seeded shuffle so the choice is not alphabetical.
    """
    ids = sorted(hospital_ids)
    rng = random.Random(seed)
    jitter = {hid: rng.random() for hid in ids}
    rank = incentive_rank or {}
    ordered = sorted(ids, key=lambda h: (-rank.get(h, 0.0), jitter[h]))

    # The cartel forms among the hospitals with the most to gain, which is the
    # same ordering strategic behaviour uses: the tertiary centres everyone asks
    # for everything. A cartel of small hospitals nobody calls would be
    # harmless, and modelling one would understate the risk.
    colluders = ordered[:collusion_size] if collusion_size else []

    n_strategic = int(round(len(ids) * max(0.0, min(1.0, strategic_fraction))))
    n_defensive = int(round(len(ids) * max(0.0, min(1.0, defensive_fraction))))
    n_defensive = min(n_defensive, len(ids) - n_strategic)

    out: dict[str, BiddingPolicy] = {}
    for i, hid in enumerate(ordered):
        if hid in colluders and cartel is not None:
            out[hid] = ColludingPolicy(cartel, shirk_prob=shirk_prob,
                                       comfort_threshold=comfort_threshold)
        elif i < n_strategic:
            out[hid] = StrategicPolicy(shirk_prob=shirk_prob,
                                       comfort_threshold=comfort_threshold)
        elif i < n_strategic + n_defensive:
            out[hid] = DefensivePolicy()
        else:
            out[hid] = HonestPolicy()
    return out
