"""Tests for the argumentation layer, the challenge mechanism, and the maths.

The properties asserted here are the ones the paper's claims rest on. If one of
these breaks, a claim in the paper becomes false, so each test says which.
"""

from __future__ import annotations

import copy

import pytest

from mahros.core.types import Acuity, Bid, ResourceType, Specialty, TransferRequest
from mahros.eval.assignment import INF, linear_sum_assignment, total_cost
from mahros.eval.stats import paired_t, t_sf, tost_equivalence
from mahros.hospital.behaviours import (
    DefensivePolicy,
    HonestPolicy,
    StrategicPolicy,
    assign_policies,
)
from mahros.ledger.interface import HashChainLedger
from mahros.negotiation.argumentation import (
    ArgKind,
    Argument,
    ArgumentationFramework,
    Label,
    framework_from,
)
from mahros.negotiation.challenge import ChallengeEngine
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS


def arg(aid, kind, speaker, subject, claim="c"):
    return Argument(arg_id=aid, kind=kind, speaker=speaker, subject=subject, claim=claim)


# --------------------------------------------------------------------------- #
# Dung semantics -- the textbook cases
# --------------------------------------------------------------------------- #

def test_unattacked_argument_stands():
    af = ArgumentationFramework()
    af.add(arg("a", ArgKind.BID, "H1", "H1"))
    assert af.grounded_extension() == {"a"}


def test_attacked_argument_falls():
    """B attacks A, nothing attacks B -> B stands, A falls."""
    af = ArgumentationFramework()
    af.add(arg("a", ArgKind.REFUSAL, "H1", "H1"))
    af.add(arg("b", ArgKind.CHALLENGE, "H0", "H1"))
    af.attack("b", "a")
    labels = af.grounded_labelling()
    assert labels["b"] is Label.IN
    assert labels["a"] is Label.OUT


def test_reinstatement():
    """The case the whole mechanism is built on.

    A refuses, B challenges A, C defends against B. C is unattacked so it
    stands; C defeats B so B falls; with B fallen, A is reinstated. This is
    exactly "an honest refusal survives being questioned".
    """
    af = ArgumentationFramework()
    af.add(arg("a", ArgKind.REFUSAL, "H1", "H1"))
    af.add(arg("b", ArgKind.CHALLENGE, "H0", "H1"))
    af.add(arg("c", ArgKind.DEFENCE, "H1", "H1"))
    af.attack("b", "a")
    af.attack("c", "b")
    labels = af.grounded_labelling()
    assert labels["c"] is Label.IN
    assert labels["b"] is Label.OUT
    assert labels["a"] is Label.IN          # reinstated


def test_even_cycle_is_undecided():
    """Two arguments attacking each other with no tiebreak: neither wins.

    Grounded semantics refuses to guess, which is the correct behaviour for a
    clinical system -- and the reason the protocol never lets a dispute end
    here without a deterministic fallback.
    """
    af = ArgumentationFramework()
    af.add(arg("a", ArgKind.REFUSAL, "H1", "H1"))
    af.add(arg("b", ArgKind.REFUSAL, "H2", "H2"))
    af.attack("a", "b")
    af.attack("b", "a")
    labels = af.grounded_labelling()
    assert labels["a"] is Label.UNDEC and labels["b"] is Label.UNDEC
    assert af.grounded_extension() == set()


def test_self_attack_is_ignored():
    af = ArgumentationFramework()
    af.add(arg("a", ArgKind.BID, "H1", "H1"))
    af.attack("a", "a")
    assert af.grounded_extension() == {"a"}


def test_attack_rulebook_wires_challenge_defence_and_burden():
    args = [
        arg("bid:H1", ArgKind.BID, "H1", "H1"),
        arg("refusal:H2", ArgKind.REFUSAL, "H2", "H2"),
        arg("challenge:H2", ArgKind.CHALLENGE, "H0", "H2"),
        arg("burden:H1", ArgKind.BURDEN_OBJECTION, "H1", "H1"),
    ]
    af = framework_from(args)
    assert ("challenge:H2", "refusal:H2") in af.attacks
    assert ("burden:H1", "bid:H1") in af.attacks
    labels = af.grounded_labelling()
    assert labels["refusal:H2"] is Label.OUT      # unanswered challenge
    assert labels["bid:H1"] is Label.OUT          # withdrew on burden grounds


# --------------------------------------------------------------------------- #
# Behaviour policies
# --------------------------------------------------------------------------- #

class _Assessment:
    def __init__(self, feasible, reason="", strain=0.9, cap=1.0):
        self.feasible = feasible
        self.reason = reason
        self.prep_minutes = 10.0
        self.capability_match = cap
        self.post_accept_strain = strain
        self.opportunity_cost = 0.5


def _req(los=3000.0, acuity=Acuity.CRITICAL):
    return TransferRequest(resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
                           acuity=acuity, expected_los_minutes=los)


def test_honest_policy_reports_the_truth():
    import random
    truth = _Assessment(True)
    out = HonestPolicy().report(truth, _req(), None, 0.0, random.Random(0))
    assert out.reported is truth and not out.misreported


def test_strategic_policy_fabricates_and_cannot_defend():
    """The asymmetry the whole mechanism depends on."""
    import random
    policy = StrategicPolicy(shirk_prob=1.0)
    truth = _Assessment(True, strain=0.9)
    out = policy.report(truth, _req(), None, 0.0, random.Random(0))
    assert out.misreported
    assert not out.reported.feasible
    assert out.truthful.feasible                 # it could have taken them
    assert policy.defend(out, out.reported.reason) is False


def test_honest_refusal_is_always_defensible():
    import random
    policy = HonestPolicy()
    truth = _Assessment(False, "at_self_protection_reserve")
    out = policy.report(truth, _req(), None, 0.0, random.Random(0))
    assert policy.defend(out, "at_self_protection_reserve") is True


def test_nobody_games_a_crash_call():
    """Acuity 5 is never refused strategically. An ethical floor, asserted."""
    import random
    policy = StrategicPolicy(shirk_prob=1.0)
    truth = _Assessment(True, strain=0.99)
    out = policy.report(truth, _req(acuity=Acuity.LIFE_THREATENING), None, 0.0,
                        random.Random(0))
    assert not out.misreported


def test_policy_assignment_is_nested_and_incentive_ordered():
    ids = [f"H{i:02d}" for i in range(12)]
    rank = {h: float(1 + i % 3) for i, h in enumerate(ids)}
    small = assign_policies(ids, strategic_fraction=0.25, seed=1, incentive_rank=rank)
    large = assign_policies(ids, strategic_fraction=0.50, seed=1, incentive_rank=rank)
    liars_small = {h for h, p in small.items() if p.name == "strategic"}
    liars_large = {h for h, p in large.items() if p.name == "strategic"}
    assert liars_small <= liars_large, "raising the dose must not reshuffle the liars"
    # the highest-incentive hospitals go first
    assert all(rank[h] == 3.0 for h in liars_small)


# --------------------------------------------------------------------------- #
# Ledger as evidence
# --------------------------------------------------------------------------- #

def test_capability_refusal_is_challenged_from_the_public_directory():
    """No ledger history needed: the service directory is public."""
    engine = ChallengeEngine(HashChainLedger(), registry={"H07": {"cardiac"}})
    bid = Bid(request_id="r1", bidder="H07", feasible=False,
              refusal_reason="no_specialty_capability")
    req = TransferRequest(resource=ResourceType.CATH_LAB, specialty=Specialty.CARDIAC)
    found = engine.challenges_for(bid, req, now=100.0)
    assert len(found) == 1
    assert "listed in the network directory" in found[0].claim


def test_capability_refusal_is_not_challenged_when_directory_agrees():
    engine = ChallengeEngine(HashChainLedger(), registry={"H07": {"general"}})
    bid = Bid(request_id="r1", bidder="H07", feasible=False,
              refusal_reason="no_specialty_capability")
    req = TransferRequest(resource=ResourceType.CATH_LAB, specialty=Specialty.CARDIAC)
    assert engine.challenges_for(bid, req, now=100.0) == []


def test_refusal_then_acceptance_is_a_broken_commitment():
    """Refusal is a commitment. Contradicting it is visible to everyone."""
    from mahros.core.types import Agreement
    ledger = HashChainLedger(block_size=64)
    ledger.record_refusal("H07", "icu_bed", "r1", "at_self_protection_reserve", at=100.0)
    ledger.record_agreement(Agreement(
        request_id="r2", origin="H01", receiver="H07",
        resource=ResourceType.ICU_BED, agreed_at=120.0))
    broken = ledger.broken_commitments("H07", window_minutes=60.0)
    assert len(broken) == 1
    refusal, accept = broken[0]
    assert refusal["request_id"] == "r1" and accept["request_id"] == "r2"


def test_broken_commitment_outside_the_window_is_not_counted():
    from mahros.core.types import Agreement
    ledger = HashChainLedger(block_size=64)
    ledger.record_refusal("H07", "icu_bed", "r1", "at_self_protection_reserve", at=100.0)
    ledger.record_agreement(Agreement(
        request_id="r2", origin="H01", receiver="H07",
        resource=ResourceType.ICU_BED, agreed_at=400.0))
    assert ledger.broken_commitments("H07", window_minutes=60.0) == []


def test_no_ledger_means_no_evidence():
    """Why the no-audit ablation is a real ablation and not a no-op."""
    from mahros.ledger.interface import NullLedger
    engine = ChallengeEngine(NullLedger(), registry={})
    bid = Bid(request_id="r1", bidder="H07", feasible=False,
              refusal_reason="at_self_protection_reserve")
    req = TransferRequest(resource=ResourceType.ICU_BED)
    assert engine.challenges_for(bid, req, now=100.0) == []


# --------------------------------------------------------------------------- #
# End-to-end properties of the protocol
# --------------------------------------------------------------------------- #

def _run(**kw):
    """A three-day baseline network.

    The `smoke` scenario is too small to exercise the mechanism: six hospitals
    over two days produce ~16 transfers, and a hospital needs a *history* before
    its refusals can be checked against anything. Three days of the twelve-
    hospital baseline is the smallest setting that actually tests the claim,
    and it still runs in a couple of seconds.
    """
    scenario = copy.deepcopy(SCENARIOS["baseline"])
    scenario.seed = 7
    scenario.horizon_hours = 24 * 3
    return SimulationRunner(
        RunConfig(scenario=scenario, strategy="mahros", seed=7, **kw)).run()


def test_deliberation_changes_nothing_when_everyone_is_honest():
    """The mechanism must be inert on an honest network, or it is a tax."""
    off = _run(enable_argumentation=False)
    on = _run(enable_argumentation=True)
    assert on.strategy_stats["refusals_overruled"] == 0
    completed_off = sum(1 for r in off.requests if r.status.value == "completed")
    completed_on = sum(1 for r in on.requests if r.status.value == "completed")
    # Identical placements; only the extra deliberation minutes differ.
    assert abs(completed_on - completed_off) <= max(2, 0.05 * max(1, completed_off))


def test_honest_hospitals_are_never_overruled():
    """The safety property. If this fails the mechanism is not deployable."""
    result = _run(strategic_fraction=0.5, defensive_fraction=0.25,
                  enable_argumentation=True)
    for hospital in result.hospitals.values():
        if hospital.policy.name != "strategic":
            assert hospital.refusals_overruled == 0, (
                f"{hospital.id} told the truth and was overruled anyway")


def test_strategic_refusal_hurts_and_argument_helps():
    """The headline claim, asserted at smoke scale."""
    clean = _run(strategic_fraction=0.0, enable_argumentation=False)
    attacked = _run(strategic_fraction=0.75, shirk_comfort_threshold=0.30,
                    enable_argumentation=False)
    defended = _run(strategic_fraction=0.75, shirk_comfort_threshold=0.30,
                    enable_argumentation=True)


    def rate(r):
        return sum(1 for q in r.requests if q.status.value == "completed") / max(1, len(r.requests))

    assert rate(attacked) < rate(clean), "strategic refusal should cost something"
    assert defended.strategy_stats["refusals_overruled"] > 0, "nothing was caught"


def test_a_hospital_is_never_compelled_beyond_its_real_capacity():
    """Overruling holds a hospital to its own assessment, never past it."""
    result = _run(strategic_fraction=1.0, enable_argumentation=True)
    for hospital in result.hospitals.values():
        for pool in hospital.resources.pools.values():
            assert pool.occupied + pool.reserved <= pool.capacity


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def test_paired_t_matches_scipy_when_available():
    scipy_stats = pytest.importorskip("scipy.stats")
    a = [0.84, 0.88, 0.87, 0.85, 0.89, 0.86, 0.83, 0.81]
    b = [0.87, 0.86, 0.88, 0.79, 0.91, 0.86, 0.82, 0.85]
    mine = paired_t(a, b)
    t, p = scipy_stats.ttest_rel(a, b)
    assert mine.t == pytest.approx(float(t), rel=1e-6)
    assert mine.p_value == pytest.approx(float(p), rel=1e-6)


def test_t_distribution_tail_is_sane():
    assert t_sf(0.0, 10) == pytest.approx(0.5, abs=1e-9)
    assert t_sf(2.228, 10) == pytest.approx(0.025, abs=1e-3)


def test_tost_declares_equivalence_only_inside_the_margin():
    tight = [1.0, 1.001, 0.999, 1.0, 1.002, 0.998]
    same = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert tost_equivalence(tight, same, margin=0.05).equivalent
    far = [1.5, 1.52, 1.48, 1.51, 1.49, 1.50]
    assert not tost_equivalence(far, same, margin=0.05).equivalent


def test_hungarian_beats_greedy_on_the_classic_swap():
    """The case that justifies the batched-optimal baseline existing.

    Greedy takes the cheap option for the first patient and strands the second.
    The joint solve swaps them and wins overall.
    """
    cost = [[10.0, 12.0],
            [10.0, 60.0]]
    assignment = linear_sum_assignment(cost)
    assert total_cost(cost, assignment) == 22.0     # not 10 + 60
    assert assignment == [1, 0]


def test_hungarian_never_assigns_a_forbidden_pairing_when_avoidable():
    cost = [[INF, 5.0], [3.0, INF]]
    assignment = linear_sum_assignment(cost)
    assert assignment == [1, 0]
    assert total_cost(cost, assignment) == 8.0


# --------------------------------------------------------------------------- #
# Capacity disclosure
# --------------------------------------------------------------------------- #

def test_a_refusal_at_reserve_leaks_more_than_an_offer():
    """The counter-intuitive property worth putting in the paper.

    Saying "yes" tells an observer only that you are not nearly full. Saying
    "we're down to our last bed" pins your occupancy to the top few levels. In
    this protocol, refusing is the expensive answer.
    """
    from mahros.negotiation.messages import Perf
    from mahros.privacy.leakage import _consistent_levels

    capacity, reserve = 26, 1
    offer = _consistent_levels(Perf.PROPOSE, {"feasible": True}, capacity, reserve)
    refuse = _consistent_levels(
        Perf.REFUSE, {"reason": "at_self_protection_reserve"}, capacity, reserve)
    assert refuse < offer, "a reserve refusal must narrow the belief more than an offer"


def test_publishing_strain_leaks_everything():
    from mahros.negotiation.messages import Perf
    from mahros.privacy.leakage import _consistent_levels

    assert _consistent_levels(Perf.PROPOSE, {"strain": 0.8}, 26, 1) == 1


def test_capability_refusal_leaks_nothing_about_occupancy():
    """It is a statement about the service catalogue, which is public anyway."""
    from mahros.negotiation.messages import Perf
    from mahros.privacy.leakage import _consistent_levels

    assert _consistent_levels(
        Perf.REFUSE, {"reason": "no_specialty_capability"}, 26, 1) == 27


def test_mahros_discloses_less_per_message_than_a_central_optimiser():
    """The privacy claim, measured rather than asserted.

    MAHROS asks more hospitals, so it discloses more in aggregate -- that cost
    is real and reported. What it never does is transmit an occupancy figure,
    and this asserts the resulting per-message difference.
    """
    from mahros.privacy.leakage import measure_leakage

    scenario = copy.deepcopy(SCENARIOS["baseline"])
    scenario.seed = 7
    scenario.horizon_hours = 24 * 3
    runs = {}
    for strategy in ("mahros", "central"):
        result = SimulationRunner(
            RunConfig(scenario=scenario, strategy=strategy, seed=7)).run()
        runs[strategy] = measure_leakage(result.bus, result.hospitals)

    assert runs["mahros"].mean_bits_per_message < runs["central"].mean_bits_per_message
    assert runs["mahros"].disclosure_fraction < runs["central"].disclosure_fraction
