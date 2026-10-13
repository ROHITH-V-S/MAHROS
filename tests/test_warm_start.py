"""Warm start: real hospitals begin the run at the occupancy they reported."""

import copy
import math
import random
import statistics as st

from mahros.core.types import ResourceType
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS
from mahros.sim.warm_start import ADMISSION_SIGMA, default_occupancy, residual_stay


def _runner(name: str, warm: bool, hours: int = 1) -> SimulationRunner:
    sc = copy.deepcopy(SCENARIOS[name])
    sc.horizon_hours = hours
    return SimulationRunner(RunConfig(scenario=sc, strategy="mahros", seed=42,
                                      warm_start=warm))


def test_real_network_starts_at_the_reported_icu_census():
    """Memorial Hermann TMC reported 119 of 168 staffed adult ICU beds used."""
    r = _runner("houston_baseline", warm=True)
    placed = r._warm_start()
    assert placed > 5000
    assert r.hospitals["450068"].resources.pool(ResourceType.ICU_BED).occupied == 119


def test_cold_start_is_still_available_and_empty():
    r = _runner("houston_baseline", warm=False)
    res = r.run()
    first = res.hospitals["450068"].resources.occupancy_samples(ResourceType.ICU_BED)
    assert first == [] or first[0] < 0.2


def test_synthetic_networks_are_unaffected():
    """Bit-identical: warm start applies to real networks only."""
    a = _runner("smoke", warm=True, hours=24).run()
    b = _runner("smoke", warm=False, hours=24).run()
    assert [q.status for q in a.requests] == [q.status for q in b.requests]
    assert a.total_admissions == b.total_admissions


def test_residual_stay_is_length_biased():
    """Mean time left = E[S^2] / 2E[S] = E[S] * exp(sigma^2) / 2 for a lognormal."""
    rng = random.Random(1)
    param = 2400.0
    mean_stay = param * math.exp(ADMISSION_SIGMA ** 2 / 2)
    expected = mean_stay * math.exp(ADMISSION_SIGMA ** 2) / 2
    got = st.fmean(residual_stay(rng, param, 1.0) for _ in range(40_000))
    assert abs(got - expected) / expected < 0.03


def test_default_occupancy_splits_inpatients_by_pool():
    caps = {ResourceType.WARD_BED: 90, ResourceType.HDU_BED: 10, ResourceType.ICU_BED: 20}
    occ = default_occupancy(caps, used=95.0, icu_used=15.0, beds=120.0)
    assert occ[ResourceType.ICU_BED][:2] == (15, "observed")
    assert occ[ResourceType.WARD_BED][0] + occ[ResourceType.HDU_BED][0] == 80
    assert occ[ResourceType.WARD_BED][1] == "derived"
