"""The escalation case-mix taken from real California transfers (2021)."""

import pytest

from mahros.calibration.case_mix import load, observed_weights
from mahros.sim.scenario import (
    _PROFILE_WEIGHTS,
    PROFILE_KEYS,
    SCENARIOS,
    profile_weights,
)


def test_vendored_data_is_the_published_total():
    data = load()
    assert data["transfers_total"] == 37_439
    assert data["source"]["coverage"].startswith("2021-01-01")


def test_observed_weights_sum_to_one_and_cover_known_profiles():
    w = observed_weights()
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w) <= set(PROFILE_KEYS)


def test_children_share_comes_from_the_age_breakdown():
    data = load()
    assert observed_weights()["icu_paediatric"] == pytest.approx(
        data["by_age_overall"]["0 to 18"] / data["transfers_total"])


def test_cardiac_is_a_quarter_of_real_transfers():
    w = observed_weights()
    assert 0.20 < w["cath_cardiac"] + w["icu_cardiac"] < 0.30


def test_real_scenarios_use_the_observed_mix_and_synthetic_the_tuned_one():
    assert SCENARIOS["houston_surge"].case_mix == "california_2021"
    assert SCENARIOS["baseline"].case_mix == "tuned"
    assert profile_weights("tuned") is _PROFILE_WEIGHTS


def test_unknown_mix_is_refused():
    with pytest.raises(ValueError):
        profile_weights("guess")
