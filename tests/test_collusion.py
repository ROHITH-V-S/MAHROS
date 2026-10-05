"""Tests for coordinated abstention and the spot-check defence.

The load-bearing test here is `test_spot_checks_never_punish_an_honest_refusal`.
Spot checks demand certification from hospitals nobody suspected, which is
exactly the move that could start punishing prudence instead of deceit.

It held for two different reasons, and the second had to be fixed rather than
discovered:

* A **genuinely full** hospital is safe structurally. Striking out a refusal
  substitutes that hospital's *truthful* bid, and a full hospital's truthful
  bid is still a refusal, so nothing changes for it.
* An **honest-but-cautious** hospital was not safe, and this test caught it.
  Holding a declared reserve means it *has* a free unit, so its truthful bid is
  feasible and striking out its refusal forced it to take the patient. The
  cause was a single attestation predicate: asked to certify "no unit
  available", a reserve-holder either signed something false or declined and
  was overruled. Splitting the predicate -- `AT_DECLARED_RESERVE` alongside
  `NO_UNIT_AVAILABLE` -- restored the property, and costs nothing against
  deceit because a liar can honestly assert neither.
"""

from __future__ import annotations

import copy

import pytest

from mahros.hospital.behaviours import ColludingPolicy, assign_policies
from mahros.negotiation.collusion import Cartel, CoRefusalDetector
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS

ATTEST = dict(enable_attestation=True, attestation_deterrence=0.8)

#: Horizon for the tests that measure the SIZE of the collusion effect.
#: Three days is mostly warm-up at a ~4.6 day mean stay, which compresses
#: the gap; the effect is only properly visible near steady state.
#:
#: The thresholds were also lowered when capability became observed rather
#: than guessed from tier. The tier guess concentrated cardiac, neuro and
#: trauma in six large hospitals; the CMS certification record shows 24 of
#: 27 with a cath lab and 21 with neurosurgery. Capability is far more
#: widely distributed than the guess assumed, so a cartel of six leaves
#: many more alternatives -- the old assumption overstated this attack.
EFFECT_DAYS = 10


def _run(days: float = 3, **kw):
    sc = copy.deepcopy(SCENARIOS["houston_surge"])
    sc.horizon_hours = 24 * days
    sc.seed = 42
    result = SimulationRunner(RunConfig(
        scenario=sc, strategy="mahros", seed=42, **kw)).run()
    return result, M.compute(result)


# --------------------------------------------------------------------------- #
# The cartel mechanism
# --------------------------------------------------------------------------- #

def test_cartel_abstains_only_inside_its_window():
    cartel = Cartel(abstain_window=60.0)
    assert not cartel.must_abstain("icu_bed", 100.0)
    cartel.note_refusal("icu_bed", 100.0)
    assert cartel.must_abstain("icu_bed", 130.0)
    assert cartel.must_abstain("icu_bed", 160.0)
    assert not cartel.must_abstain("icu_bed", 161.0)
    # A refusal of one resource says nothing about another.
    assert not cartel.must_abstain("ward_bed", 130.0)


def test_cartel_forms_among_the_hospitals_with_most_to_gain():
    cartel = Cartel()
    ids = [f"H{i:02d}" for i in range(9)]
    rank = {h: float(i % 3 + 1) for i, h in enumerate(ids)}
    pols = assign_policies(ids, collusion_size=3, cartel=cartel,
                           incentive_rank=rank)
    colluders = [h for h, p in pols.items() if p.name == "colluding"]
    assert len(colluders) == 3
    assert all(rank[h] == 3.0 for h in colluders), \
        "cartel should form among the highest-incentive hospitals"


def test_no_cartel_by_default():
    pols = assign_policies([f"H{i:02d}" for i in range(6)])
    assert not any(isinstance(p, ColludingPolicy) for p in pols.values())


# --------------------------------------------------------------------------- #
# The attack works, which is the uncomfortable part
# --------------------------------------------------------------------------- #

def test_coordinated_abstention_beats_the_ledger_challenge():
    """If this ever stops failing, the attack model has been weakened."""
    _, honest = _run(EFFECT_DAYS)
    _, colluding = _run(EFFECT_DAYS, collusion_size=6)
    assert colluding.success_rate < honest.success_rate - 0.05, (
        "coordinated abstention should do real damage; it is the gap the "
        "project's own limitations section named")


def test_cartel_evades_challenge_better_than_uncoordinated_liars():
    """The point of the attack: same dishonesty, far less evidence."""
    loud, _ = _run(strategic_fraction=6 / 27)
    quiet, _ = _run(collusion_size=6)
    caught_loud = sum(h.refusals_overruled for h in loud.hospitals.values())
    caught_quiet = sum(h.refusals_overruled for h in quiet.hospitals.values())
    assert caught_quiet < caught_loud


def test_abstention_costs_the_cartel_patients():
    """Collusion is not free: members turn away patients they could take."""
    result, _ = _run(collusion_size=6)
    assert result.cartel is not None
    assert result.cartel.stats()["abstentions"] > 0


# --------------------------------------------------------------------------- #
# Statistical detection fails, and the negative result is pinned
# --------------------------------------------------------------------------- #

def test_detector_is_purely_observational():
    """It must never be able to influence a decision.

    Request ids are UUIDs and are not reproducible by design, so this compares
    the outcome rather than the identifiers.
    """
    a, ma = _run(collusion_size=6)
    b, mb = _run(collusion_size=6)
    assert a.corefusal is not None
    assert len(a.requests) == len(b.requests)
    assert ma.success_rate == mb.success_rate
    assert ma.mean_wait == mb.mean_wait


def test_lift_needs_enough_observations():
    d = CoRefusalDetector(min_opportunities=30)
    for _ in range(5):
        d.observe(asked=["A", "B"], refused=["A", "B"], now=0.0, resource="icu_bed")
    assert d.lift("A", "B") != d.lift("A", "B")        # NaN: too few
    assert d.follow_on_lift("A", "B") != d.follow_on_lift("A", "B")


def test_independent_refusals_score_about_one():
    d = CoRefusalDetector(min_opportunities=10)
    import random
    rng = random.Random(0)
    for i in range(4000):
        refused = [h for h in ("A", "B") if rng.random() < 0.5]
        d.observe(asked=["A", "B"], refused=refused, now=float(i),
                  resource="icu_bed")
    assert 0.9 < d.lift("A", "B") < 1.1


def test_statistical_detection_does_not_separate_a_cartel():
    """Pinned as a negative result, not hidden.

    Both statistics score the colluding network no higher than the honest one.
    The cartel's signature sits in the marginal refusal rates, which lift
    divides out, and it congests its neighbours so their rates rise too.
    """
    honest, _ = _run()
    colluding, _ = _run(collusion_size=6)
    members = set(colluding.cartel.members)
    d = colluding.corefusal

    scored = [d.follow_on_lift(a, b) for (a, b) in d._follow_opportunities
              if d._follow_opportunities[(a, b)] >= d.min_opportunities]
    inside = [v for (a, b), v in zip(
        [k for k in d._follow_opportunities
         if d._follow_opportunities[k] >= d.min_opportunities], scored)
        if a in members and b in members and v == v]

    if inside:
        assert max(inside) < 1.5, (
            "if a cartel ever becomes statistically separable, the paper's "
            "negative result needs revisiting")


# --------------------------------------------------------------------------- #
# The spot-check defence
# --------------------------------------------------------------------------- #

def test_spot_checks_are_off_by_default():
    result, _ = _run(collusion_size=6)
    assert result.strategy_stats.get("spot_checks", 0) == 0


def test_spot_checks_recover_the_collusion_loss():
    _, no_check = _run(EFFECT_DAYS, collusion_size=6)
    _, checked = _run(EFFECT_DAYS, collusion_size=6,
                      spot_check_rate=0.30, **ATTEST)
    assert checked.success_rate > no_check.success_rate + 0.05


def test_spot_checks_overrule_the_cartel():
    result, _ = _run(collusion_size=6, spot_check_rate=0.30, **ATTEST)
    caught = sum(h.refusals_overruled for h in result.hospitals.values()
                 if h.policy.name == "colluding")
    assert caught > 0
    assert result.strategy_stats["spot_checks"] > 0


@pytest.mark.parametrize("rate", [0.15, 0.30, 0.50])
def test_spot_checks_never_punish_an_honest_refusal(rate):
    """The property that decides whether proactive checking is deployable.

    A mechanism that demanded certification from everyone and then punished the
    genuinely full would be far worse than the problem it solves.
    """
    for kw in ({}, {"defensive_fraction": 0.5}, {"collusion_size": 6}):
        result, _ = _run(spot_check_rate=rate, **ATTEST, **kw)
        wrongly = sum(
            h.refusals_overruled for h in result.hospitals.values()
            if h.policy.name not in ("strategic", "colluding"))
        assert wrongly == 0, f"false accusation at spot rate {rate}, arm {kw}"


def test_spot_checks_cost_an_honest_network_nothing():
    _, plain = _run()
    _, checked = _run(spot_check_rate=0.30, **ATTEST)
    assert abs(checked.success_rate - plain.success_rate) < 0.03
