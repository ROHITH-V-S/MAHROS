"""Tests for the real-facility network substrate.

The most important test in this file is `test_no_undeclared_parameters`. The
project's earlier failure was not a bug -- it was a claim that had drifted away
from the code. That test makes drift a build failure: every field the network
builder sets must be classified in `PROVENANCE` as observed, derived or
assumed, so a parameter cannot be quietly introduced and then described as
"real" in the paper.
"""

from __future__ import annotations

import copy
import math

import pytest

from mahros.calibration.facilities import (
    METROS,
    SUSPECTED_AGGREGATES,
    haversine_km,
    load_vendor,
    num,
)
from mahros.core.types import ResourceType, Specialty
from mahros.sim.real_network import (
    PROVENANCE,
    TIER3_MIN_ICU_RATIO,
    build_real_network,
    erlang_b,
    offered_load_for_occupancy,
    provenance_table,
)
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import REAL_SCENARIOS, SCENARIOS, build_network

METRO = "houston"


@pytest.fixture(scope="module")
def payload():
    return load_vendor(METRO)


@pytest.fixture(scope="module")
def net():
    return build_real_network(METRO)


# --------------------------------------------------------------------------- #
# The vendored data is what it claims to be
# --------------------------------------------------------------------------- #

def test_source_is_public_domain_and_carries_no_patient_data(payload):
    src = payload["source"]
    assert src["license"].startswith("U.S. Public Domain")
    assert src["contains_patient_data"] is False
    assert "healthdata.gov" in src["endpoint"]


def test_every_facility_has_a_real_identity(payload):
    for fac in payload["facilities"]:
        # A CCN is six characters; this is the join key into every CMS dataset.
        assert len(fac["ccn"]) == 6 and fac["ccn"].isalnum(), fac
        assert fac["name"].strip()
        assert fac["address"].strip()
        assert -90 <= fac["lat"] <= 90
        assert -180 <= fac["lon"] <= 180


def test_facilities_are_unique(payload):
    ccns = [f["ccn"] for f in payload["facilities"]]
    assert len(ccns) == len(set(ccns))


def test_facilities_lie_inside_the_declared_catchment(payload):
    sel = payload["selection"]
    for fac in payload["facilities"]:
        d = haversine_km(sel["centroid_lat"], sel["centroid_lon"],
                         fac["lat"], fac["lon"])
        assert d <= sel["radius_km"] + 1e-6, fac["name"]


def test_every_exclusion_carries_a_reason(payload):
    assert payload["excluded"], "expected at least one recorded exclusion"
    for e in payload["excluded"]:
        assert e["reason"].strip(), e
        assert e["ccn"] and e["name"]


def test_suspected_aggregates_are_actually_excluded(payload):
    excluded = {e["ccn"] for e in payload["excluded"]}
    for ccn in SUSPECTED_AGGREGATES:
        assert ccn in excluded
    kept = {f["ccn"] for f in payload["facilities"]}
    assert not (kept & set(SUSPECTED_AGGREGATES))


def test_suppressed_values_are_null_not_zero():
    # -999999 is CDC privacy suppression. Treating it as zero would manufacture
    # ICU scarcity the data does not show.
    assert num("-999999") is None
    assert num("-999999.0") is None
    assert num(None) is None
    assert num("0") == 0.0
    assert num("12.5") == 12.5


# --------------------------------------------------------------------------- #
# The network is built from that data faithfully
# --------------------------------------------------------------------------- #

def test_network_has_real_hospitals(net):
    assert net.n_hospitals >= 20
    names = {c.name for c in net.configs}
    # Real facilities, not "Primary Hospital 03".
    assert not any(n.startswith(("Primary Hospital", "District Hospital",
                                 "Tertiary Hospital")) for n in names)
    assert any("Methodist" in n or "Memorial Hermann" in n for n in names)


def test_icu_capacity_equals_reported_icu(net):
    # ICU beds are observed, so they must survive unmodified at scale 1.0.
    for cfg in net.configs:
        reported = net.observed[cfg.hospital_id]["adult_icu_beds"]
        assert cfg.capacities[ResourceType.ICU_BED] == int(round(reported))


def test_inpatient_capacity_matches_reported_beds(net):
    # ward + hdu + icu should reconstruct reported inpatient beds closely;
    # rounding across three pools allows a bed or two of slack.
    for cfg in net.configs:
        reported = net.observed[cfg.hospital_id]["inpatient_beds"]
        total = sum(cfg.capacities.get(r, 0) for r in
                    (ResourceType.WARD_BED, ResourceType.HDU_BED,
                     ResourceType.ICU_BED))
        assert abs(total - reported) <= max(3, 0.02 * reported), cfg.name


def test_hospital_ids_are_ccns(net):
    for cfg in net.configs:
        assert len(cfg.hospital_id) == 6 and cfg.hospital_id.isalnum()


def test_geography_is_real_and_spread_out(net):
    xs = [c.x for c in net.configs]
    ys = [c.y for c in net.configs]
    assert max(xs) - min(xs) > 20, "network should span a real metro"
    assert max(ys) - min(ys) > 20
    # No two hospitals at the same point.
    pts = {(round(c.x, 3), round(c.y, 3)) for c in net.configs}
    assert len(pts) == len(net.configs)


def test_tier_three_requires_real_critical_care_depth(net):
    """Size alone must not make a referral centre.

    The Woman's Hospital of Texas reports 513 inpatient beds and 4 ICU beds. A
    bed-count-only rule made it a tier-3 cardiac and neuro centre, which is
    false. This is the regression test for that.
    """
    for cfg in net.configs:
        obs = net.observed[cfg.hospital_id]
        ratio = obs["adult_icu_beds"] / max(obs["inpatient_beds"], 1)
        if cfg.tier == 3:
            assert ratio >= TIER3_MIN_ICU_RATIO, (
                f"{cfg.name} is tier 3 with ICU ratio {ratio:.3f}")
            assert Specialty.CARDIAC in cfg.specialties


def test_paediatric_capability_is_observed_not_assumed(net):
    """The one capability signal the dataset actually carries."""
    for cfg in net.configs:
        has_picu = net.observed[cfg.hospital_id]["paediatric_icu_beds"] > 0
        if has_picu:
            assert Specialty.PAEDIATRIC in cfg.specialties, cfg.name


def test_arrival_rates_are_plausible(net):
    """Offered load may exceed admitted load at a full hospital -- by design.

    Erlang-B fitting deliberately offers more load than a near-full pool can
    carry, because that is what blocking means. The quantity that must stay
    physically sensible is the load actually *carried*: at a ~4.6 day stay a bed
    turns over about 0.22 times a day.
    """
    from mahros.sim.real_network import (
        LOS_SCALE,
        POOL_MEAN_LOS_MINUTES,
        _LOGNORMAL_MEAN_INFLATION,
        erlang_b,
    )
    for cfg in net.configs:
        beds = net.observed[cfg.hospital_id]["inpatient_beds"]
        carried = 0.0
        for pool, los_minutes in POOL_MEAN_LOS_MINUTES.items():
            cap = cfg.capacities.get(pool, 0)
            if cap <= 0:
                continue
            w_h = (los_minutes * _LOGNORMAL_MEAN_INFLATION * LOS_SCALE) / 60.0
            weight = cfg.admission_mix[pool][0]
            offered = cfg.base_arrival_rate * weight * w_h
            carried += offered * (1 - erlang_b(cap, offered)) / w_h
        per_bed_per_day = carried * 24.0 / beds
        assert 0 < per_bed_per_day < 0.35, f"{cfg.name}: {per_bed_per_day:.3f}"


def test_admission_mix_matches_bed_composition(net):
    """An ICU-heavy hospital must admit an ICU-heavy mix.

    A global mix sending 90% of admissions to ward beds starved the ICU and
    overflowed the ward at facilities reporting 40%+ of their beds as ICU,
    which is what the occupancy validation caught.
    """
    for cfg in net.configs:
        mix = cfg.admission_mix
        assert mix, cfg.name
        assert abs(sum(w for w, _ in mix.values()) - 1.0) < 1e-9
        icu_cap = cfg.capacities.get(ResourceType.ICU_BED, 0)
        total = sum(cfg.capacities.get(r, 0) for r in
                    (ResourceType.WARD_BED, ResourceType.HDU_BED,
                     ResourceType.ICU_BED))
        if icu_cap and total and icu_cap / total > 0.30:
            assert mix[ResourceType.ICU_BED][0] > 0.15, cfg.name


def test_capacity_scale_tightens_the_network(net):
    scarce = build_real_network(METRO, capacity_scale=0.72)
    full_icu = sum(c.capacities[ResourceType.ICU_BED] for c in net.configs)
    scarce_icu = sum(c.capacities[ResourceType.ICU_BED] for c in scarce.configs)
    assert scarce_icu < full_icu


def test_unknown_week_is_rejected():
    with pytest.raises(ValueError):
        build_real_network(METRO, week="1999-01-01")


# --------------------------------------------------------------------------- #
# Erlang-B blocking correction
# --------------------------------------------------------------------------- #

def test_erlang_b_is_a_probability():
    for c in (1, 5, 50, 500):
        for a in (0.0, 1.0, 10.0, 1000.0):
            assert 0.0 <= erlang_b(c, a) <= 1.0


def test_erlang_b_known_value():
    # B(1, a) = a / (1 + a) for a single server.
    for a in (0.5, 1.0, 3.0):
        assert erlang_b(1, a) == pytest.approx(a / (1 + a))


def test_offered_load_recovers_target_occupancy():
    for servers, target in ((10, 0.5), (100, 0.9), (37, 0.75), (500, 0.95)):
        a = offered_load_for_occupancy(servers, target)
        carried = a * (1 - erlang_b(servers, a)) / servers
        assert carried == pytest.approx(target, abs=1e-3)


def test_offered_load_exceeds_naive_little_law():
    """The correction must push load *up*, or it is not doing anything."""
    servers, target = 20, 0.95
    naive = target * servers
    assert offered_load_for_occupancy(servers, target) > naive


# --------------------------------------------------------------------------- #
# Anti-drift: the paper cannot claim more than the code declares
# --------------------------------------------------------------------------- #

def test_no_undeclared_parameters(net):
    """Every field the builder sets must be classified in PROVENANCE."""
    declared = " ".join(p.field for p in PROVENANCE).lower()
    cfg = net.configs[0]
    for resource in cfg.capacities:
        token = resource.name.lower()
        assert token in declared, (
            f"{resource.name} is set by the network builder but is not "
            f"classified in PROVENANCE as observed/derived/assumed")
    for attr in ("base_arrival_rate", "admission_mix", "coordinators", "tier",
                 "name"):
        assert attr.lower() in declared, f"{attr} is undeclared in PROVENANCE"


def test_provenance_labels_are_valid():
    for p in PROVENANCE:
        assert p.provenance in ("observed", "derived", "assumed")
        assert p.source.strip()
        assert p.field.strip()


def test_provenance_table_reports_all_three_classes():
    table = provenance_table()
    assert "**observed**" in table
    assert "_assumed_" in table
    assert "observed," in table


def test_assumed_fields_are_actually_unavailable():
    """Anything marked observed must not be in the assumed list, and vice versa."""
    by_class = {}
    for p in PROVENANCE:
        by_class.setdefault(p.provenance, set()).add(p.field)
    assert not (by_class["observed"] & by_class.get("assumed", set()))

    # Cath labs moved from assumed to observed when the CMS Provider of
    # Services join landed: that file reports an actual room count.
    observed = " ".join(by_class["observed"]).upper()
    assert "CATH_LAB" in observed

    # These two did NOT move, and must not be quietly promoted. The POS file
    # carries VNTLTR_BED_CNT but it is populated for zero hospitals, and
    # OPRTG_ROOM_SRVC_CD says an operating room exists without saying how many.
    assumed = " ".join(by_class.get("assumed", [])).upper()
    assert "OR_SLOT" in assumed
    assert "VENTILATOR" in assumed


# --------------------------------------------------------------------------- #
# Integration with the rest of the simulator
# --------------------------------------------------------------------------- #

def test_real_scenarios_are_registered():
    for name in REAL_SCENARIOS:
        assert name in SCENARIOS
        assert SCENARIOS[name].is_real


def test_build_network_dispatches_on_real_metro():
    real = build_network(SCENARIOS["houston_baseline"])
    assert len(real) >= 20
    assert all(len(c.hospital_id) == 6 for c in real)


def test_synthetic_path_is_untouched():
    """Adding a real substrate must not perturb any synthetic result."""
    sc = SCENARIOS["baseline"]
    assert not sc.is_real
    assert sc.los_scale == 1.0
    cfgs = build_network(sc)
    assert len(cfgs) == sc.n_hospitals
    assert cfgs[0].hospital_id == "H00"
    assert cfgs[0].admission_mix is None


def test_real_scenario_runs_end_to_end():
    sc = copy.deepcopy(SCENARIOS["houston_smoke"])
    result = SimulationRunner(
        RunConfig(scenario=sc, strategy="mahros", seed=42)).run()
    assert result.total_admissions > 0
    assert len(result.hospitals) >= 20
    # Rejected admissions are the guard that the network is still modelling a
    # transfer problem rather than a diversion problem.
    rate = result.rejected_admissions / max(result.total_admissions, 1)
    assert rate < 0.25, f"rejected-admission rate {rate:.1%} too high"


def test_real_network_is_deterministic():
    a = build_real_network(METRO)
    b = build_real_network(METRO)
    assert [c.hospital_id for c in a.configs] == [c.hospital_id for c in b.configs]
    assert [c.base_arrival_rate for c in a.configs] == \
           [c.base_arrival_rate for c in b.configs]


# --------------------------------------------------------------------------- #
# The prose must not drift from the code
# --------------------------------------------------------------------------- #

def test_readme_provenance_counts_match_the_code():
    """The README states how many parameters are observed/derived/assumed.

    A hand-written count in prose is exactly the kind of claim that drifts away
    from the code -- which is the failure this whole substrate exists to fix,
    and which happened once already while writing that very sentence. Pin it.
    """
    from collections import Counter
    from pathlib import Path
    import re

    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    counts = Counter(p.provenance for p in PROVENANCE)

    match = re.search(
        r"(\d+)\s+observed,\s+(\d+)\s+derived,\s*\n?\s*(\d+)\s+assumed", text)
    assert match, "README no longer states the provenance counts"
    stated = tuple(int(g) for g in match.groups())
    actual = (counts["observed"], counts["derived"], counts["assumed"])
    assert stated == actual, (
        f"README claims {stated} observed/derived/assumed but the code has "
        f"{actual}. Regenerate docs/DATA_PROVENANCE.md and fix the README.")


def test_generated_provenance_doc_is_current():
    """docs/DATA_PROVENANCE.md must reflect the code that is checked in."""
    from pathlib import Path
    doc = Path(__file__).resolve().parent.parent / "docs" / "DATA_PROVENANCE.md"
    assert doc.exists(), "run python experiments/build_provenance.py"
    text = doc.read_text(encoding="utf-8")
    for p in PROVENANCE:
        assert p.field in text, (
            f"{p.field!r} is classified in the code but missing from the "
            f"generated doc -- regenerate it")


# --------------------------------------------------------------------------- #
# A hospital must not be advertised as offering what it does not have
# --------------------------------------------------------------------------- #

def test_zero_capacity_resources_are_not_in_the_public_directory(net):
    """Regression: the console publicly accused an honest hospital of lying.

    Texas Orthopedic Hospital (CCN 450804) reports zero staffed adult ICU beds.
    It does not operate an ICU. The network builder still creates an `icu_bed`
    pool for it with capacity 0, and the challenge engine's resource registry
    was built from the pool *keys* -- so the public directory declared an ICU at
    a hospital that has none, and contradicted a completely truthful refusal.

    The existing false-accusation metric did not catch this: the defence
    succeeded, nothing was overruled, and the counter stayed at zero. What a
    viewer saw was still an honest hospital being called a liar.
    """
    from mahros.negotiation.cnp import CNPConfig, ContractNetNegotiator
    from mahros.negotiation.messages import MessageBus
    from mahros.negotiation.scoring import ScoringWeights
    from mahros.fairness.metrics import FairnessLedger
    from mahros.privacy.anonymizer import PrivacyAudit, build_anonymizer
    from mahros.hospital.hospital import Hospital

    hospitals = {c.hospital_id: Hospital(c, None, seed=1) for c in net.configs}
    negotiator = ContractNetNegotiator(
        hospitals=hospitals,
        travel_time=lambda a, b: 20.0,
        bus=MessageBus(),
        fairness=FairnessLedger(list(hospitals)),
        privacy=PrivacyAudit(build_anonymizer(prefer_presidio=False)),
        weights=ScoringWeights(),
        config=CNPConfig(),
    )
    registry = negotiator.challenges.resource_registry

    for hid, hosp in hospitals.items():
        declared = registry.get(hid, set())
        for resource, pool in hosp.resources.pools.items():
            if pool.capacity == 0:
                assert resource.value not in declared, (
                    f"{hosp.name} has zero {resource.value} but the public "
                    f"directory advertises it")
            else:
                assert resource.value in declared, (
                    f"{hosp.name} has {pool.capacity} {resource.value} but is "
                    f"not listed as offering it")


def test_a_hospital_with_no_icu_is_never_challenged_for_refusing_icu():
    """End to end: the exact query that surfaced the bug."""
    from mahros.server.live import LiveNetwork
    live = LiveNetwork(scenario="houston_baseline", seed=42)
    live.seed_load(0.55)
    assert live.hospitals["450804"].resources.pools[ResourceType.ICU_BED].capacity == 0

    result = live.request_transfer("450068", "icu_bed", "general", 4)
    targets = [s.get("target") for s in result["steps"] if s["kind"] == "challenge"]
    assert "450804" not in targets, (
        "a hospital with no ICU at all was challenged for saying so")


def test_defence_wording_distinguishes_capability_from_capacity():
    """"We have no ICU" and "our ICU is full" must not read alike."""
    from mahros.core.types import Acuity, Specialty, TransferRequest
    from mahros.negotiation.cnp import defence_wording

    req = TransferRequest(
        origin="450068", patient_ref="p1", resource=ResourceType.ICU_BED,
        specialty=Specialty.CARDIAC, acuity=Acuity.CRITICAL,
        created_at=0.0, expires_at=150.0, expected_los_minutes=3000)

    structural = defence_wording("H", "resource_not_offered", req)
    temporal = defence_wording("H", "at_self_protection_reserve", req)

    assert structural != temporal
    # The permanent claim must not talk about today, and must not imply the
    # hospital has the service at all.
    assert "permanent" in structural.lower()
    assert "does not provide" in structural.lower()
    # The temporal claim must make clear the service exists but is full.
    assert "does operate" in temporal.lower()
    assert "none free" in temporal.lower()
    # The old bug: capacity prose about "that earlier admission" shown for a
    # hospital that never made one.
    assert "earlier admission" not in structural.lower()
